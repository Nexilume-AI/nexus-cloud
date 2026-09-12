"""Personal inference execution receipts, not wallets or commercial settlement."""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
import json
import re
import uuid
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.core.exceptions import ImproperlyConfigured
from rest_framework import exceptions
from apps.audit.services import write_audit_log
from apps.deployments.candidates import resolve_model_source_deployment
from apps.gateway.models import GatewayRequestLog
from apps.gateway.provider_clients import ProviderClientError
from apps.tenancy.models import Tenant
from .models import PersonalGatewayRequest, PersonalGatewayAttempt
from .resource_catalog import PersonalResourceCatalog
from .services import installation_context


@dataclass(frozen=True)
class RequestHandle:
    id: uuid.UUID


@dataclass(frozen=True)
class AttemptHandle:
    id: uuid.UUID
    request_id: uuid.UUID


def context(request, tenant):
    if getattr(request, "api_key", None) is not None:
        raise exceptions.PermissionDenied("Use personal owner credentials.")
    from .router_credentials import current_credential
    current_credential(request)
    return PersonalResourceCatalog()._context(request, tenant)


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 1024 * 1024:
        raise exceptions.ValidationError("Gateway request exceeds the personal request-size limit.")
    return salted_hmac("nexus.personal.gateway.request.v1", encoded, algorithm="sha256").hexdigest()


def operation_for_request(request):
    # The Provider sees normalized Chat; retain the actual client operation.
    # Never accept a client-supplied internal operation marker as authority.
    route = getattr(getattr(request, "resolver_match", None), "url_name", "")
    image_routes = {"personal-images-generate": "images.generate", "personal-images-edit": "images.edit", "personal-images-variation": "images.variation"}
    if route in image_routes:
        return image_routes[route]
    return "responses" if route in {"openai-compatible-responses", "openai-compatible-responses-slash"} else "chat.completions"


def request_id(request):
    value = getattr(request, "request_id", "") or request.headers.get("X-Request-ID", "") or "req_" + uuid.uuid4().hex
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", value) is None:
        raise exceptions.ValidationError("X-Request-ID must contain 1–64 letters, digits, dots, colons, underscores or hyphens.")
    request.request_id = value
    return value


def source_context(tenant, source):
    current = resolve_model_source_deployment(tenant=tenant, source=source)
    if current is None:
        raise exceptions.NotFound("Source is no longer available to this personal instance.")
    # Fail before sending rather than using cached credentials after a change.
    fields = ("provider_id", "provider_account_id", "provider_runtime_id", "runtime_model_offer_id",
              "canonical_model_id", "upstream_model_id", "endpoint", "model")
    if any(getattr(current, field) != getattr(source, field) for field in fields) or any(
            getattr(current.provider_account, field) != getattr(source.provider_account, field)
            for field in ("url", "encrypted_key")):
        raise exceptions.ValidationError("Source connection changed. Reload and start a new request.")
    return current


def _lease(request, handle, *, lock=False):
    if not isinstance(handle, RequestHandle):
        raise exceptions.NotFound("Gateway request context is unavailable.")
    query = PersonalGatewayRequest.objects.select_for_update() if lock else PersonalGatewayRequest.objects
    row = query.filter(pk=handle.id).first()
    if row is None:
        raise exceptions.NotFound("Gateway request context is unavailable.")
    user, tenant_id, project_id = context(request, row.tenant_id)
    if (str(row.actor_id) != str(user.pk) or str(row.project_id) != project_id or
            row.request_id != getattr(request, "request_id", "")):
        raise exceptions.NotFound("Gateway request context is unavailable.")
    if row.state not in {"pending", "dispatched"} or row.expires_at <= timezone.now():
        raise exceptions.ValidationError("Gateway request has ended or expired; do not replay it.")
    return row


