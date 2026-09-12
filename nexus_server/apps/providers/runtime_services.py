from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import RemoteDisconnected, IncompleteRead
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework import exceptions, status

from apps.audit.services import log_audit
from apps.common.resource_limits import enforce_tenant_resource_quota
from apps.common.crypto import decrypt_secret, encrypt_secret
from apps.common.models import SoftDeleteModel
from apps.common.project_scope import scope_queryset_to_current_project
from apps.deployments.models import CanonicalModel, Deployment, ModelGroup, ModelGroupDeployment
from apps.common.authorization import has_nexus_permission
from apps.tenancy.models import Project, Tenant
from apps.common.request_context import get_tenant_from_request

from .commerce import invoke_provider_commerce
from .credentials import issue_provider_export_key
from .runtime_integration import runtime_integration

from .models import (
    Provider,
    ProviderAccount,
    ProviderRuntimeAccount,
    ProviderRuntimeModelOffer,
)
from .health_services import record_runtime_health_check
from .runtime_runner import cliproxyapi_management_key, cliproxyapi_oauth_provider, get_provider_runtime_runner
from .services import default_model_for_provider, get_or_create_provider


_OAUTH_RELAY_LOCK = threading.Lock()
_OAUTH_RELAY_SERVERS: dict[int, ThreadingHTTPServer] = {}
_OAUTH_RELAY_STATES: dict[str, tuple[str, float]] = {}


