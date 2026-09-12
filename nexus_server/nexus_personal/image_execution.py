"""Owner-authenticated Images API with durable receipts, never wallet settlement."""
import base64
import hashlib
import hmac
import json
import re
import time
from datetime import timedelta
from decimal import Decimal
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.utils import timezone
from rest_framework import exceptions
from apps.common.request_context import get_tenant_from_request
from apps.datasets.media_services import upload_media_asset, signed_media_url, media_storage_root
from apps.datasets.models import MediaAsset
from apps.gateway.image_media import decode_image_base64, fetch_public_image, validate_image
from apps.gateway.image_services import digest, input_files, asset_bytes, ImageOperationError, ImageInvocationFailed
from apps.gateway.models import GatewayImageOperation, GatewayRequestLog
from apps.gateway.provider_adapters import ProviderClientError, ProviderResponse, adapter_for_provider
from apps.tenancy.models import Tenant
from . import gateway_lifecycle as lifecycle
from .models import PersonalGatewayRequest

OPERATIONS = {"images.generate", "images.edit", "images.variation"}


def owner_context(request):
    if getattr(request, "_nexus_personal_router_credential_id", None) is not None:
        raise exceptions.PermissionDenied("Image and media access require owner authentication; this Router credential does not grant them.")
    tenant = get_tenant_from_request(request)
    user, tenant_id, project_id = lifecycle.context(request, tenant)
    return tenant, user, tenant_id, project_id


def image_price(*, deployment, operation, payload):
    if operation not in OPERATIONS:
        raise exceptions.ValidationError("Unsupported image operation.")
    lifecycle.source_context(deployment.tenant_id, deployment)
    # No Nexus platform charge in the personal edition. This does not mean
    # the configured upstream vendor supplies images for free.
    return Decimal("0")


def lock_image_account(*, tenant, create=False):
    if not connection.in_atomic_block:
        raise RuntimeError("Image admission requires a transaction.")
    return Tenant.objects.select_for_update().get(pk=getattr(tenant, "pk", tenant))


def image_response(*, request, record, response_format):
    _, user, tenant_id, _ = owner_context(request)
    current = GatewayImageOperation.objects.filter(pk=record.pk, tenant_id=tenant_id, principal=f"user:{user.pk}").first()
    if current is None:
        raise exceptions.NotFound("Image operation is unavailable.")
    if current.state != "succeeded":
        raise ImageOperationError("Image request is pending, failed or interrupted; it will not be regenerated with this key.")
    data = []
    for asset_id in current.asset_ids:
        if response_format == "b64_json":
            content, _ = asset_bytes(request, asset_id)
            data.append({"b64_json": base64.b64encode(content).decode()})
        else:
            data.append({"url": signed_media_url(request=request, asset_id=asset_id)})
    return {"created": int(current.created_at.timestamp()), "data": data, "usage": {"images": len(data)}}


def discard_outputs(assets):
    root = media_storage_root().resolve()
    complete = True
    for asset in assets:
        asset.delete()  # Make access fail even if physical cleanup needs retry.
        path = root / asset.storage_path
        # Only unlink this request's exact regular output, never follow a link.
        try:
            if path.is_symlink() or root not in path.resolve().parents or (path.exists() and not path.is_file()):
                complete = False
            elif path.is_file():
                path.unlink()
        except OSError:
            complete = False
    return complete


def output_names(record):
    return [f"image-{record.pk}-{index}.{extension}" for index in range(4) for extension in ("png", "jpeg", "webp")]