def expire_requests(*, tenant_id):
    now = timezone.now()
    # A replay fence remains forever. Expiry releases operational activity,
    # never authorization to dispatch this request again.
    ids = list(PersonalGatewayRequest.objects.filter(tenant_id=tenant_id,
        state__in=["pending", "dispatched"], expires_at__lte=now).values_list("pk", flat=True)[:200])
    count = 0
    for pk in ids:
        with transaction.atomic():
            row = PersonalGatewayRequest.objects.select_for_update().get(pk=pk)
            if row.state not in {"pending", "dispatched"} or row.expires_at > now:
                continue
            row.attempts.filter(state__in=["dispatched", "responded"]).update(
                state="interrupted", finished_at=now, error_code="GATEWAY_REQUEST_EXPIRED")
            row.state, row.finished_at = "interrupted", now
            row.save(update_fields=["state", "finished_at"])
            _interruption_log(row, "GATEWAY_REQUEST_EXPIRED")
            count += 1
    return count


def _interruption_log(row, code):
    attempt = row.attempts.order_by("-dispatched_at", "-pk").first()
    source_id = attempt.source_id if attempt else None
    if GatewayRequestLog.objects.filter(tenant_id=row.tenant_id, request_id=row.request_id,
            deployment_id=source_id, status="failed").exists():
        return
    from apps.deployments.models import Deployment
    source_id = Deployment.objects.filter(pk=source_id, tenant_id=row.tenant_id).values_list("pk", flat=True).first()
    GatewayRequestLog.objects.create(tenant_id=row.tenant_id, project_id=str(row.project_id), actor_id=row.actor_id,
        router_id=row.router_id, model=row.requested_model, request_id=row.request_id, deployment_id=source_id,
        status="failed", error_code=code, operation=row.operation)


@transaction.atomic
def reserve(*, request, tenant, deployments, provider_payload, amount_override=None, request_snapshot=None):
    from apps.gateway.services import GatewayRequestReplay, get_router_for_payload
    user, tenant_id, project_id = context(request, tenant)
    if amount_override not in (None, Decimal("0")):
        raise exceptions.ValidationError("Personal Gateway requests do not reserve money.")
    if not deployments:
        raise exceptions.NotFound("No Source is available.")
    for source in deployments:
        source_context(tenant, source)
    key = request_id(request)
    router = get_router_for_payload(tenant=tenant, payload=provider_payload)
    fingerprint = digest({"payload": provider_payload, "sources": [str(d.pk) for d in deployments],
        "request": getattr(request, "data", {}) if request_snapshot is None else request_snapshot,
        "path": getattr(request, "path", "")})
    Tenant.objects.select_for_update().get(pk=tenant_id)
    expire_requests(tenant_id=tenant_id)
    existing = PersonalGatewayRequest.objects.filter(tenant_id=tenant_id, request_id=key).first()
    if existing:
        raise GatewayRequestReplay("This request ID is already recorded. Check its result; use a new ID only for a deliberate new invocation.")
    timeout = max(1, int(getattr(settings, "NEXUS_PROVIDER_REQUEST_TIMEOUT_SECONDS", 60)))
    try:
        with transaction.atomic():
            row = PersonalGatewayRequest.objects.create(tenant_id=tenant_id, project_id=project_id,
                actor=user, request_id=key, request_digest=fingerprint,
                requested_model=provider_payload.get("model", ""), router=router, operation=operation_for_request(request),
                source_ids=[str(d.pk) for d in deployments],
                expires_at=timezone.now() + timedelta(seconds=max(900, timeout * len(deployments) + 60)))
    except IntegrityError:
        raise GatewayRequestReplay() from None
    handle = RequestHandle(row.pk)
    request._nexus_personal_gateway_request = handle
    return handle


@transaction.atomic
def prepare_attempt(*, request, deployment, provider_payload):
    row = _lease(request, getattr(request, "_nexus_personal_gateway_request", None), lock=True)
    if str(deployment.pk) not in row.source_ids:
        raise exceptions.NotFound("Source was not admitted for this request.")
    source_context(row.tenant_id, deployment)
    from .router_credentials import current_credential
    credential = current_credential(request)
    if credential is not None:
        type(credential).objects.filter(pk=credential.pk).update(last_used_at=timezone.now())
    try:
        with transaction.atomic():
            attempt = PersonalGatewayAttempt.objects.create(request=row, source_id=deployment.pk)
    except IntegrityError:
        raise exceptions.ValidationError("This Source attempt was already dispatched.") from None
    row.state = "dispatched"
    row.save(update_fields=["state"])
    handle = AttemptHandle(attempt.pk, row.pk)
    request._nexus_personal_gateway_attempt = handle
    request._nexus_personal_stream_done = False
    request._nexus_personal_stream_received = False
    return handle