class ProviderRuntimeError(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Provider runtime request failed."
    default_code = "PROVIDER_RUNTIME_ERROR"


class ProviderRuntimeNotFound(ProviderRuntimeError):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Provider runtime not found."
    default_code = "NOT_FOUND"


class ProviderRuntimeLoginRequired(ProviderRuntimeError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Provider runtime login is required."
    default_code = "PROVIDER_RUNTIME_LOGIN_REQUIRED"


class ProviderProbeConflict(ProviderRuntimeError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Provider configuration changed while model discovery was running. Refresh and retry."
    default_code = "PROVIDER_PROBE_CONFLICT"


class ProviderRuntimeBusy(ProviderRuntimeError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Provider runtime lifecycle operation is already in progress."
    default_code = "PROVIDER_RUNTIME_BUSY"


class ProviderCapacityUnavailable(ProviderRuntimeError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Provider capacity is unavailable for this request."
    default_code = "PROVIDER_CAPACITY_UNAVAILABLE"


class ManualModelDiscoveryDisabled(ProviderRuntimeError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Manual model discovery is disabled. Refresh the Provider Runtime /models catalog instead."
    default_code = "MANUAL_MODEL_DISCOVERY_DISABLED"


def list_runtime_model_offers(*, request, runtime_id: str):
    runtime = get_provider_runtime(request=request, runtime_id=runtime_id)
    require_provider_runtime_admin(request=request, tenant=runtime.tenant)
    return runtime.model_offers.exclude(status=SoftDeleteModel.STATUS_DELETED).select_related("canonical_model").order_by(
        "upstream_model_id"
    )


def refresh_provider_runtime_models(*, request, runtime_id: str):
    runtime = get_provider_runtime(request=request, runtime_id=runtime_id)
    require_provider_runtime_admin(request=request, tenant=runtime.tenant)
    runtime = _prepare_manual_catalog_refresh(runtime)
    refresh_runtime_model_offers(runtime=runtime, actor=request.user, strict=True)
    log_runtime_write(request=request, runtime=runtime, action="providers.runtime.models.refresh")
    return list_runtime_model_offers(request=request, runtime_id=runtime_id)


def refresh_provider_runtime_model_offer(*, request, runtime_id: str, offer_id: str):
    runtime = get_provider_runtime(request=request, runtime_id=runtime_id)
    require_provider_runtime_admin(request=request, tenant=runtime.tenant)
    runtime = _prepare_manual_catalog_refresh(runtime)
    if runtime.status != ProviderRuntimeAccount.STATUS_ACTIVE:
        raise ProviderRuntimeError("Start the Provider Runtime before refreshing a Model Offer.")
    offer = (
        runtime.model_offers.all()
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .filter(id=offer_id)
        .first()
    )
    if offer is None:
        raise ProviderRuntimeNotFound("Runtime Model Offer not found.")

    runtime_version = _model_probe_runtime_version(runtime)
    offer_version = (offer.updated_at, offer.status, offer.upstream_model_id, offer.canonical_model_id)
    # Network I/O must not retain an Offer row lock. A short transaction below
    # fences late results against stop/delete/edit/another completed probe.
    now = timezone.now()
    advertised = offer.upstream_model_id in discover_runtime_model_ids(runtime=runtime)
    declared_contract = getattr(runtime, "_discovered_model_contracts", {}).get(offer.upstream_model_id)
    probe = probe_runtime_model(runtime=runtime, upstream_model_id=offer.upstream_model_id) if advertised else None
    with transaction.atomic():
        current_runtime = (
            ProviderRuntimeAccount.objects.select_for_update(of=("self",))
            .select_related("provider_account", "source_provider_account")
            .filter(pk=runtime.pk, tenant_id=runtime.tenant_id).first()
        )
        offer = runtime.model_offers.select_for_update().filter(pk=offer.pk).first()
        if (
            current_runtime is None or offer is None
            or _model_probe_runtime_version(current_runtime) != runtime_version
            or (offer.updated_at, offer.status, offer.upstream_model_id, offer.canonical_model_id) != offer_version
        ):
            raise ProviderProbeConflict()
        if declared_contract is not None:
            offer.metadata = {**(offer.metadata or {}), "model_contract": declared_contract}
        if advertised:
            if not offer.canonical_model_id:
                offer.canonical_model = _canonical_model_for_upstream(offer.upstream_model_id)
            if offer.status == ProviderRuntimeModelOffer.STATUS_UNAVAILABLE:
                offer.status = (
                    ProviderRuntimeModelOffer.STATUS_CONFIRMED
                    if offer.confirmed_at
                    else ProviderRuntimeModelOffer.STATUS_DETECTED
                )
            _apply_model_probe(offer, probe, now)
            offer.last_discovered_at = now
        else:
            offer.status = ProviderRuntimeModelOffer.STATUS_UNAVAILABLE
            offer.health_status = ProviderRuntimeModelOffer.HEALTH_UNHEALTHY
            offer.health_reason = "No longer advertised by the Provider Runtime model catalog."
        offer.last_health_check_at = now
        offer.save(update_fields=[
            "canonical_model", "status", "health_status", "health_reason", "last_discovered_at",
            "last_health_check_at", "updated_at", "metadata",
        ])
        log_runtime_write(
            request=request,
            runtime=current_runtime,
            action="providers.runtime.model_offer.refresh",
            metadata={"model_offer_id": str(offer.id)},
        )
    return offer


def _model_probe_runtime_version(runtime):
    """Return only configuration that can change model discovery results.

    Health and quota reconciliation deliberately update account ``updated_at``
    timestamps. Treating those timestamps as a configuration version made a
    harmless background health check invalidate an in-flight catalog probe.
    Conversely, bulk/configuration writes can change a material field without
    touching ``updated_at``. Fence on the actual inputs used by discovery and
    model probing instead.
    """

    source = runtime.source_provider_account if runtime.source_provider_account_id else None
    return (
        runtime.status,
        runtime.runtime_type,
        runtime.container_id,
        runtime.internal_api_url,
        runtime.encrypted_proxy_api_key,
        runtime.provider_account_id, runtime.source_provider_account_id,
        source.status if source is not None else None,
        source.url if source is not None else None,
        source.encrypted_key if source is not None else None,
    )


def prepare_runtime_model_offer_for_use(
    *,
    offer: ProviderRuntimeModelOffer,
    actor,
    allow_degraded: bool,
) -> ProviderRuntimeModelOffer:
    """Validate a discovered Offer and persist the internal confirmation on first use."""

    if offer.status not in {
        ProviderRuntimeModelOffer.STATUS_DETECTED,
        ProviderRuntimeModelOffer.STATUS_CONFIRMED,
    }:
        raise ProviderRuntimeError("The Model Offer is not currently advertised by this Provider Runtime.")
    if not offer.canonical_model_id or offer.canonical_model.status != SoftDeleteModel.STATUS_ACTIVE:
        raise ProviderRuntimeError("The Model Offer has not been mapped to an active Canonical Model.")
    allowed_health = {ProviderRuntimeModelOffer.HEALTH_HEALTHY}
    if allow_degraded:
        allowed_health.add(ProviderRuntimeModelOffer.HEALTH_DEGRADED)
    if offer.health_status not in allowed_health:
        requirement = "healthy or degraded" if allow_degraded else "healthy"
        raise ProviderRuntimeError(f"The Model Offer must be {requirement} before this action.")
    if offer.status == ProviderRuntimeModelOffer.STATUS_DETECTED:
        conflict = ProviderRuntimeModelOffer.objects.exclude(id=offer.id).filter(
            runtime_account=offer.runtime_account,
            canonical_model=offer.canonical_model,
            status__in=[
                ProviderRuntimeModelOffer.STATUS_CONFIRMED,
                ProviderRuntimeModelOffer.STATUS_UNAVAILABLE,
            ],
        ).exists()
        if conflict:
            raise ProviderRuntimeError("This Runtime already has an active Offer for that Canonical Model.")
        offer.status = ProviderRuntimeModelOffer.STATUS_CONFIRMED
        offer.confirmed_at = timezone.now()
        offer.confirmed_by = actor
        offer.save(update_fields=["status", "confirmed_at", "confirmed_by", "updated_at"])
    return offer


@transaction.atomic
def update_provider_runtime_model_offer(*, request, runtime_id: str, offer_id: str, data: dict[str, Any]):
    runtime = get_provider_runtime(request=request, runtime_id=runtime_id)
    require_provider_runtime_admin(request=request, tenant=runtime.tenant)
    offer = runtime.model_offers.select_for_update().exclude(status=SoftDeleteModel.STATUS_DELETED).filter(id=offer_id).first()
    if offer is None:
        raise ProviderRuntimeNotFound("Runtime Model Offer not found.")
    if "canonical_model_id" in data:
        canonical = CanonicalModel.objects.filter(
            id=data.get("canonical_model_id"), status=CanonicalModel.STATUS_ACTIVE
        ).first()
        if canonical is None:
            raise ProviderRuntimeError("Canonical Model not found or inactive.")
        if offer.canonical_model_id and offer.canonical_model_id != canonical.id and (
            offer.sources.exclude(status=SoftDeleteModel.STATUS_DELETED).exists()
            or runtime_integration().offer_has_contributions(offer=offer)
        ):
            raise ProviderRuntimeError(
                "The Canonical Model cannot change after a Source or Marketplace Contribution has been created."
            )
        conflict = runtime.model_offers.exclude(id=offer.id).filter(
            canonical_model=canonical,
            status__in=[ProviderRuntimeModelOffer.STATUS_CONFIRMED, ProviderRuntimeModelOffer.STATUS_UNAVAILABLE],
        ).exists()
        if conflict:
            raise ProviderRuntimeError("This Runtime already has an active Offer for that Canonical Model.")
        offer.canonical_model = canonical
    for field in (
        "price_per_1k_tokens",
        "input_price_per_1k_tokens",
        "output_price_per_1k_tokens",
        "cached_input_price_per_1k_tokens",
        "reasoning_output_price_per_1k_tokens",
        "daily_limit",
        "monthly_limit",
        "capacity",
        "capabilities",
        "image_pricing",
    ):
        if field in data:
            setattr(offer, field, data[field])
    if "status" in data:
        next_status = data["status"]
        if next_status == ProviderRuntimeModelOffer.STATUS_CONFIRMED:
            if not offer.canonical_model_id:
                raise ProviderRuntimeError("Map a Canonical Model before confirming this Offer.")
            if offer.health_status not in {
                ProviderRuntimeModelOffer.HEALTH_HEALTHY,
                ProviderRuntimeModelOffer.HEALTH_DEGRADED,
            }:
                raise ProviderRuntimeError("The upstream model must pass discovery health before confirmation.")
            offer.confirmed_at = timezone.now()
            offer.confirmed_by = request.user
        offer.status = next_status
    offer.save()
    log_runtime_write(
        request=request,
        runtime=runtime,
        action="providers.runtime.model_offer.update",
        metadata={"model_offer_id": str(offer.id), "fields": sorted(data.keys())},
    )
    return offer


def _prepare_manual_catalog_refresh(runtime):
    if (
        runtime.runtime_type in {ProviderRuntimeAccount.RUNTIME_CODEX_PROXY, ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI}
        and str(getattr(settings, "NEXUS_PROVIDER_RUNTIME_RUNNER", "fake")).lower() in {"docker", "controller"}
        and runtime.status in {
            ProviderRuntimeAccount.STATUS_ACTIVE, ProviderRuntimeAccount.STATUS_UNHEALTHY,
            ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED,
        }
    ):
        # Use the same fenced/backoff recovery as maintenance, not an ad-hoc
        # restart. Never replay inference calls or resurrect an explicit Stop.
        runtime = reconcile_provider_runtime_health(runtime_id=runtime.id) or runtime
        if runtime.status != ProviderRuntimeAccount.STATUS_ACTIVE:
            raise ProviderRuntimeError(runtime.last_error or "Provider is recovering; retry model refresh shortly.")
    return runtime


def refresh_runtime_model_offers(
    *,
    runtime: ProviderRuntimeAccount,
    actor=None,
    strict: bool = False,
) -> list[ProviderRuntimeModelOffer]:
    if runtime.status != ProviderRuntimeAccount.STATUS_ACTIVE:
        if strict:
            raise ProviderRuntimeError("Start the Provider Runtime before refreshing its model catalog.")
        return []
    runtime_version = _model_probe_runtime_version(runtime)
    try:
        model_ids = discover_runtime_model_ids(runtime=runtime)
    except ProviderRuntimeError:
        provider_catalog_refresh_result(runtime_id=runtime.id, failed=True)
        if strict:
            raise
        return []
    contracts = getattr(runtime, "_discovered_model_contracts", {})
    probes = {
        upstream_model_id: probe_runtime_model(runtime=runtime, upstream_model_id=upstream_model_id)
        for upstream_model_id in model_ids
    }
    now = timezone.now()
    seen = set(model_ids)
    with transaction.atomic():
        current = (
            ProviderRuntimeAccount.objects.select_for_update(of=("self",))
            .select_related("provider_account", "source_provider_account")
            .get(pk=runtime.pk)
        )
        if current.status != ProviderRuntimeAccount.STATUS_ACTIVE or _model_probe_runtime_version(current) != runtime_version:
            raise ProviderProbeConflict()
        existing = {
            offer.upstream_model_id: offer
            for offer in current.model_offers.select_for_update(of=("self",))
            .exclude(status=SoftDeleteModel.STATUS_DELETED)
            .select_related("canonical_model")
        }
        new_offer_count = sum(1 for model_id in model_ids if model_id not in existing)
        if new_offer_count:
            from apps.common.resource_limits import enforce_capability

            enforce_capability(tenant=current.tenant, code="models.model_offers", requested=new_offer_count)
        for upstream_model_id in model_ids:
            offer = existing.get(upstream_model_id)
            canonical_model = (
                offer.canonical_model
                if offer is not None and offer.canonical_model_id
                else _canonical_model_for_upstream(upstream_model_id)
            )
            if offer is None:
                offer = ProviderRuntimeModelOffer.objects.create(
                    runtime_account=current,
                    canonical_model=canonical_model,
                    upstream_model_id=upstream_model_id,
                    status=ProviderRuntimeModelOffer.STATUS_DETECTED,
                )
            else:
                if not offer.canonical_model_id:
                    offer.canonical_model = canonical_model
                if offer.status == ProviderRuntimeModelOffer.STATUS_UNAVAILABLE:
                    offer.status = (
                        ProviderRuntimeModelOffer.STATUS_CONFIRMED
                        if offer.confirmed_at
                        else ProviderRuntimeModelOffer.STATUS_DETECTED
                    )
            declared_contract = contracts.get(upstream_model_id)
            if declared_contract is not None:
                offer.metadata = {**(offer.metadata or {}), "model_contract": declared_contract}
            _apply_model_probe(offer, probes[upstream_model_id], now)
            offer.last_discovered_at = now
            offer.last_health_check_at = now
            offer.save(update_fields=[
                "canonical_model", "status", "health_status", "health_reason", "last_discovered_at",
                "last_health_check_at", "updated_at", "metadata",
            ])
        current.model_offers.exclude(status__in=[
            ProviderRuntimeModelOffer.STATUS_DISABLED,
            SoftDeleteModel.STATUS_DELETED,
        ]).exclude(upstream_model_id__in=seen).update(
            status=ProviderRuntimeModelOffer.STATUS_UNAVAILABLE,
            health_status=ProviderRuntimeModelOffer.HEALTH_UNHEALTHY,
            health_reason="No longer advertised by the Provider Runtime model catalog.",
            last_health_check_at=now,
        )
        offers = list(current.model_offers.exclude(status=SoftDeleteModel.STATUS_DELETED).select_related("canonical_model"))
    # Commit the directory even when a model probe is inconclusive. Signal the
    # existing catalog worker to use its bounded retry backoff, not the normal
    # 30-minute success interval. Never replay an uncertain inference request.
    if any(healthy is None for healthy, _reason in probes.values()):
        provider_catalog_refresh_result(runtime_id=runtime.id, failed=True)
        if strict:
            raise ProviderRuntimeError("PROVIDER_MODEL_CHECK_PENDING: Model catalog updated; some model checks are inconclusive and will be checked again.")
    return offers


def _apply_model_probe(offer, probe, checked_at):
    from django.utils.dateparse import parse_datetime

    healthy, reason = probe
    previous = (offer.metadata or {}).get("health_probe", {})
    if not isinstance(previous, dict):
        previous = {}
    if healthy is None:
        definitive = previous.get("last_definitive_status", offer.health_status)
        try:
            last_verified = parse_datetime(previous.get("last_definitive_at", ""))
        except (ValueError, TypeError):
            last_verified = None
        if last_verified is not None and timezone.is_naive(last_verified):
            last_verified = None
        last_verified = last_verified or getattr(offer, "last_health_check_at", None) or checked_at
        fresh = timedelta() <= checked_at - last_verified <= timedelta(minutes=5)
        offer.health_status = (
            ProviderRuntimeModelOffer.HEALTH_DEGRADED
            if definitive == ProviderRuntimeModelOffer.HEALTH_HEALTHY and fresh
            else ProviderRuntimeModelOffer.HEALTH_UNKNOWN
        )
    else:
        definitive = ProviderRuntimeModelOffer.HEALTH_HEALTHY if healthy else ProviderRuntimeModelOffer.HEALTH_UNHEALTHY
        last_verified = checked_at
        offer.health_status = definitive
    offer.metadata = {**(offer.metadata or {}), "health_probe": {
        "state": "pending" if healthy is None else "verified",
        "last_definitive_status": definitive,
        "last_definitive_at": last_verified.isoformat(),
        "checked_at": checked_at.isoformat(),
    }}
    offer.health_reason = f"Advertised by the Provider Runtime model catalog. {reason}"


def _canonical_model_for_upstream(upstream_model_id: str) -> CanonicalModel:
    """Create or reuse the default Canonical Model for a discovered upstream name."""

    key = _canonical_model_key_for_upstream(upstream_model_id)
    defaults = {
        "display_name": upstream_model_id,
        "metadata": {
            "auto_created": True,
            "source": "provider_discovery",
            "upstream_model_id": upstream_model_id,
        },
    }
    try:
        with transaction.atomic():
            canonical_model, _ = CanonicalModel.objects.get_or_create(key=key, defaults=defaults)
            return canonical_model
    except IntegrityError:
        # A concurrent Provider refresh may have created the same global model.
        return CanonicalModel.objects.get(key=key)


def _canonical_model_key_for_upstream(upstream_model_id: str) -> str:
    """Keep normal upstream names intact and safely normalize exceptional identifiers."""

    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", upstream_model_id.strip()).strip("-._")
    if not normalized:
        normalized = "model"
    if len(normalized) <= CanonicalModel._meta.get_field("key").max_length:
        return normalized
    digest = hashlib.sha256(upstream_model_id.encode("utf-8")).hexdigest()[:12]
    prefix_length = CanonicalModel._meta.get_field("key").max_length - len(digest) - 1
    return f"{normalized[:prefix_length].rstrip('-._')}-{digest}"


def _trusted_inferred_model_contract(upstream_model_id: str) -> dict[str, list[str]] | None:
    """Infer only model families whose API operation is unambiguous.

    Some OpenAI-compatible runtimes expose the standard ``/models`` shape but
    omit Nexus' optional ``model_contract`` extension. GPT Image models cannot
    be safely tested through chat completions, and generating an image merely
    for health checking would be billable. Keep this inference intentionally
    narrow; unknown model names continue to use the historical chat contract.
    """

    model_name = upstream_model_id.strip().casefold().rsplit("/", 1)[-1]
    if model_name == "gpt-image" or model_name.startswith("gpt-image-"):
        return {
            "operations": ["images.generate", "images.edit"],
            "input_modalities": ["text", "image"],
            "output_modalities": ["image"],
        }
    return None


def discover_runtime_model_ids(*, runtime: ProviderRuntimeAccount) -> list[str]:
    if (
        runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI
        and str(getattr(settings, "NEXUS_PROVIDER_RUNTIME_RUNNER", "fake")).lower() in {"docker", "controller"}
    ):
        from .runtime_runner import probe_cliproxyapi_auth_health
        auth = probe_cliproxyapi_auth_health(runtime=runtime)
        if not auth.healthy:
            # OAuth failures can leave only built-in images in /models. That
            # response must not retire the last known text model directory.
            raise ProviderRuntimeError(auth.reason)
    base_url = (runtime.internal_api_url or "").rstrip("/")
    if not base_url:
        raise ProviderRuntimeError("Provider Runtime API URL is unavailable.")
    if base_url.endswith("/chat/completions"):
        base_url = base_url[: -len("/chat/completions")]
    url = base_url if base_url.endswith("/models") else f"{base_url}/models"
    key = ""
    try:
        if runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_DIRECT_API:
            account = runtime.source_provider_account
            if account is not None and account.encrypted_key:
                key = decrypt_secret(account.encrypted_key)
        elif runtime.encrypted_proxy_api_key:
            key = decrypt_secret(runtime.encrypted_proxy_api_key)
    except Exception as exc:
        raise ProviderRuntimeError("Provider Runtime credentials could not be decrypted.") from exc
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = Request(url, headers=headers, method="GET")
    payload = _read_provider_catalog(request)
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ProviderRuntimeError("Provider model catalog must return a data array.")
    advertised = [str(row.get("id") or "").strip() for row in rows if isinstance(row, dict)]
    from apps.gateway.image_serializers import ModelContractSerializer
    runtime._discovered_model_contracts = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("id") or "").strip()
        if not model_id:
            continue
        declaration = row.get("model_contract") or row
        if "operations" not in declaration:
            inferred_contract = _trusted_inferred_model_contract(model_id)
            if inferred_contract is not None:
                runtime._discovered_model_contracts[model_id] = inferred_contract
            continue
        validator = ModelContractSerializer(data=declaration)
        if not validator.is_valid():
            raise ProviderRuntimeError("Provider returned an invalid model operation contract.")
        runtime._discovered_model_contracts[model_id] = dict(validator.validated_data)
    invalid_count = sum(
        1
        for model_id in advertised
        if model_id and (len(model_id) > 255 or any(ord(char) < 32 for char in model_id))
    )
    if invalid_count:
        raise ProviderRuntimeError(
            f"Provider model catalog advertised {invalid_count} invalid model identifier(s)."
        )
    model_ids = sorted(set(advertised) - {""})
    if not model_ids:
        raise ProviderRuntimeError("Provider model catalog did not advertise any valid model IDs.")
    return model_ids


def _read_provider_catalog(request):
    timeout = min(15.0, max(1.0, float(getattr(settings, "NEXUS_DEPLOYMENT_HEALTH_TIMEOUT", 5))))
    for attempt in range(2):
        try:
            with urlopen(request, timeout=timeout if attempt == 0 else min(30.0, timeout * 3)) as response:
                body = response.read(2 * 1024 * 1024 + 1)
            if len(body) > 2 * 1024 * 1024:
                raise ProviderRuntimeError("PROVIDER_CATALOG_TOO_LARGE: Model directory exceeded the response limit; known models retained.")
            return json.loads(body.decode("utf-8"))
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if attempt == 0 and status in {429, 500, 502, 503, 504}:
                continue
            if status in {401, 403}:
                raise ProviderRuntimeError("PROVIDER_LOGIN_REQUIRED: Provider model catalog rejected authentication. Check credentials or sign in again.") from None
            raise ProviderRuntimeError(f"PROVIDER_CATALOG_HTTP_{status}: Provider model catalog returned HTTP {status}; known models retained.") from None
        except (URLError, TimeoutError, OSError, RemoteDisconnected, IncompleteRead) as exc:
            if attempt == 0:
                continue
            timed_out = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
            code = "PROVIDER_CATALOG_TIMEOUT" if timed_out else "PROVIDER_CATALOG_CONNECTION_FAILED"
            raise ProviderRuntimeError(f"{code}: Model directory could not be read after two attempts; known models retained. Retry scheduled.") from None
        except ValueError:
            raise ProviderRuntimeError("PROVIDER_CATALOG_INVALID_JSON: Provider returned an invalid JSON directory; known models retained.") from None


def probe_runtime_model(*, runtime: ProviderRuntimeAccount, upstream_model_id: str) -> tuple[bool | None, str]:
    """Probe an automatically discovered model without persisting a Source first."""

    contract = getattr(runtime, "_discovered_model_contracts", {}).get(upstream_model_id)
    if contract and "chat.completions" not in contract.get("operations", []):
        return True, "Catalog reachable. Image generation is not probed automatically to avoid charges."
    base_url = (runtime.internal_api_url or "").rstrip("/")
    if not base_url:
        return False, "Provider Runtime API URL is unavailable."
    if base_url.endswith("/models"):
        base_url = base_url[: -len("/models")]
    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    parsed = urlparse(url)
    if parsed.hostname in getattr(settings, "NEXUS_GATEWAY_FAIL_HOSTS", {"fail.local", "fail.provider.local"}):
        return False, "Provider model health probe failed."
    if parsed.hostname in getattr(settings, "NEXUS_GATEWAY_MOCK_HOSTS", set()):
        return True, "Discovered model health probe succeeded."
    try:
        key = runtime_api_key(runtime=runtime)
    except ProviderRuntimeError as exc:
        return False, str(exc.detail if hasattr(exc, "detail") else exc)
    headers = {"Accept": "application/json", "Content-Type": "application/json; charset=utf-8"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": upstream_model_id,
        "messages": [{"role": "user", "content": "health"}],
        "max_tokens": 1,
    }
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        # Inference can legitimately take longer than a local /models GET.
        # Keep a separate bounded budget and never retry this POST in place.
        with urlopen(request, timeout=min(120.0, max(1.0, float(getattr(
            settings, "NEXUS_PROVIDER_MODEL_PROBE_TIMEOUT_SECONDS", 30
        ))))) as response:
            raw = response.read().decode("utf-8")
            decoded = json.loads(raw)
            if not isinstance(decoded, dict):
                return False, "Provider model health probe returned an invalid response."
    except HTTPError as exc:
        if exc.code in {408, 429, 500, 502, 503, 504}:
            return None, f"PROVIDER_MODEL_HTTP_{exc.code}: Temporary model probe failure; retry scheduled."
        return False, f"Provider model health probe returned HTTP {exc.code}."
    except (URLError, TimeoutError, OSError, RemoteDisconnected, IncompleteRead) as exc:
        timed_out = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
        code = "PROVIDER_MODEL_TIMEOUT" if timed_out else "PROVIDER_MODEL_CONNECTION_FAILED"
        return None, f"{code}: Model probe is inconclusive; retry scheduled. The request was not replayed."
    except ValueError:
        return False, "PROVIDER_MODEL_INVALID_JSON: Model probe returned invalid JSON."
    return True, "Manual model health probe succeeded."


def runtime_api_key(*, runtime: ProviderRuntimeAccount) -> str:
    try:
        if runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_DIRECT_API:
            account = runtime.source_provider_account
            return decrypt_secret(account.encrypted_key) if account is not None and account.encrypted_key else ""
        return decrypt_secret(runtime.encrypted_proxy_api_key) if runtime.encrypted_proxy_api_key else ""
    except Exception as exc:
        raise ProviderRuntimeError("Provider Runtime credentials could not be decrypted.") from exc


def list_provider_runtimes(*, request):
    tenant = get_tenant_from_request(request)
    return (
        scope_queryset_to_current_project(ProviderRuntimeAccount.objects.filter(tenant=tenant), request)
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .select_related("provider_account", "source_provider_account", "source_provider_account__provider", "project")
        .prefetch_related("model_offers__canonical_model", *runtime_integration().runtime_prefetches())
        .filter(runtime_integration().runtime_filter(request=request, tenant=tenant))
        .order_by("-created_at")
    )


def get_provider_runtime(*, request, runtime_id: str) -> ProviderRuntimeAccount:
    tenant = get_tenant_from_request(request)
    runtime = (
        ProviderRuntimeAccount.objects.filter(tenant=tenant, id=runtime_id)
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .select_related("provider_account", "source_provider_account", "source_provider_account__provider", "project")
        .prefetch_related("model_offers__canonical_model", *runtime_integration().runtime_prefetches())
        .filter(runtime_integration().runtime_filter(request=request, tenant=tenant))
        .first()
    )
    if runtime is None:
        raise ProviderRuntimeNotFound()
    return runtime


@transaction.atomic
def create_provider_runtime(*, request, data: dict[str, Any]) -> ProviderRuntimeAccount:
    tenant = get_tenant_from_request(request)
    require_provider_runtime_admin(request=request, tenant=tenant)
    project = resolve_project_from_request(request=request, tenant=tenant)
    source_account = resolve_source_provider_account(tenant=tenant, account_id=data.get("source_provider_account_id"))
    if source_account is not None:
        source_account = ProviderAccount.objects.select_for_update().get(id=source_account.id)
        ensure_provider_account_has_no_runtime(source_account=source_account)
    validate_runtime_account_binding(runtime_type=data["runtime_type"], source_account=source_account)
    name = (data.get("name") or "").strip()
    if not name:
        if source_account is not None:
            name = runtime_name_for_source_account(account=source_account, runtime_type=data["runtime_type"])
        else:
            name = f"manual-{data['runtime_type'].replace('_', '-')}"
    runtime = ProviderRuntimeAccount.objects.create(
        tenant=tenant,
        project=project,
        owner=request.user,
        source_provider_account=source_account,
        name=name,
        runtime_type=data["runtime_type"],
        share_mode=data.get("share_mode", ProviderRuntimeAccount.SHARE_PRIVATE),
        storage_path=runtime_storage_path(tenant=tenant),
        public_login_path="",
    )
    runtime.public_login_path = f"/api/v1/provider-runtimes/{runtime.id}/login/"
    runtime.save(update_fields=["public_login_path", "updated_at"])
    log_runtime_write(request=request, runtime=runtime, action="providers.runtime.create")
    return runtime


@transaction.atomic
def update_provider_runtime(*, request, runtime_id: str, data: dict[str, Any]) -> ProviderRuntimeAccount:
    runtime = get_provider_runtime(request=request, runtime_id=runtime_id)
    require_provider_runtime_admin(request=request, tenant=runtime.tenant)
    critical_fields = {"runtime_type", "source_provider_account_id"}
    if runtime.status == ProviderRuntimeAccount.STATUS_ACTIVE and critical_fields.intersection(data.keys()):
        raise ProviderRuntimeError("Stop provider runtime before changing runtime type or source account.")

    changed: list[str] = []
    if "name" in data:
        runtime.name = data["name"]
        changed.append("name")
    if "runtime_type" in data:
        runtime.runtime_type = data["runtime_type"]
        changed.append("runtime_type")
    if "source_provider_account_id" in data:
        source_account = resolve_source_provider_account(
            tenant=runtime.tenant,
            account_id=data.get("source_provider_account_id"),
        )
        if source_account is not None:
            source_account = ProviderAccount.objects.select_for_update().get(id=source_account.id)
            ensure_provider_account_has_no_runtime(source_account=source_account, exclude_runtime_id=runtime.id)
        runtime.source_provider_account = source_account
        changed.append("source_provider_account")
    validate_runtime_account_binding(runtime_type=runtime.runtime_type, source_account=runtime.source_provider_account)

    runtime.save(update_fields=changed + ["updated_at"])
    log_runtime_write(request=request, runtime=runtime, action="providers.runtime.update", metadata={"fields": sorted(data.keys())})
    return runtime


def remove_provider_runtime(*, request, runtime_id: str) -> ProviderRuntimeAccount:
    runtime = get_provider_runtime(request=request, runtime_id=runtime_id)
    require_provider_runtime_admin(request=request, tenant=runtime.tenant)
    with transaction.atomic():
        runtime = (
            ProviderRuntimeAccount.objects.select_for_update(of=("self",))
            .select_related("provider_account")
            .get(id=runtime.id)
        )
        previous_status = runtime.status
        runtime.status = ProviderRuntimeAccount.STATUS_STOPPING
        runtime.save(update_fields=["status", "updated_at"])
    try:
        if runtime.runtime_type != ProviderRuntimeAccount.RUNTIME_DIRECT_API:
            get_provider_runtime_runner().stop(runtime=runtime)
    except Exception:
        with transaction.atomic():
            locked = ProviderRuntimeAccount.objects.select_for_update().get(id=runtime.id)
            if locked.status == ProviderRuntimeAccount.STATUS_STOPPING:
                locked.status = previous_status
                locked.save(update_fields=["status", "updated_at"])
        raise

    with transaction.atomic():
        runtime = (
            ProviderRuntimeAccount.objects.select_for_update(of=("self",))
            .select_related("provider_account")
            .get(id=runtime.id)
        )
        if runtime.status != ProviderRuntimeAccount.STATUS_STOPPING:
            raise ProviderRuntimeBusy("Provider runtime lifecycle changed while it was being removed.")
        now = timezone.now()
        runtime_integration().remove_contributions(runtime=runtime, now=now)

        deployments = list(runtime.sources.exclude(status=SoftDeleteModel.STATUS_DELETED))
        for deployment in deployments:
            ModelGroupDeployment.objects.filter(deployment=deployment).exclude(status=SoftDeleteModel.STATUS_DELETED).update(
                status=SoftDeleteModel.STATUS_DELETED,
                deleted_at=now,
            )
            deployment.delete()

        provider_account = runtime.provider_account
        if provider_account is not None and provider_account.account_id == f"runtime_{runtime.id}":
            provider_account.delete()

        runtime.share_mode = ProviderRuntimeAccount.SHARE_PRIVATE
        runtime.status = SoftDeleteModel.STATUS_DELETED
        runtime.deleted_at = now
        runtime.save(update_fields=["share_mode", "status", "deleted_at", "updated_at"])
    log_runtime_write(request=request, runtime=runtime, action="providers.runtime.remove")
    return runtime


def start_direct_api_runtime(*, request, runtime: ProviderRuntimeAccount) -> ProviderRuntimeAccount:
    with transaction.atomic():
        runtime = (
            ProviderRuntimeAccount.objects.select_for_update(of=("self",))
            .select_related("source_provider_account")
            .get(id=runtime.id)
        )
        if runtime.status == ProviderRuntimeAccount.STATUS_ACTIVE:
            return runtime
        if runtime.status == ProviderRuntimeAccount.STATUS_STOPPING:
            raise ProviderRuntimeBusy("Provider runtime is stopping. Wait before starting it again.")
        validate_runtime_account_binding(runtime_type=runtime.runtime_type, source_account=runtime.source_provider_account)
        runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
        runtime.last_error = ""
        runtime.container_id = ""
        runtime.internal_login_url = ""
        runtime.internal_api_url = runtime.source_provider_account.url
        runtime.encrypted_proxy_api_key = ""
        runtime.save(
            update_fields=[
                "status",
                "last_error",
                "container_id",
                "internal_login_url",
                "internal_api_url",
                "encrypted_proxy_api_key",
                "updated_at",
            ]
        )
        log_runtime_write(request=request, runtime=runtime, action="providers.runtime.start")
    # Discovery performs upstream HTTP calls and must not keep the Runtime row
    # or the surrounding request transaction open.
    refresh_runtime_model_offers(runtime=runtime, actor=request.user)
    return runtime


def start_provider_runtime(*, request, runtime_id: str) -> ProviderRuntimeAccount:
    runtime = get_provider_runtime(request=request, runtime_id=runtime_id)
    require_provider_runtime_admin(request=request, tenant=runtime.tenant)
    if runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_DIRECT_API:
        return start_direct_api_runtime(request=request, runtime=runtime)
    with transaction.atomic():
        runtime = ProviderRuntimeAccount.objects.select_for_update().get(id=runtime.id)
        if runtime.status in {ProviderRuntimeAccount.STATUS_ACTIVE, ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED}:
            return runtime
        if runtime.status == ProviderRuntimeAccount.STATUS_STARTING:
            return runtime
        if runtime.status == ProviderRuntimeAccount.STATUS_STOPPING:
            raise ProviderRuntimeBusy("Provider runtime is stopping. Wait before starting it again.")
        if runtime.encrypted_proxy_api_key:
            proxy_api_key = decrypt_secret(runtime.encrypted_proxy_api_key)
        else:
            proxy_api_key = f"nexus-prx-{secrets.token_urlsafe(24)}"
            runtime.encrypted_proxy_api_key = encrypt_secret(proxy_api_key)
        runtime.status = ProviderRuntimeAccount.STATUS_STARTING
        runtime.last_error = ""
        runtime.save(update_fields=["status", "last_error", "encrypted_proxy_api_key", "updated_at"])
    try:
        result = get_provider_runtime_runner().start(runtime=runtime, proxy_api_key=proxy_api_key)
    except Exception as exc:
        with transaction.atomic():
            runtime = ProviderRuntimeAccount.objects.select_for_update().get(id=runtime.id)
            if runtime.status == ProviderRuntimeAccount.STATUS_STARTING:
                runtime.status = ProviderRuntimeAccount.STATUS_FAILED
                runtime.last_error = str(exc)[:1024]
                runtime.save(update_fields=["status", "last_error", "updated_at"])
        log_runtime_write(request=request, runtime=runtime, action="providers.runtime.start.failed")
        raise ProviderRuntimeError(runtime.last_error) from exc
    lifecycle_changed = False
    with transaction.atomic():
        runtime = ProviderRuntimeAccount.objects.select_for_update().get(id=runtime.id)
        if runtime.status != ProviderRuntimeAccount.STATUS_STARTING:
            lifecycle_changed = True
        else:
            runtime.container_id = result.container_id
            runtime.internal_login_url = result.internal_login_url
            runtime.internal_api_url = result.internal_api_url
            runtime.save(
                update_fields=[
                    "container_id",
                    "internal_login_url",
                    "internal_api_url",
                    "updated_at",
                ]
            )
    if lifecycle_changed:
        # A concurrent removal may win while Docker is still starting. Clean up
        # the exact returned container so it cannot survive as an orphan.
        runtime.container_id = result.container_id
        try:
            get_provider_runtime_runner().stop(runtime=runtime)
        finally:
            raise ProviderRuntimeBusy("Provider runtime lifecycle changed while it was starting.")
    login_result = login_runtime_if_required(request=request, runtime=runtime)
    with transaction.atomic():
        current = ProviderRuntimeAccount.objects.select_for_update().get(id=runtime.id)
        if current.status != ProviderRuntimeAccount.STATUS_STARTING:
            raise ProviderRuntimeBusy("Provider runtime lifecycle changed while login was completing.")
        if login_result == "active":
            current.status = ProviderRuntimeAccount.STATUS_ACTIVE
            current.last_error = ""
        elif login_result == "login_required":
            current.status = ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED
            current.last_error = "Interactive login is required."
        else:
            current.status = ProviderRuntimeAccount.STATUS_FAILED
        current.save(update_fields=["status", "last_error", "updated_at"])
        runtime = current
    log_runtime_write(request=request, runtime=runtime, action="providers.runtime.start")
    if runtime.status == ProviderRuntimeAccount.STATUS_ACTIVE:
        refresh_runtime_model_offers(runtime=runtime, actor=request.user)
    return runtime


def restore_provider_runtime_processes() -> dict[str, int]:
    """Restore Provider containers whose persisted lifecycle says they should run.

    A Cloud process restart must not rotate the Provider proxy credential or
    require the user to repeat interactive login. Direct API runtimes do not
    own a local process and are intentionally excluded.
    """

    desired_statuses = {
        ProviderRuntimeAccount.STATUS_STARTING,
        ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED,
        ProviderRuntimeAccount.STATUS_ACTIVE,
        ProviderRuntimeAccount.STATUS_UNHEALTHY,
        ProviderRuntimeAccount.STATUS_FAILED,
    }
    runtime_ids = list(
        ProviderRuntimeAccount.objects.filter(runtime_integration().maintenance_filter()).filter(status__in=desired_statuses)
        .exclude(Q(status=ProviderRuntimeAccount.STATUS_FAILED) & Q(encrypted_proxy_api_key=""))
        .exclude(runtime_type=ProviderRuntimeAccount.RUNTIME_DIRECT_API)
        .values_list("id", flat=True)
    )
    result_counts = {"examined": len(runtime_ids), "restored": 0, "already_running": 0, "failed": 0}
    for runtime_id in runtime_ids:
        try:
            outcome = _reconcile_provider_runtime(runtime_id=runtime_id, startup=True)
            result_counts["restored"] += int(outcome.restored)
            result_counts["already_running"] += int(outcome.already_running)
            result_counts["failed"] += int(outcome.failed)
        except Exception:  # One Provider must not prevent the Cloud from starting.
            result_counts["failed"] += 1

    return result_counts


def _sync_provider_runtime_endpoint(*, runtime: ProviderRuntimeAccount) -> None:
    runtime.sources.exclude(status=SoftDeleteModel.STATUS_DELETED).update(endpoint=runtime.internal_api_url)
    provider_account = runtime.provider_account
    if provider_account is not None and provider_account.account_id == f"runtime_{runtime.id}":
        provider_account.url = runtime.internal_api_url
        provider_account.encrypted_key = runtime.encrypted_proxy_api_key
        provider_account.save(update_fields=["url", "encrypted_key", "updated_at"])


def _provider_process_recovery_recommended(health) -> bool:
    """Distinguish a lost local process from an upstream service failure."""

    if getattr(health, "recovery_recommended", False) is True:
        return True
    # Exact legacy controller messages; never infer a restart from arbitrary
    # upstream text such as an HTTP 503 containing the word "timeout".
    return getattr(health, "reason", "") in {
            "container is not running",
            "container is stopped",
            "docker inspect failed",
            "runtime API URL is missing",
            "Runtime transport is unavailable.",
    }


def _provider_runtime_recovery_key(runtime_id) -> str:
    return f"nexus:providers:runtime-recovery:{runtime_id}"


def _claim_provider_recovery(runtime_id) -> bool:
    """Bound restart storms; a cache outage must never trigger extra restarts."""
    key = _provider_runtime_recovery_key(runtime_id)
    try:
        attempts = min(int(cache.get(key + ":attempts", 0)), 8)
        delay = min(30 * (2 ** attempts), 300)
        if not cache.add(key, True, timeout=delay):
            return False
        cache.set(key + ":attempts", attempts + 1, timeout=86400)
        return True
    except Exception:
        return False


def _clear_provider_recovery(runtime_id) -> None:
    try:
        cache.delete_many([
            _provider_runtime_recovery_key(runtime_id),
            _provider_runtime_recovery_key(runtime_id) + ":attempts",
        ])
    except Exception:
        pass


def provider_catalog_refresh_result(*, runtime_id, failed: bool) -> None:
    """Shared retry schedule for startup, recovery and the catalog worker."""
    key = f"nexus:providers:catalog:{runtime_id}"
    try:
        previous = cache.get(key) or {}
        attempts = min(int(previous.get("attempts", 0)) + 1, 8) if failed else 0
        delay = min(30 * (2 ** (attempts - 1)), 300) if failed else max(
            60, int(getattr(settings, "NEXUS_PROVIDER_MODEL_DISCOVERY_INTERVAL_SECONDS", 1800))
        )
        cache.set(key, {"attempts": attempts, "next_at": time.time() + delay}, timeout=max(86400, delay * 2))
    except Exception:
        # Catalog contents still live in PostgreSQL. Cache availability does
        # not decide whether the runtime is healthy.
        pass


def login_provider_runtime_with_credentials(*, request, runtime_id: str) -> ProviderRuntimeAccount:
    runtime = get_provider_runtime(request=request, runtime_id=runtime_id)
    require_provider_runtime_admin(request=request, tenant=runtime.tenant)
    if runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_DIRECT_API:
        raise ProviderRuntimeError("Direct API runtimes do not require login.")
    if not runtime.container_id or not runtime.internal_api_url:
        raise ProviderRuntimeError("Start provider runtime before credential login.")
    if runtime.source_provider_account is None:
        raise ProviderRuntimeError("Provider runtime does not have a source provider account.")
    proxy_api_key = decrypt_secret(runtime.encrypted_proxy_api_key) if runtime.encrypted_proxy_api_key else ""
    if not proxy_api_key:
        raise ProviderRuntimeError("Provider runtime proxy key is not available.")
    login_result = login_runtime_if_required(request=request, runtime=runtime)
    with transaction.atomic():
        current = ProviderRuntimeAccount.objects.select_for_update().get(id=runtime.id)
        if current.status in {
            ProviderRuntimeAccount.STATUS_STARTING,
            ProviderRuntimeAccount.STATUS_STOPPING,
            ProviderRuntimeAccount.STATUS_STOPPED,
            SoftDeleteModel.STATUS_DELETED,
        }:
            raise ProviderRuntimeBusy("Provider runtime lifecycle changed while login was completing.")
        current.last_error = runtime.last_error
        if login_result != "active":
            current.status = ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED if login_result == "login_required" else ProviderRuntimeAccount.STATUS_FAILED
            current.save(update_fields=["status", "last_error", "updated_at"])
            runtime = current
        else:
            current.status = ProviderRuntimeAccount.STATUS_ACTIVE
            current.last_error = ""
            current.save(update_fields=["status", "last_error", "updated_at"])
            runtime = current
    if login_result != "active":
        log_runtime_write(request=request, runtime=runtime, action="providers.runtime.login.required")
        return runtime
    log_runtime_write(request=request, runtime=runtime, action="providers.runtime.login.credentials")
    refresh_runtime_model_offers(runtime=runtime, actor=request.user)
    return runtime


def stop_provider_runtime(*, request, runtime_id: str) -> ProviderRuntimeAccount:
    runtime = get_provider_runtime(request=request, runtime_id=runtime_id)
    require_provider_runtime_admin(request=request, tenant=runtime.tenant)
    with transaction.atomic():
        runtime = ProviderRuntimeAccount.objects.select_for_update().get(id=runtime.id)
        if runtime.status == ProviderRuntimeAccount.STATUS_STOPPED:
            return runtime
        if runtime.status == ProviderRuntimeAccount.STATUS_STOPPING:
            return runtime
        if runtime.status == ProviderRuntimeAccount.STATUS_STARTING:
            raise ProviderRuntimeBusy("Provider runtime is still starting. Wait before stopping it.")
        previous_status = runtime.status
        runtime.status = ProviderRuntimeAccount.STATUS_STOPPING
        runtime.save(update_fields=["status", "updated_at"])
    try:
        if runtime.runtime_type != ProviderRuntimeAccount.RUNTIME_DIRECT_API:
            get_provider_runtime_runner().stop(runtime=runtime)
    except Exception:
        with transaction.atomic():
            runtime = ProviderRuntimeAccount.objects.select_for_update().get(id=runtime.id)
            if runtime.status == ProviderRuntimeAccount.STATUS_STOPPING:
                runtime.status = previous_status
                runtime.save(update_fields=["status", "updated_at"])
        raise
    with transaction.atomic():
        runtime = ProviderRuntimeAccount.objects.select_for_update().get(id=runtime.id)
        if runtime.status != ProviderRuntimeAccount.STATUS_STOPPING:
            raise ProviderRuntimeBusy("Provider runtime lifecycle changed while it was stopping.")
        runtime.status = ProviderRuntimeAccount.STATUS_STOPPED
        runtime.save(update_fields=["status", "updated_at"])
    runtime.model_offers.exclude(status=SoftDeleteModel.STATUS_DELETED).update(
        health_status=ProviderRuntimeModelOffer.HEALTH_UNHEALTHY,
        health_reason="Provider Runtime is stopped.",
        last_health_check_at=timezone.now(),
    )
    log_runtime_write(request=request, runtime=runtime, action="providers.runtime.stop")
    return runtime


def check_provider_runtime_health(*, request, runtime_id: str) -> ProviderRuntimeAccount:
    runtime = get_provider_runtime(request=request, runtime_id=runtime_id)
    require_provider_runtime_admin(request=request, tenant=runtime.tenant)
    if runtime.status in {ProviderRuntimeAccount.STATUS_STARTING, ProviderRuntimeAccount.STATUS_STOPPING}:
        raise ProviderRuntimeBusy("Wait for the current Provider lifecycle operation before checking health.")
    if runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_DIRECT_API:
        return check_direct_api_runtime_health(request=request, runtime=runtime)
    result = get_provider_runtime_runner().health_check(runtime=runtime)
    checked_at = timezone.now()
    with transaction.atomic():
        current = (
            ProviderRuntimeAccount.objects.select_for_update(of=("self",))
            .select_related("source_provider_account")
            .get(id=runtime.id)
        )
        if current.status in {
            ProviderRuntimeAccount.STATUS_STARTING,
            ProviderRuntimeAccount.STATUS_STOPPING,
            ProviderRuntimeAccount.STATUS_STOPPED,
            SoftDeleteModel.STATUS_DELETED,
        }:
            raise ProviderRuntimeBusy("Provider runtime lifecycle changed while health was being checked.")
        current.last_health_check_at = checked_at
        current.last_error = "" if result.healthy else result.reason[:1024]
        if result.login_required:
            current.status = ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED
        elif result.healthy:
            proxy_api_key = decrypt_secret(current.encrypted_proxy_api_key) if current.encrypted_proxy_api_key else ""
            activate_logged_in_runtime(runtime=current, proxy_api_key=proxy_api_key, actor=request.user)
        else:
            current.status = ProviderRuntimeAccount.STATUS_UNHEALTHY
        current.save(update_fields=["status", "last_error", "last_health_check_at", "updated_at"])
        if current.status != ProviderRuntimeAccount.STATUS_ACTIVE:
            current.model_offers.exclude(status=SoftDeleteModel.STATUS_DELETED).update(
                health_status=ProviderRuntimeModelOffer.HEALTH_UNHEALTHY,
                health_reason=current.last_error or "Provider Runtime health check failed.",
                last_health_check_at=checked_at,
            )
        record_runtime_health_check(
            runtime=current,
            status=current.status,
            reason=current.last_error,
            latency_ms=getattr(result, "latency_ms", 0) or 0,
        )
        runtime = current
    # Model discovery is a separate explicit/30-minute operation. A 60-second
    # Runtime health check must not issue a paid inference against every model.
    log_runtime_write(request=request, runtime=runtime, action=f"providers.runtime.health.{runtime.status}")
    return runtime


def check_direct_api_runtime_health(*, request, runtime: ProviderRuntimeAccount) -> ProviderRuntimeAccount:
    checked_at = timezone.now()
    with transaction.atomic():
        current = (
            ProviderRuntimeAccount.objects.select_for_update(of=("self",))
            .select_related("source_provider_account")
            .get(id=runtime.id)
        )
        if current.status in {
            ProviderRuntimeAccount.STATUS_STARTING,
            ProviderRuntimeAccount.STATUS_STOPPING,
            ProviderRuntimeAccount.STATUS_STOPPED,
            SoftDeleteModel.STATUS_DELETED,
        }:
            raise ProviderRuntimeBusy("Provider runtime lifecycle changed while health was being checked.")
        source = current.source_provider_account
        current.last_health_check_at = checked_at
        if source is None or source.status != SoftDeleteModel.STATUS_ACTIVE or not source.encrypted_key:
            current.status = ProviderRuntimeAccount.STATUS_UNHEALTHY
            current.last_error = "Direct API source account is missing or inactive."
        else:
            current.status = ProviderRuntimeAccount.STATUS_ACTIVE
            current.last_error = ""
        current.save(update_fields=["status", "last_error", "last_health_check_at", "updated_at"])
        if current.status != ProviderRuntimeAccount.STATUS_ACTIVE:
            current.model_offers.exclude(status=SoftDeleteModel.STATUS_DELETED).update(
                health_status=ProviderRuntimeModelOffer.HEALTH_UNHEALTHY,
                health_reason=current.last_error,
                last_health_check_at=checked_at,
            )
        record_runtime_health_check(
            runtime=current,
            status=current.status,
            reason=current.last_error,
            latency_ms=(current.sources.order_by("last_latency_ms").values_list("last_latency_ms", flat=True).first() or 0),
        )
        runtime = current
    log_runtime_write(request=request, runtime=runtime, action=f"providers.runtime.health.{runtime.status}")
    return runtime


@dataclass
class _ProviderRecoveryOutcome:
    runtime: ProviderRuntimeAccount | None
    restored: bool = False
    already_running: bool = False
    failed: bool = False


def reconcile_provider_runtime_health(*, runtime_id) -> ProviderRuntimeAccount | None:
    return _reconcile_provider_runtime(runtime_id=runtime_id).runtime


def _reconcile_provider_runtime(*, runtime_id, startup=False) -> _ProviderRecoveryOutcome:
    """Continuously reconcile one Provider without requiring a user request.

    The network probe is intentionally performed without holding a row lock.
    Before persisting the result we re-check the lifecycle state so a concurrent
    stop/start operation always wins over a stale health response.
    """
    runtime = (
        ProviderRuntimeAccount.objects.filter(runtime_integration().maintenance_filter()).select_related("source_provider_account", "owner")
        .filter(id=runtime_id)
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .first()
    )
    eligible = {
        ProviderRuntimeAccount.STATUS_ACTIVE,
        ProviderRuntimeAccount.STATUS_UNHEALTHY,
        ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED,
    }
    if startup:
        eligible |= {ProviderRuntimeAccount.STATUS_STARTING, ProviderRuntimeAccount.STATUS_FAILED}
    elif runtime is not None and runtime.encrypted_proxy_api_key and runtime.last_error.startswith("Automatic start recovery failed:"):
        eligible.add(ProviderRuntimeAccount.STATUS_FAILED)
    outcome = _ProviderRecoveryOutcome(runtime)
    if runtime is None or runtime.status not in eligible:
        return outcome
    if startup and not runtime.encrypted_proxy_api_key:
        # Commit before calling the isolated controller, which deliberately
        # loads credentials from PostgreSQL rather than accepting them on IPC.
        with transaction.atomic():
            runtime = ProviderRuntimeAccount.objects.filter(runtime_integration().maintenance_filter()).select_for_update().get(id=runtime.id)
            if runtime.status not in eligible:
                return _ProviderRecoveryOutcome(runtime)
            if not runtime.encrypted_proxy_api_key:
                runtime.encrypted_proxy_api_key = encrypt_secret(f"nexus-prx-{secrets.token_urlsafe(24)}")
                runtime.save(update_fields=["encrypted_proxy_api_key", "updated_at"])
    observed_version = _model_probe_runtime_version(runtime)
    observed_health_at = runtime.last_health_check_at
    checked_at = timezone.now()
    next_status = ProviderRuntimeAccount.STATUS_UNHEALTHY
    reason = ""
    latency_ms = 0
    result = None
    runner = None
    try:
        if runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_DIRECT_API:
            source = runtime.source_provider_account
            if source is None or source.status != SoftDeleteModel.STATUS_ACTIVE or not source.encrypted_key:
                raise ProviderRuntimeError("Direct API source account is missing or inactive.")
            try:
                decrypt_secret(source.encrypted_key)
            except Exception:
                raise ProviderRuntimeError(
                    "Provider credentials could not be decrypted. Re-enter the API key or restore the original encryption keyring."
                ) from None
            next_status = ProviderRuntimeAccount.STATUS_ACTIVE
        else:
            runner = get_provider_runtime_runner()
            result = runner.health_check(runtime=runtime)
            outcome.already_running = bool(result.healthy or result.login_required)
            latency_ms = getattr(result, "latency_ms", 0) or 0
            if result.login_required:
                next_status = ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED
                reason = result.reason[:1024]
            elif not result.healthy:
                raise ProviderRuntimeError(result.reason or "Provider Runtime health check failed.")
            else:
                next_status = ProviderRuntimeAccount.STATUS_ACTIVE
    except ProviderRuntimeError as exc:
        next_status = ProviderRuntimeAccount.STATUS_UNHEALTHY
        reason = str(exc)[:1024] or "Provider Runtime health check failed."
    except Exception:  # Do not persist raw controller/network exceptions.
        next_status = ProviderRuntimeAccount.STATUS_UNHEALTHY
        reason = "Provider Runtime health check unavailable; retry scheduled."

    with transaction.atomic():
        locked = ProviderRuntimeAccount.objects.filter(runtime_integration().maintenance_filter()).select_for_update().get(id=runtime.id)
        if (
            _model_probe_runtime_version(locked) != observed_version
            or locked.last_health_check_at != observed_health_at
        ):
            return _ProviderRecoveryOutcome(locked)
        if (
            result is not None
            and not result.healthy
            and not result.login_required
            and _provider_process_recovery_recommended(result)
            and _claim_provider_recovery(locked.id)
        ):
            # Rare process repair is serialized with Start/Stop and config
            # writes. Stop either wins before this lock, or runs afterwards
            # against the restored endpoint; a stale probe cannot resurrect it.
            recovery_stage = "credential validation"
            try:
                proxy_key = decrypt_secret(locked.encrypted_proxy_api_key)
                if not proxy_key:
                    raise ProviderRuntimeError("Provider Runtime proxy key is unavailable.")
                recovery_stage = "container restore"
                restored = runner.restore(runtime=locked, proxy_api_key=proxy_key)
                outcome.restored = True
                locked.container_id = restored.container_id
                locked.internal_login_url = restored.internal_login_url
                locked.internal_api_url = restored.internal_api_url
                locked.save(update_fields=["container_id", "internal_login_url", "internal_api_url", "updated_at"])
                recovery_stage = "endpoint synchronization"
                _sync_provider_runtime_endpoint(runtime=locked)
                recovery_stage = "health verification"
                result = restored.health or runner.health_check(runtime=locked)
                if result.healthy:
                    next_status, reason = ProviderRuntimeAccount.STATUS_ACTIVE, ""
                elif result.login_required:
                    next_status, reason = ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED, "Interactive login is required."
                else:
                    reason = "Automatic recovery did not pass health check; retry scheduled."
            except Exception:
                # Report the failed step, never raw Docker/controller errors
                # which may contain credentials, host paths or private URLs.
                reason = f"Automatic Provider Runtime recovery failed during {recovery_stage}; retry scheduled."
        locked.status = next_status
        locked.last_error = "" if next_status == ProviderRuntimeAccount.STATUS_ACTIVE else reason
        locked.last_health_check_at = checked_at
        if next_status == ProviderRuntimeAccount.STATUS_ACTIVE and locked.source_provider_account_id:
            source = locked.source_provider_account
            source.login_status = ProviderAccount.LOGIN_ACTIVE
            source.last_login_error = ""
            source.last_login_at = checked_at
            source.save(update_fields=["login_status", "last_login_error", "last_login_at", "updated_at"])
        elif next_status == ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED and locked.source_provider_account_id:
            source = locked.source_provider_account
            source.login_status = ProviderAccount.LOGIN_REQUIRED
            source.last_login_error = reason
            source.save(update_fields=["login_status", "last_login_error", "updated_at"])
        locked.save(update_fields=["status", "last_error", "last_health_check_at", "updated_at"])
        if next_status != ProviderRuntimeAccount.STATUS_ACTIVE and result is not None and not getattr(result, "inconclusive", False):
            locked.model_offers.exclude(status=SoftDeleteModel.STATUS_DELETED).update(
                health_status=ProviderRuntimeModelOffer.HEALTH_UNHEALTHY,
                health_reason=locked.last_error or "Provider Runtime health check failed.",
                last_health_check_at=checked_at,
            )
        record_runtime_health_check(
            runtime=locked,
            status=locked.status,
            reason=locked.last_error,
            latency_ms=latency_ms,
        )
    if next_status == ProviderRuntimeAccount.STATUS_ACTIVE:
        _clear_provider_recovery(locked.id)
        if startup:
            try:
                refresh_runtime_model_offers(runtime=locked, actor=locked.owner, strict=True)
                provider_catalog_refresh_result(runtime_id=locked.id, failed=False)
            except Exception:
                # Runtime health and catalog health are separate observations.
                # The catalog task will retry without discarding known offers.
                provider_catalog_refresh_result(runtime_id=locked.id, failed=True)
        elif runtime.status != ProviderRuntimeAccount.STATUS_ACTIVE or outcome.restored:
            # Only the catalog worker performs potentially slow / paid model
            # probes. Recovering one process must not stall everyone's health.
            try:
                cache.set(f"nexus:providers:catalog:{locked.id}", {"attempts": 0, "next_at": 0}, timeout=86400)
            except Exception:
                pass
    outcome.runtime = locked
    outcome.failed = next_status == ProviderRuntimeAccount.STATUS_UNHEALTHY
    return outcome


def reconcile_stale_provider_runtime_starts() -> dict[str, int]:
    """Recover a lost start/controller response without requiring a Cloud restart."""

    cutoff = timezone.now() - timedelta(
        seconds=int(getattr(settings, "NEXUS_PROVIDER_LIFECYCLE_STALE_SECONDS", 180))
    )
    runtime_ids = list(
        ProviderRuntimeAccount.objects.filter(runtime_integration().maintenance_filter()).filter(
            status=ProviderRuntimeAccount.STATUS_STARTING,
            updated_at__lt=cutoff,
        ).values_list("id", flat=True)
    )
    recovered = 0
    failed = 0
    for runtime_id in runtime_ids:
        runtime = (
            ProviderRuntimeAccount.objects.filter(runtime_integration().maintenance_filter()).select_related("source_provider_account", "owner")
            .filter(id=runtime_id, status=ProviderRuntimeAccount.STATUS_STARTING)
            .first()
        )
        if runtime is None:
            continue
        try:
            if runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_DIRECT_API:
                source = runtime.source_provider_account
                if source is None or source.status != SoftDeleteModel.STATUS_ACTIVE or not source.encrypted_key:
                    raise ProviderRuntimeError("Direct API source account is missing or inactive.")
                result = None
                next_status = ProviderRuntimeAccount.STATUS_ACTIVE
                reason = ""
            else:
                proxy_api_key = decrypt_secret(runtime.encrypted_proxy_api_key) if runtime.encrypted_proxy_api_key else ""
                if not proxy_api_key:
                    raise ProviderRuntimeError("Provider Runtime proxy key is unavailable.")
                result = get_provider_runtime_runner().restore(runtime=runtime, proxy_api_key=proxy_api_key)
                runtime.container_id = result.container_id
                runtime.internal_login_url = result.internal_login_url
                runtime.internal_api_url = result.internal_api_url
                health = get_provider_runtime_runner().health_check(runtime=runtime)
                if health.healthy:
                    next_status = ProviderRuntimeAccount.STATUS_ACTIVE
                    reason = ""
                elif health.login_required:
                    next_status = ProviderRuntimeAccount.STATUS_LOGIN_REQUIRED
                    reason = "Interactive login is required."
                else:
                    raise ProviderRuntimeError(health.reason or "Recovered Runtime did not pass health check.")
            with transaction.atomic():
                locked = ProviderRuntimeAccount.objects.filter(runtime_integration().maintenance_filter()).select_for_update().get(id=runtime_id)
                if locked.status != ProviderRuntimeAccount.STATUS_STARTING:
                    continue
                if result is not None:
                    locked.container_id = result.container_id
                    locked.internal_login_url = result.internal_login_url
                    locked.internal_api_url = result.internal_api_url
                elif locked.source_provider_account_id:
                    locked.internal_api_url = locked.source_provider_account.url
                locked.status = next_status
                locked.last_error = reason
                locked.last_health_check_at = timezone.now()
                locked.save(update_fields=[
                    "container_id", "internal_login_url", "internal_api_url", "status",
                    "last_error", "last_health_check_at", "updated_at",
                ])
                if result is not None:
                    _sync_provider_runtime_endpoint(runtime=locked)
                record_runtime_health_check(
                    runtime=locked,
                    status=locked.status,
                    reason="Recovered interrupted start.",
                )
                recovered += 1
        except Exception as exc:
            with transaction.atomic():
                locked = ProviderRuntimeAccount.objects.filter(runtime_integration().maintenance_filter()).select_for_update().filter(id=runtime_id).first()
                if locked is not None and locked.status == ProviderRuntimeAccount.STATUS_STARTING:
                    locked.status = ProviderRuntimeAccount.STATUS_FAILED
                    locked.last_error = f"Automatic start recovery failed: {exc}"[:1024]
                    locked.last_health_check_at = timezone.now()
                    locked.save(update_fields=["status", "last_error", "last_health_check_at", "updated_at"])
                    record_runtime_health_check(runtime=locked, status=locked.status, reason=locked.last_error)
            failed += 1
    return {"examined": len(runtime_ids), "recovered": recovered, "failed": failed}


def reconcile_stale_provider_runtime_stops() -> dict[str, int]:
    cutoff = timezone.now() - timedelta(
        seconds=int(getattr(settings, "NEXUS_PROVIDER_LIFECYCLE_STALE_SECONDS", 180))
    )
    runtime_ids = list(
        ProviderRuntimeAccount.objects.filter(runtime_integration().maintenance_filter()).filter(
            status=ProviderRuntimeAccount.STATUS_STOPPING,
            updated_at__lt=cutoff,
        ).values_list("id", flat=True)
    )
    stopped = 0
    failed = 0
    for runtime_id in runtime_ids:
        runtime = ProviderRuntimeAccount.objects.filter(runtime_integration().maintenance_filter()).get(id=runtime_id)
        try:
            if runtime.runtime_type != ProviderRuntimeAccount.RUNTIME_DIRECT_API:
                get_provider_runtime_runner().stop(runtime=runtime)
            with transaction.atomic():
                locked = ProviderRuntimeAccount.objects.filter(runtime_integration().maintenance_filter()).select_for_update().get(id=runtime_id)
                if locked.status != ProviderRuntimeAccount.STATUS_STOPPING:
                    continue
                locked.status = ProviderRuntimeAccount.STATUS_STOPPED
                locked.last_error = ""
                locked.last_health_check_at = timezone.now()
                locked.save(update_fields=["status", "last_error", "last_health_check_at", "updated_at"])
                locked.model_offers.exclude(status=SoftDeleteModel.STATUS_DELETED).update(
                    health_status=ProviderRuntimeModelOffer.HEALTH_UNHEALTHY,
                    health_reason="Provider Runtime is stopped.",
                    last_health_check_at=locked.last_health_check_at,
                )
                record_runtime_health_check(runtime=locked, status=locked.status, reason="Recovered interrupted stop.")
                stopped += 1
        except Exception as exc:
            ProviderRuntimeAccount.objects.filter(runtime_integration().maintenance_filter()).filter(
                id=runtime_id,
                status=ProviderRuntimeAccount.STATUS_STOPPING,
            ).update(last_error=f"Automatic stop recovery failed: {exc}"[:1024])
            failed += 1
    return {"examined": len(runtime_ids), "stopped": stopped, "failed": failed}


def provider_runtime_login_info(*, request, runtime_id: str) -> dict[str, str]:
    runtime = get_provider_runtime(request=request, runtime_id=runtime_id)
    require_provider_runtime_admin(request=request, tenant=runtime.tenant)
    if runtime.runtime_type != ProviderRuntimeAccount.RUNTIME_DIRECT_API and runtime.container_id.startswith("fake_provider_runtime_"):
        raise ProviderRuntimeError(
            "Provider runtime was started with the fake runner. Set NEXUS_PROVIDER_RUNTIME_RUNNER=docker, restart this runtime, then open login."
        )
    if runtime.runtime_type != ProviderRuntimeAccount.RUNTIME_DIRECT_API and not runtime.internal_login_url:
        raise ProviderRuntimeError("Provider runtime login URL is not available. Start the runtime with a login-capable runner first.")
    login_url = provider_runtime_browser_login_url(runtime=runtime)
    return {
        "runtime_id": str(runtime.id),
        "runtime_type": runtime.runtime_type,
        "login_url": login_url,
        "public_login_path": runtime.public_login_path,
        "status": runtime.status,
    }


def export_provider_runtime_credentials(*, request, runtime_id: str) -> dict[str, str]:
    runtime = get_provider_runtime(request=request, runtime_id=runtime_id)
    require_provider_runtime_admin(request=request, tenant=runtime.tenant)
    if runtime.status != ProviderRuntimeAccount.STATUS_ACTIVE:
        raise ProviderRuntimeError("Only active provider runtimes can export API credentials.")

    runtime_api_base_url = runtime.internal_api_url
    runtime_api_key = ""
    key_source = "runtime_proxy_key"
    if runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_DIRECT_API:
        source = runtime.source_provider_account
        if source is None or not source.encrypted_key:
            raise ProviderRuntimeError("Direct API runtime source key is not available.")
        runtime_api_key = decrypt_secret(source.encrypted_key)
        runtime_api_base_url = runtime.internal_api_url or source.url
        key_source = "source_provider_account"
    elif runtime.encrypted_proxy_api_key:
        runtime_api_key = decrypt_secret(runtime.encrypted_proxy_api_key)
    elif runtime.provider_account and runtime.provider_account.encrypted_key:
        runtime_api_key = decrypt_secret(runtime.provider_account.encrypted_key)
        key_source = "provider_account"

    if not runtime_api_base_url or not runtime_api_key:
        raise ProviderRuntimeError("Provider runtime API URL or key is not available. Start and complete login before exporting.")

    offer = runtime.model_offers.filter(
        status=ProviderRuntimeModelOffer.STATUS_CONFIRMED,
        canonical_model__isnull=False,
    ).select_related("canonical_model").order_by("created_at").first()
    if offer is None:
        raise ProviderRuntimeError("Confirm at least one Runtime Model Offer before exporting credentials.")
    recommended_model = offer.canonical_model.key
    gateway_api_base_url = request.build_absolute_uri("/api/v1/openai/v1").rstrip("/")
    gateway_key, gateway_plaintext_key = create_runtime_export_gateway_key(request=request, runtime=runtime, model=recommended_model)
    env_text = "\n".join(
        [
            f"OPENAI_BASE_URL={gateway_api_base_url}",
            f"OPENAI_API_KEY={gateway_plaintext_key}",
            f"NEXUS_TENANT_ID={runtime.tenant_id}",
            f"NEXUS_RUNTIME_ID={runtime.id}",
            f"NEXUS_RUNTIME_TYPE={runtime.runtime_type}",
            f"NEXUS_RUNTIME_MODEL={recommended_model}",
            f"NEXUS_RUNTIME_DIRECT_BASE_URL={runtime_api_base_url}",
            f"NEXUS_RUNTIME_DIRECT_API_KEY={runtime_api_key}",
            "",
        ]
    )
    curl = "\n".join(
        [
            f"curl {gateway_api_base_url}/chat/completions \\",
            f'  -H "Authorization: Bearer {gateway_plaintext_key}" \\',
            '  -H "Content-Type: application/json" \\',
            f'  -d \'{{"model":"{recommended_model}","messages":[{{"role":"user","content":"hello"}}]}}\'',
        ]
    )
    log_runtime_write(
        request=request,
        runtime=runtime,
        action="providers.runtime.credentials.export",
        metadata={"key_source": key_source, "gateway_api_key_id": str(gateway_key.id)},
    )
    return {
        "runtime_id": str(runtime.id),
        "runtime_name": runtime.name,
        "runtime_type": runtime.runtime_type,
        "status": runtime.status,
        "gateway_api_base_url": gateway_api_base_url,
        "gateway_chat_completions_url": f"{gateway_api_base_url}/chat/completions",
        "gateway_models_url": f"{gateway_api_base_url}/models",
        "gateway_api_key": gateway_plaintext_key,
        "gateway_api_key_id": str(gateway_key.id),
        "gateway_authorization_header": f"Bearer {gateway_plaintext_key}",
        "runtime_api_base_url": runtime_api_base_url,
        "runtime_api_key": runtime_api_key,
        "runtime_authorization_header": f"Bearer {runtime_api_key}",
        "recommended_model": recommended_model,
        "canonical_model": offer.canonical_model.key,
        "upstream_model_id": offer.upstream_model_id,
        "env": env_text,
        "curl": curl,
    }


def create_runtime_export_gateway_key(*, request, runtime: ProviderRuntimeAccount, model: str) -> tuple[Any, str]:
    return issue_provider_export_key(request=request, runtime=runtime, model=model)


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


def _open_no_redirect(request: Request, *, timeout: int | float):
    return build_opener(NoRedirectHandler).open(request, timeout=timeout)


def provider_runtime_browser_login_url(*, runtime: ProviderRuntimeAccount) -> str:
    if runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI:
        return provider_runtime_cliproxyapi_login_url(runtime=runtime)
    if runtime.runtime_type != ProviderRuntimeAccount.RUNTIME_CODEX_PROXY:
        return runtime.internal_login_url
    if not runtime.encrypted_proxy_api_key:
        return runtime.internal_login_url
    proxy_api_key = decrypt_secret(runtime.encrypted_proxy_api_key)
    request = Request(
        runtime.internal_login_url,
        headers={"Authorization": f"Bearer {proxy_api_key}"},
        method="GET",
    )
    timeout = int(getattr(settings, "NEXUS_PROVIDER_REQUEST_TIMEOUT_SECONDS", 60))
    try:
        response = _open_no_redirect(request, timeout=timeout)
        login_url = response.geturl()
        prepare_codex_oauth_callback_relay(runtime=runtime, login_url=login_url)
        return login_url
    except HTTPError as exc:
        if exc.code in {301, 302, 303, 307, 308}:
            location = exc.headers.get("Location")
            if location:
                login_url = urljoin(runtime.internal_login_url, location)
                prepare_codex_oauth_callback_relay(runtime=runtime, login_url=login_url)
                return login_url
        detail = exc.read(300).decode("utf-8", errors="replace")
        raise ProviderRuntimeError(f"Provider runtime login endpoint returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise ProviderRuntimeError(f"Provider runtime login endpoint is not reachable: {exc.reason}") from exc


def provider_runtime_cliproxyapi_login_url(*, runtime: ProviderRuntimeAccount) -> str:
    if not runtime.encrypted_proxy_api_key:
        return runtime.internal_login_url
    proxy_api_key = decrypt_secret(runtime.encrypted_proxy_api_key)
    management_key = cliproxyapi_management_key(proxy_api_key=proxy_api_key)
    request = Request(
        runtime.internal_login_url,
        headers={"X-Management-Key": management_key},
        method="GET",
    )
    timeout = int(getattr(settings, "NEXUS_PROVIDER_REQUEST_TIMEOUT_SECONDS", 60))
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(2000).decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read(500).decode("utf-8", errors="replace")
        raise ProviderRuntimeError(f"CLIProxyAPI login endpoint returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise ProviderRuntimeError(f"CLIProxyAPI login endpoint is not reachable: {exc.reason}") from exc

    try:
        data = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise ProviderRuntimeError("CLIProxyAPI login endpoint returned invalid JSON.") from exc
    login_url = str(data.get("url") or "").strip()
    if not login_url:
        raise ProviderRuntimeError("CLIProxyAPI login endpoint did not return an authorization URL.")
    prepare_codex_oauth_callback_relay(runtime=runtime, login_url=login_url)
    return login_url


def prepare_codex_oauth_callback_relay(*, runtime: ProviderRuntimeAccount, login_url: str) -> None:
    state = parse_qs(urlparse(login_url).query).get("state", [""])[0]
    if not state:
        return
    start_codex_oauth_callback_relay(runtime=runtime)
    register_codex_oauth_relay_state(state=state, runtime=runtime)


def register_codex_oauth_relay_state(*, state: str, runtime: ProviderRuntimeAccount) -> None:
    expires_at = time.time() + int(getattr(settings, "NEXUS_CODEX_OAUTH_RELAY_TTL_SECONDS", 5 * 60))
    with _OAUTH_RELAY_LOCK:
        prune_codex_oauth_relay_states()
        _OAUTH_RELAY_STATES[state] = (str(runtime.id), expires_at)


def start_codex_oauth_callback_relay(*, runtime: ProviderRuntimeAccount | None = None) -> None:
    port = oauth_callback_port_for_runtime(runtime=runtime)
    with _OAUTH_RELAY_LOCK:
        if port in _OAUTH_RELAY_SERVERS:
            return
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), CodexOAuthCallbackRelayHandler)
        except OSError as exc:
            raise ProviderRuntimeError(
                f"Provider OAuth callback port {port} is already in use. Stop the conflicting local process, then retry login."
            ) from exc
        thread = threading.Thread(target=server.serve_forever, name=f"nexus-provider-oauth-relay-{port}", daemon=True)
        thread.start()
        _OAUTH_RELAY_SERVERS[port] = server


def oauth_callback_port_for_runtime(*, runtime: ProviderRuntimeAccount | None) -> int:
    if (
        runtime is not None
        and runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI
        and cliproxyapi_oauth_provider(runtime=runtime) == "anthropic"
    ):
        return int(getattr(settings, "NEXUS_CLAUDE_OAUTH_CALLBACK_PORT", 54545))
    return int(getattr(settings, "NEXUS_CODEX_OAUTH_CALLBACK_PORT", 1455))


def prune_codex_oauth_relay_states() -> None:
    now = time.time()
    expired = [state for state, (_runtime_id, expires_at) in _OAUTH_RELAY_STATES.items() if expires_at <= now]
    for state in expired:
        _OAUTH_RELAY_STATES.pop(state, None)


class CodexOAuthCallbackRelayHandler(BaseHTTPRequestHandler):
    server_version = "NexusCodexOAuthRelay/1.0"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in {"/auth/callback", "/callback"}:
            self.send_error(404, "Not found")
            return
        try:
            relay_codex_oauth_callback(callback_url=self.callback_url())
        except Exception as exc:
            self.write_html(500, codex_oauth_callback_html(success=False, message=str(exc)))
            return
        self.write_html(200, codex_oauth_callback_html(success=True, message="Login completed. You can close this window."))

    def callback_url(self) -> str:
        host = self.headers.get("Host") or f"localhost:{self.server.server_port}"
        return f"http://{host}{self.path}"

    def write_html(self, status_code: int, html: str) -> None:
        payload = html.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:
        return None


def relay_codex_oauth_callback(*, callback_url: str) -> None:
    parsed = urlparse(callback_url)
    state = parse_qs(parsed.query).get("state", [""])[0]
    if not state:
        raise ProviderRuntimeError("OAuth callback is missing state.")
    with _OAUTH_RELAY_LOCK:
        prune_codex_oauth_relay_states()
        runtime_entry = _OAUTH_RELAY_STATES.pop(state, None)
    if runtime_entry is None:
        raise ProviderRuntimeError("OAuth callback state is unknown or expired. Open login again.")

    runtime_id, _expires_at = runtime_entry
    runtime = ProviderRuntimeAccount.objects.filter(runtime_integration().maintenance_filter()).select_related("source_provider_account", "tenant", "owner", "project").get(id=runtime_id)
    proxy_api_key = decrypt_secret(runtime.encrypted_proxy_api_key) if runtime.encrypted_proxy_api_key else ""
    if not proxy_api_key:
        raise ProviderRuntimeError("Provider runtime proxy key is not available.")
    if runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI:
        callback_relay_url = urljoin(runtime.internal_login_url, "/v0/management/oauth-callback")
        post_cliproxyapi_oauth_callback(
            callback_relay_url=callback_relay_url,
            callback_url=callback_url,
            provider=cliproxyapi_oauth_provider(runtime=runtime),
        )
    else:
        code_relay_url = urljoin(runtime.internal_login_url, "/auth/code-relay")
        post_codex_code_relay(code_relay_url=code_relay_url, callback_url=callback_url, proxy_api_key=proxy_api_key)
    activate_logged_in_runtime(runtime=runtime, proxy_api_key=proxy_api_key, actor=runtime.owner)
    # Login proxies finish their model refresh before the relay call returns.
    # Synchronize the fresh catalog here so Model Offers are available as soon
    # as the browser login completes, without waiting for a later health check.
    refresh_runtime_model_offers(runtime=runtime, actor=runtime.owner)


def post_cliproxyapi_oauth_callback(*, callback_relay_url: str, callback_url: str, provider: str = "codex") -> None:
    payload = json.dumps({"provider": provider, "redirect_url": callback_url}).encode("utf-8")
    request = Request(
        callback_relay_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=int(getattr(settings, "NEXUS_PROVIDER_REQUEST_TIMEOUT_SECONDS", 60))) as response:
            body = response.read(1000).decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read(500).decode("utf-8", errors="replace")
        raise ProviderRuntimeError(f"CLIProxyAPI OAuth callback relay returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise ProviderRuntimeError(f"CLIProxyAPI OAuth callback relay is not reachable: {exc.reason}") from exc
    try:
        data = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise ProviderRuntimeError("CLIProxyAPI OAuth callback relay returned invalid JSON.") from exc
    if data.get("status") != "ok":
        raise ProviderRuntimeError(str(data.get("error") or "CLIProxyAPI OAuth callback relay failed."))


def post_codex_code_relay(*, code_relay_url: str, callback_url: str, proxy_api_key: str) -> None:
    payload = json.dumps({"callbackUrl": callback_url}).encode("utf-8")
    request = Request(
        code_relay_url,
        data=payload,
        headers={
            "Authorization": f"Bearer {proxy_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=int(getattr(settings, "NEXUS_PROVIDER_REQUEST_TIMEOUT_SECONDS", 60))) as response:
            body = response.read(1000).decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read(500).decode("utf-8", errors="replace")
        raise ProviderRuntimeError(f"Provider runtime code relay returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise ProviderRuntimeError(f"Provider runtime code relay is not reachable: {exc.reason}") from exc
    try:
        data = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise ProviderRuntimeError("Provider runtime code relay returned invalid JSON.") from exc
    if not data.get("success"):
        raise ProviderRuntimeError(str(data.get("error") or "Provider runtime code relay failed."))


@transaction.atomic
def activate_logged_in_runtime(*, runtime: ProviderRuntimeAccount, proxy_api_key: str, actor) -> None:
    if runtime.source_provider_account_id:
        source = runtime.source_provider_account
        if source is not None:
            source.login_status = ProviderAccount.LOGIN_ACTIVE
            source.last_login_error = ""
            source.last_login_at = timezone.now()
            source.save(update_fields=["login_status", "last_login_error", "last_login_at", "updated_at"])
    runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
    runtime.last_error = ""
    runtime.save(update_fields=["status", "last_error", "updated_at"])


def codex_oauth_callback_html(*, success: bool, message: str) -> str:
    event_type = "oauth-callback-success" if success else "oauth-callback-error"
    title = "Login Successful" if success else "Login Failed"
    safe_message = (
        str(message)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>{title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f8fafc; color: #0f172a; display: grid; min-height: 100vh; place-items: center; margin: 0; }}
.card {{ border: 1px solid #cbd5e1; border-radius: 8px; background: white; padding: 24px; max-width: 420px; box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08); }}
h2 {{ margin: 0 0 12px; font-size: 20px; }}
p {{ margin: 0; color: #475569; }}
</style></head>
<body><div class="card"><h2>{title}</h2><p>{safe_message}</p></div>
<script>
if (window.opener) {{ try {{ window.opener.postMessage({{type: "{event_type}"}}, "*"); }} catch (e) {{}} }}
if ({str(success).lower()}) {{ setTimeout(function () {{ try {{ window.close(); }} catch (e) {{}} }}, 700); }}
</script></body></html>"""


def share_provider_runtime(*, request, runtime_id: str, data: dict[str, Any]) -> list[ProviderPoolContribution]:
    return invoke_provider_commerce("share_provider_runtime", request=request, runtime_id=runtime_id, data=data)


def get_provider_runtime_marketplace_offer(*, request, runtime_id: str) -> dict[str, Any]:
    return invoke_provider_commerce("get_provider_runtime_marketplace_offer", request=request, runtime_id=runtime_id)


def unshare_provider_runtime(*, request, runtime_id: str) -> ProviderRuntimeAccount:
    return invoke_provider_commerce("unshare_provider_runtime", request=request, runtime_id=runtime_id)


def login_runtime_if_required(*, request, runtime: ProviderRuntimeAccount) -> str:
    source = runtime.source_provider_account
    if source is None:
        return "active"
    credential_runtime = (
        source.auth_mode == ProviderAccount.AUTH_USERNAME_PASSWORD_LOGIN
        or (
            source.auth_mode == ProviderAccount.AUTH_INTERACTIVE_LOGIN
            and runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI
            and source.encrypted_username
            and source.encrypted_password
        )
    )
    if source.auth_mode == ProviderAccount.AUTH_INTERACTIVE_LOGIN and not credential_runtime:
        source.login_status = ProviderAccount.LOGIN_REQUIRED
        source.last_login_error = "Interactive login is required."
        source.save(update_fields=["login_status", "last_login_error", "updated_at"])
        runtime.last_error = "Interactive login is required."
        return "login_required"
    if not credential_runtime:
        return "active"

    username = decrypt_secret(source.encrypted_username)
    password = decrypt_secret(source.encrypted_password)
    if not username or not password:
        source.login_status = ProviderAccount.LOGIN_REQUIRED
        source.last_login_error = "username and password are required."
        source.save(update_fields=["login_status", "last_login_error", "updated_at"])
        runtime.last_error = "username and password are required."
        return "login_required"

    source.login_status = ProviderAccount.LOGIN_LOGGING_IN
    source.last_login_error = ""
    source.save(update_fields=["login_status", "last_login_error", "updated_at"])
    result = get_provider_runtime_runner().login_with_credentials(runtime=runtime, username=username, password=password)
    if result.success:
        source.login_status = ProviderAccount.LOGIN_ACTIVE
        source.last_login_error = ""
        source.last_login_at = timezone.now()
        source.save(update_fields=["login_status", "last_login_error", "last_login_at", "updated_at"])
        runtime.last_error = ""
        return "active"
    message = redact_secret_text(result.reason, username=username, password=password)
    if result.login_required:
        source.login_status = ProviderAccount.LOGIN_REQUIRED
        source.last_login_error = message[:512]
        source.save(update_fields=["login_status", "last_login_error", "updated_at"])
        runtime.last_error = message[:1024]
        return "login_required"
    source.login_status = ProviderAccount.LOGIN_FAILED
    source.last_login_error = message[:512]
    source.save(update_fields=["login_status", "last_login_error", "updated_at"])
    runtime.last_error = message[:1024]
    return "failed"


def resolve_source_provider_account(*, tenant: Tenant, account_id) -> ProviderAccount | None:
    if not account_id:
        return None
    account = ProviderAccount.objects.filter(tenant=tenant, id=account_id, status=SoftDeleteModel.STATUS_ACTIVE).first()
    if account is None:
        raise ProviderRuntimeNotFound("Source provider account not found.")
    runtime_integration().validate_source_account(account=account, tenant=tenant)
    if account.auth_mode == ProviderAccount.AUTH_USERNAME_PASSWORD_LOGIN and (
        not account.encrypted_username or not account.encrypted_password
    ):
        raise ProviderRuntimeError("Source provider account requires username and password.")
    return account


def validate_runtime_account_binding(*, runtime_type: str, source_account: ProviderAccount | None) -> None:
    if runtime_type == ProviderRuntimeAccount.RUNTIME_DIRECT_API:
        if source_account is None:
            raise ProviderRuntimeError("Direct API runtime requires an API key source account.")
        if source_account.auth_mode != ProviderAccount.AUTH_API_KEY:
            raise ProviderRuntimeError("Direct API runtime can only use api_key provider accounts.")
        if not source_account.encrypted_key:
            raise ProviderRuntimeError("Direct API source account requires an API key.")
        return
    if source_account is not None and source_account.auth_mode == ProviderAccount.AUTH_API_KEY:
        raise ProviderRuntimeError("API key provider accounts must use the direct_api runtime type.")


def runtime_name_for_source_account(*, account: ProviderAccount, runtime_type: str) -> str:
    return f"{account.name or account.account_id}-{runtime_type.replace('_', '-')}"


def ensure_provider_account_has_no_runtime(
    *, source_account: ProviderAccount, exclude_runtime_id=None
) -> None:
    runtimes = ProviderRuntimeAccount.objects.filter(source_provider_account=source_account).exclude(
        status=SoftDeleteModel.STATUS_DELETED
    )
    if exclude_runtime_id is not None:
        runtimes = runtimes.exclude(id=exclude_runtime_id)
    if runtimes.exists():
        raise ProviderRuntimeError("This Provider Account already has a Runtime. Each Account can own only one Runtime.")


def redact_secret_text(message: str, *, username: str = "", password: str = "") -> str:
    text = str(message or "")
    if username:
        text = text.replace(username, "[REDACTED_USERNAME]")
    if password:
        text = text.replace(password, "[REDACTED_PASSWORD]")
    return text


def ensure_pool_contribution(
    *, runtime: ProviderRuntimeAccount, offer: ProviderRuntimeModelOffer, defaults: dict[str, Any] | None = None
) -> ProviderPoolContribution:
    return invoke_provider_commerce("ensure_pool_contribution", runtime=runtime, offer=offer, defaults=defaults)


PROVIDER_USAGE_QUANTUM = Decimal("0.000001")
PROVIDER_USAGE_EXACT_QUANTUM = Decimal("0.000000000001")


def provider_pricing_snapshot(contribution: ProviderPoolContribution) -> dict[str, Decimal]:
    return invoke_provider_commerce("provider_pricing_snapshot", contribution)


def provider_usage_raw_amount(
    *,
    contribution: ProviderPoolContribution,
    request_tokens: int,
    response_tokens: int,
    cached_input_tokens: int = 0,
    reasoning_output_tokens: int = 0,
) -> tuple[Decimal, dict[str, Decimal]]:
    return invoke_provider_commerce(
        "provider_usage_raw_amount", contribution=contribution,
        request_tokens=request_tokens, response_tokens=response_tokens,
        cached_input_tokens=cached_input_tokens, reasoning_output_tokens=reasoning_output_tokens,
    )


def settle_provider_usage_charge(
    *,
    consumer_tenant: Tenant,
    contribution: ProviderPoolContribution,
    request_tokens: int,
    response_tokens: int,
    cached_input_tokens: int = 0,
    reasoning_output_tokens: int = 0,
    amount_override: Decimal | None = None,
) -> dict[str, Any]:
    return invoke_provider_commerce(
        "settle_provider_usage_charge", consumer_tenant=consumer_tenant, contribution=contribution,
        request_tokens=request_tokens, response_tokens=response_tokens,
        cached_input_tokens=cached_input_tokens, reasoning_output_tokens=reasoning_output_tokens,
        amount_override=amount_override,
    )


def reserve_provider_capacity(
    *,
    contribution: ProviderPoolContribution,
    request_id: str,
    reserved_tokens: int,
) -> ProviderCapacityReservation | None:
    return invoke_provider_commerce("reserve_provider_capacity", contribution=contribution, request_id=request_id, reserved_tokens=reserved_tokens)


def release_provider_capacity(*, reservation: ProviderCapacityReservation | None) -> None:
    return invoke_provider_commerce("release_provider_capacity", reservation=reservation)


def mark_provider_capacity_used(
    *,
    reservation: ProviderCapacityReservation | None,
    actual_tokens: int,
) -> None:
    return invoke_provider_commerce("mark_provider_capacity_used", reservation=reservation, actual_tokens=actual_tokens)


def settle_provider_capacity(
    *,
    reservation: ProviderCapacityReservation | None,
    actual_tokens: int,
) -> None:
    return invoke_provider_commerce("settle_provider_capacity", reservation=reservation, actual_tokens=actual_tokens)


def ensure_runtime_provider_account(*, runtime: ProviderRuntimeAccount) -> ProviderAccount:
    if runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_DIRECT_API:
        if runtime.source_provider_account is None:
            raise ProviderRuntimeError("Direct API Runtime source account is unavailable.")
        account = runtime.source_provider_account
    else:
        if not runtime.encrypted_proxy_api_key:
            raise ProviderRuntimeError("Provider Runtime proxy key is unavailable.")
        provider = get_or_create_provider("codex-login")
        account, _ = ProviderAccount.objects.update_or_create(
            tenant=runtime.tenant,
            provider=provider,
            account_id=f"runtime_{runtime.id}",
            defaults={
                "url": runtime.internal_api_url,
                "encrypted_key": runtime.encrypted_proxy_api_key,
                "status": SoftDeleteModel.STATUS_ACTIVE,
                "deleted_at": None,
                "created_by": runtime.owner,
            },
        )
    if runtime.provider_account_id != account.id:
        runtime.provider_account = account
        runtime.save(update_fields=["provider_account", "updated_at"])
    return account


def record_pool_usage(
    *,
    request,
    consumer_tenant: Tenant,
    contribution: ProviderPoolContribution,
    gateway_request_log,
    request_tokens: int,
    response_tokens: int,
    total_tokens: int,
    cached_input_tokens: int = 0,
    reasoning_output_tokens: int = 0,
    settlement: dict[str, Any] | None = None,
    capacity_reservation: ProviderCapacityReservation | None = None,
) -> ProviderPoolUsage | None:
    return invoke_provider_commerce(
        "record_pool_usage", request=request, consumer_tenant=consumer_tenant, contribution=contribution,
        gateway_request_log=gateway_request_log, request_tokens=request_tokens,
        response_tokens=response_tokens, total_tokens=total_tokens,
        cached_input_tokens=cached_input_tokens, reasoning_output_tokens=reasoning_output_tokens,
        settlement=settlement, capacity_reservation=capacity_reservation,
    )


def record_failed_pool_usage(
    *,
    request,
    consumer_tenant: Tenant,
    contribution: ProviderPoolContribution,
    gateway_request_log,
) -> ProviderPoolUsage | None:
    return invoke_provider_commerce(
        "record_failed_pool_usage", request=request, consumer_tenant=consumer_tenant,
        contribution=contribution, gateway_request_log=gateway_request_log,
    )


def runtime_storage_path(*, tenant: Tenant) -> str:
    return (Path(str(tenant.id)) / uuid.uuid4().hex).as_posix()


def resolve_project_from_request(*, request, tenant: Tenant) -> Project | None:
    project_id = getattr(request, "project_id", "")
    if not project_id:
        return None
    project = Project.objects.filter(tenant=tenant, id=project_id, status=SoftDeleteModel.STATUS_ACTIVE).first()
    if project is None:
        raise exceptions.NotFound("Project not found.")
    return project


def require_provider_runtime_admin(*, request, tenant: Tenant) -> None:
    if not has_nexus_permission(request.user, tenant, "admin"):
        raise exceptions.PermissionDenied("Tenant admin permission is required.")


def log_runtime_write(*, request, runtime: ProviderRuntimeAccount, action: str, metadata: dict[str, Any] | None = None) -> None:
    payload = {"runtime_type": runtime.runtime_type, "status": runtime.status}
    payload.update(metadata or {})
    log_audit(
        request=request,
        action=action,
        actor=request.user,
        resource_type="provider_runtime",
        resource_id=runtime.pk,
        metadata=payload,
    )