def images(*, request, payload, operation):
    from apps.gateway.services import get_router_for_payload, resolve_deployment_candidates
    tenant, user, tenant_id, project_id = owner_context(request)
    if operation not in OPERATIONS:
        raise exceptions.ValidationError("Unsupported image operation.")
    key = request.headers.get("Idempotency-Key", "")
    if not key or len(key.encode("utf-8")) > 128:
        raise exceptions.ValidationError("An Idempotency-Key of 1–128 bytes is required.")
    payload = dict(payload, _nexus_operation=operation)
    if payload.get("router_id"):
        payload["router_id"] = str(payload["router_id"])
    files = input_files(request, operation)
    fingerprint = digest(json.dumps({"payload": payload, "project": project_id,
        "files": [(name, hashlib.sha256(content).hexdigest(), mime) for name, content, mime in files]},
        sort_keys=True, default=str).encode())
    lookup = dict(tenant_id=tenant_id, principal=f"user:{user.pk}", key_digest=digest(key.encode()))
    def replay(record):
        if not hmac.compare_digest(record.request_digest, fingerprint):
            raise ImageOperationError("Idempotency-Key was already used with different arguments.")
        return image_response(request=request, record=record, response_format=payload["response_format"])
    existing = GatewayImageOperation.objects.filter(**lookup).first()
    if existing:
        return replay(existing)
    deployments = resolve_deployment_candidates(request=request, tenant=tenant, payload=payload)
    deployment = deployments[0]
    router = get_router_for_payload(tenant=tenant, payload=payload)
    with transaction.atomic():
        lock_image_account(tenant=tenant)
        existing = GatewayImageOperation.objects.filter(**lookup).first()
        if existing:
            return replay(existing)
        record = GatewayImageOperation.objects.create(**lookup, request_digest=fingerprint, operation=operation)
        request.request_id = str(record.pk)
        # The image fingerprint includes validated parameters and actual file
        # hashes. UploadedFile objects must never enter JSON serialization.
        reservation = lifecycle.reserve(request=request, tenant=tenant, deployments=[deployment], provider_payload=payload,
            request_snapshot={"image_request_digest": fingerprint})
    attempt = None
    created = []
    started = time.monotonic()
    try:
        attempt = lifecycle.prepare_attempt(request=request, deployment=deployment, provider_payload=payload)
        raw = adapter_for_provider(deployment.provider.name).images(deployment=deployment, operation=operation, payload=payload, files=files)
        output = raw.get("data")
        if not isinstance(output, list) or not 1 <= len(output) <= payload["n"]:
            raise exceptions.ValidationError("Invalid image output count.")
        decoded = []
        for item in output:
            if not isinstance(item, dict):
                raise exceptions.ValidationError("Invalid image output.")
            content = decode_image_base64(item["b64_json"]) if item.get("b64_json") else fetch_public_image(item.get("url", ""))
            mime, _ = validate_image(content)
            decoded.append((content, mime))
        for index, (content, mime) in enumerate(decoded):
            created.append(upload_media_asset(request=request,
                uploaded_file=SimpleUploadedFile(f"image-{record.pk}-{index}.{mime.split('/')[1]}", content, content_type=mime),
                purpose=MediaAsset.PURPOSE_CHAT_INPUT, expires_at=timezone.now() + timedelta(hours=24)))
        response = ProviderResponse(raw={}, request_tokens=0, response_tokens=0, total_tokens=0,
            model=deployment.model, latency_ms=max(1, int((time.monotonic() - started) * 1000)),
            operation=operation, image_count=len(created))
        with transaction.atomic():
            lock_image_account(tenant=tenant)
            current = GatewayImageOperation.objects.select_for_update().get(pk=record.pk)
            if current.state != "pending":
                raise ImageOperationError("IMAGE_RESULT_UNCERTAIN")
            lifecycle.record_attempt_tokens(reservation=attempt, actual_tokens=0)
            log = lifecycle.finalize(request=request, tenant=tenant, api_key=None, router=router, fallback_count=0,
                deployment=deployment, requested_model=payload["model"], provider_response=response, cost=Decimal("0"),
                reservation=reservation, capacity_reservation=attempt)
            current.state, current.asset_ids, current.gateway_log = "succeeded", [str(asset.pk) for asset in created], log
            current.save(update_fields=["state", "asset_ids", "gateway_log", "updated_at"])
        reservation, attempt = None, None
    except Exception as exc:
        code = str(exc.error_code) if isinstance(exc, ProviderClientError) else "IMAGE_OPERATION_FAILED"
        if re.fullmatch(r"[A-Z0-9_]{1,64}", code) is None:
            code = "IMAGE_OPERATION_FAILED"
        GatewayImageOperation.objects.filter(pk=record.pk).update(state="failed", error_code=code)
        # Also locate an output whose upload audit failed before the helper
        # returned it. Names are unique to this exact admitted operation.
        created = list(MediaAsset.objects.filter(tenant_id=tenant_id, owner=user, file_name__in=output_names(record)))
        if not discard_outputs(created):
            GatewayImageOperation.objects.filter(pk=record.pk).update(error_code="IMAGE_CLEANUP_PENDING")
        GatewayRequestLog.objects.create(tenant_id=tenant_id, project_id=project_id, actor=user, router=router,
            deployment=deployment, model=payload["model"], operation=operation, status="failed",
            error_code=code, request_id=str(record.pk), latency_ms=max(1, int((time.monotonic() - started) * 1000)))
        raise ImageInvocationFailed("Image request failed or was interrupted. This key will not trigger another generation.") from None
    finally:
        if attempt is not None:
            lifecycle.release_attempt(reservation=attempt)
        if reservation is not None:
            lifecycle.release(reservation=reservation)
    return image_response(request=request, record=record, response_format=payload["response_format"])


def expire_images():
    cutoff = timezone.now() - timedelta(minutes=15)
    ids = list(GatewayImageOperation.objects.filter(state="pending", created_at__lte=cutoff).values_list("pk", "tenant_id")[:200])
    expired = 0
    for pk, tenant_id in ids:
        with transaction.atomic():
            lock_image_account(tenant=tenant_id)
            row = GatewayImageOperation.objects.select_for_update().get(pk=pk)
            if row.state != "pending":
                continue
            lease = PersonalGatewayRequest.objects.filter(request_id=str(pk), tenant_id=tenant_id).first()
            if lease is not None and lease.state in {"pending", "dispatched"}:
                if lease.expires_at > timezone.now():
                    continue
                lifecycle.expire_requests(tenant_id=tenant_id)
            assets = list(MediaAsset.objects.filter(tenant_id=tenant_id, file_name__in=output_names(row)))
            cleaned = discard_outputs(assets)
            row.state = "failed"
            row.error_code = "IMAGE_RESULT_UNCERTAIN" if cleaned else "IMAGE_CLEANUP_PENDING"
            row.save(update_fields=["state", "error_code", "updated_at"])
            expired += 1
    # Deleted metadata remains as a precise retry target if an OS file lock
    # prevented removal. Never enumerate/delete unrelated workspace contents.
    cleanup_ids = list(GatewayImageOperation.objects.filter(state="failed", error_code="IMAGE_CLEANUP_PENDING")
        .values_list("pk", "tenant_id")[:200])
    for pk, tenant_id in cleanup_ids:
        with transaction.atomic():
            lock_image_account(tenant=tenant_id)
            row = GatewayImageOperation.objects.select_for_update().get(pk=pk)
            if row.state != "failed" or row.error_code != "IMAGE_CLEANUP_PENDING":
                continue
            assets = list(MediaAsset.objects.filter(tenant_id=tenant_id, file_name__in=output_names(row)))
            if discard_outputs(assets):
                row.error_code = "IMAGE_OPERATION_FAILED"
                row.save(update_fields=["error_code", "updated_at"])
    return {"expired": expired}