def _attempt(handle, *, lock=False):
    if not isinstance(handle, AttemptHandle):
        raise exceptions.NotFound("Gateway attempt is unavailable.")
    query = PersonalGatewayAttempt.objects.select_for_update() if lock else PersonalGatewayAttempt.objects
    attempt = query.filter(pk=handle.id, request_id=handle.request_id).first()
    if attempt is None:
        raise exceptions.NotFound("Gateway attempt is unavailable.")
    return attempt


@transaction.atomic
def record_attempt_tokens(*, reservation, actual_tokens):
    attempt = _attempt(reservation, lock=True)
    if attempt.state != "dispatched" or type(actual_tokens) is not int or actual_tokens < 0:
        raise exceptions.ValidationError("Gateway attempt usage is invalid.")
    attempt.state, attempt.total_tokens = "responded", actual_tokens
    attempt.save(update_fields=["state", "total_tokens"])


@transaction.atomic
def release_attempt(*, reservation):
    if reservation is None:
        return
    attempt = _attempt(reservation, lock=True)
    if attempt.state in {"dispatched", "responded"}:
        attempt.state, attempt.finished_at = "interrupted", timezone.now()
        attempt.save(update_fields=["state", "finished_at"])


@transaction.atomic
def release(*, reservation):
    if reservation is None:
        return
    if not isinstance(reservation, RequestHandle):
        raise exceptions.NotFound("Gateway request is unavailable.")
    row = PersonalGatewayRequest.objects.select_for_update().get(pk=reservation.id)
    if row.state not in {"pending", "dispatched"}:
        return
    row.state = "interrupted" if row.attempts.filter(state="interrupted").exists() else "failed"
    row.finished_at = timezone.now()
    row.save(update_fields=["state", "finished_at"])
    _interruption_log(row, "GATEWAY_REQUEST_INTERRUPTED" if row.state == "interrupted" else "GATEWAY_REQUEST_ABORTED")


def _log_fields(request, tenant, router, deployment, model):
    from apps.gateway.services import consumer_source_id, selected_model_group_id, routing_trace_for_deployment
    user, tenant_id, project_id = context(request, tenant)
    return dict(tenant_id=tenant_id, project_id=project_id, actor=user, model=model, router=router,
        router_strategy=router.strategy if router else "", deployment=deployment,
        consumer_source_id=consumer_source_id(deployment), selected_model_group_id=selected_model_group_id(deployment),
        routing_trace=routing_trace_for_deployment(deployment), provider=deployment.provider.name if deployment else "",
        request_id=request.request_id, operation=operation_for_request(request))


@transaction.atomic
def write_failure_log(*, request, tenant, api_key, router, fallback_count, deployment, model, error_code, latency_ms, cost):
    row = _lease(request, getattr(request, "_nexus_personal_gateway_request", None), lock=True)
    if str(tenant.pk) != str(row.tenant_id) or api_key is not None or cost != 0:
        raise exceptions.NotFound("Gateway failure context is unavailable.")
    log = GatewayRequestLog.objects.create(**_log_fields(request, tenant, router, deployment, model),
        status="failed", error_code=error_code[:64], latency_ms=latency_ms, fallback_count=fallback_count)
    handle = getattr(request, "_nexus_personal_gateway_attempt", None)
    if isinstance(handle, AttemptHandle):
        attempt = _attempt(handle, lock=True)
        if attempt.request_id != row.pk or attempt.source_id != deployment.pk:
            raise exceptions.NotFound("Gateway attempt context is unavailable.")
        attempt.state = "interrupted" if error_code in {"PROVIDER_TIMEOUT", "PROVIDER_REQUEST_FAILED", "PROVIDER_STREAM_INCOMPLETE", "PROVIDER_STREAM_FAILED"} else "failed"
        attempt.error_code, attempt.finished_at = error_code[:64], timezone.now()
        attempt.save(update_fields=["state", "error_code", "finished_at"])
    return log


def record_failed_attempt(*, request, tenant, deployment, gateway_request_log):
    row = _lease(request, getattr(request, "_nexus_personal_gateway_request", None))
    receipt = GatewayRequestLog.objects.filter(pk=gateway_request_log.pk, tenant_id=row.tenant_id,
        actor_id=row.actor_id, request_id=row.request_id, deployment_id=deployment.pk, status="failed").first()
    if receipt is None:
        raise exceptions.NotFound("Gateway failure receipt is unavailable.")
    if receipt.error_code in {"PROVIDER_TIMEOUT", "PROVIDER_REQUEST_FAILED", "PROVIDER_STREAM_INCOMPLETE", "PROVIDER_STREAM_FAILED"}:
        from apps.gateway.services import ProviderUpstreamError
        raise ProviderUpstreamError("Upstream result is uncertain. It was not automatically replayed.", code=receipt.error_code)


@transaction.atomic
def finalize(*, request, tenant, api_key, router, fallback_count, deployment, requested_model, provider_response,
             cost, reservation, capacity_reservation=None):
    from apps.gateway.services import record_model_group_source_selection
    row = _lease(request, reservation, lock=True)
    attempt = _attempt(capacity_reservation, lock=True)
    if (str(tenant.pk) != str(row.tenant_id) or api_key is not None or cost != 0 or attempt.request_id != row.pk
            or attempt.source_id != deployment.pk or attempt.state != "responded"):
        raise exceptions.NotFound("Gateway completion context is unavailable.")
    log = GatewayRequestLog.objects.create(**_log_fields(request, tenant, router, deployment, requested_model),
        status="success", image_count=provider_response.image_count,
        request_tokens=provider_response.request_tokens, response_tokens=provider_response.response_tokens,
        total_tokens=provider_response.total_tokens, fallback_count=fallback_count, latency_ms=provider_response.latency_ms)
    row.state, row.finished_at, row.result_log = "completed", timezone.now(), log
    row.save(update_fields=["state", "finished_at", "result_log"])
    attempt.state, attempt.finished_at = "completed", row.finished_at
    attempt.save(update_fields=["state", "finished_at"])
    record_model_group_source_selection(deployment=deployment)
    write_audit_log(request=request, actor=request.user, tenant=tenant, action="gateway.invoke",
        resource_type="deployment", resource_id=deployment.pk,
        after={"gateway_log_id": str(log.pk), "request_id": row.request_id,
               "total_tokens": log.total_tokens, "fallback_count": fallback_count})
    return log


def observe_stream(*, request, event):
    from .router_credentials import current_credential
    current_credential(request)
    if isinstance(event.raw, dict):
        if event.raw.get("error"):
            raise ProviderClientError("Upstream reported a streaming failure.", error_code="PROVIDER_STREAM_FAILED")
        if isinstance(event.raw.get("choices"), list) and event.raw["choices"]:
            request._nexus_personal_stream_received = True
    if event.done:
        request._nexus_personal_stream_done = True


def complete_stream(*, request):
    if not (getattr(request, "_nexus_personal_stream_done", False) and getattr(request, "_nexus_personal_stream_received", False)):
        raise ProviderClientError("Upstream stream ended without its completion marker.", error_code="PROVIDER_STREAM_INCOMPLETE")


class PersonalGatewayLifecycle:
    reserve = staticmethod(reserve)
    release = staticmethod(release)
    finalize = staticmethod(finalize)

    def calculate(self, *, deployment, **kwargs):
        # Personal has no platform charge. This is not a paid settlement result.
        return Decimal("0")

    def estimate(self, **kwargs):
        return Decimal("0")

    def record(self, **kwargs):
        raise exceptions.ValidationError("Complete the admitted Gateway request instead of writing an unbound receipt.")

    def image_price(self, **kwargs):
        from .image_execution import image_price
        return image_price(**kwargs)

    def lock_image_account(self, **kwargs):
        from .image_execution import lock_image_account
        return lock_image_account(**kwargs)

    def expire_images(self, **kwargs):
        from .image_execution import expire_images
        return expire_images(**kwargs)
