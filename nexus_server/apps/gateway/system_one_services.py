"""Native decisions over the same Router, Source and accounting machinery."""
import json
import time
import uuid
from datetime import timedelta
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError, PermissionDenied
from apps.audit.services import write_audit_log
from apps.common.request_context import get_tenant_from_request
from .image_services import digest
from .models import GatewayDecisionOperation, GatewayRequestLog
from .provider_adapters import adapter_for_provider, ProviderClientError
from .system_one import PROVIDER_NAMES
from . import services as gateway


class DecisionReplay(APIException):
    status_code = 409
    default_code = "SYSTEM_ONE_ALREADY_DISPATCHED"
    default_detail = "This idempotency key was already admitted. No request was replayed. Use a new key only for an intentional new decision."


class DecisionQuotaExceeded(APIException):
    status_code = 429
    default_code = "API_KEY_QUOTA_EXCEEDED"
    default_detail = "This API key has reached its configured request, token or spending limit."


@transaction.atomic
def admit(*, request, tenant, payload, deployments):
    api_key = getattr(request, "api_key", None)
    personal_key = getattr(request, "_nexus_personal_router_credential_id", None)
    principal = (f"key:{api_key.pk}" if api_key else
                 f"personal-key:{personal_key}" if personal_key else f"user:{request.user.pk}")
    key = request.headers.get("Idempotency-Key") or getattr(request, "request_id", "") or str(uuid.uuid4())
    if not isinstance(key, str) or not 1 <= len(key) <= 128 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValidationError("Idempotency-Key must contain 1–128 printable ASCII characters without spaces.")
    # A tenant row lock works in both editions and serializes same-key admission.
    from apps.tenancy.models import Tenant
    Tenant.objects.select_for_update().get(pk=tenant.pk)
    lookup = {"tenant": tenant, "principal": principal, "key_digest": digest(key.encode())}
    if GatewayDecisionOperation.objects.filter(**lookup).exists():
        raise DecisionReplay()
    tokens = gateway.provider_capacity_token_cap(provider_payload=payload)
    amount = gateway.estimate_gateway_charge(deployments=deployments, provider_payload=payload)
    if api_key is not None:
        # Reload under lock so a revoked/disabled key cannot be admitted from a
        # stale authentication object. No direct commercial imports in core.
        current = type(api_key).objects.select_for_update(of=("self",)).select_related("policy").get(pk=api_key.pk)
        if current.status != "active" or current.deleted_at:
            raise PermissionDenied("API key is no longer active.")
        policy = getattr(current, "policy", None)
        if policy is None or policy.status != "active" or policy.deleted_at:
            raise PermissionDenied("API key policy is no longer active.")
        now = timezone.now()
        logs = GatewayRequestLog.objects.filter(tenant=tenant, api_key=api_key)
        # Completed operations already occur in GatewayRequestLog. Only pending
        # dispatches supplement it; stale pending fences remain non-replayable.
        pending = GatewayDecisionOperation.objects.filter(tenant=tenant, principal=principal,
            state="pending", created_at__gte=now - timedelta(minutes=15))
        recent = logs.filter(created_at__gte=now - timedelta(minutes=1))
        recent_pending = pending.filter(created_at__gte=now - timedelta(minutes=1))
        if policy.rpm and recent.count() + recent_pending.count() >= policy.rpm:
            raise DecisionQuotaExceeded()
        recent_tokens = (recent.aggregate(n=Sum("total_tokens"))["n"] or 0)
        held_tokens = recent_pending.aggregate(n=Sum("reserved_tokens"))["n"] or 0
        if policy.tpm and recent_tokens + held_tokens + tokens > policy.tpm:
            raise DecisionQuotaExceeded()
        month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        spent = logs.filter(created_at__gte=month).aggregate(n=Sum("cost"))["n"] or Decimal("0")
        held = pending.aggregate(n=Sum("reserved_amount"))["n"] or Decimal("0")
        if policy.monthly_budget is not None and spent + held + amount > policy.monthly_budget:
            raise DecisionQuotaExceeded()
    record = GatewayDecisionOperation.objects.create(**lookup, reserved_tokens=tokens, reserved_amount=amount)
    # Keep the middleware's request ID for the public header and audit trail.
    # The dispatch UUID is a separate, durable upstream idempotency identifier.
    return record


def invoke(*, request, payload):
    tenant = get_tenant_from_request(request)
    api_key = getattr(request, "api_key", None)
    payload = {**payload, "_nexus_operation": "systemone"}
    router = deployment = record = reservation = capacity = None
    started = time.monotonic()
    try:
        gateway.gateway_integration().prepare_request(request=request, tenant=tenant, payload=payload)
        gateway.enforce_operation_policy(api_key, "systemone")
        gateway.apply_single_router_key_default(payload=payload, api_key=api_key)
        gateway.enforce_api_key_policy(api_key=api_key, model=payload["model"])
        router = gateway.get_router_for_payload(tenant=tenant, payload=payload)
        gateway.enforce_api_key_router_policy(api_key=api_key, router=router)
        if api_key and api_key.project_id and router and router.project_id not in (None, api_key.project_id):
            raise PermissionDenied("This Router is outside the API key Project.")
        deployments = gateway.resolve_deployment_candidates(request=request, tenant=tenant, payload=payload)
        deployments = [d for d in deployments if d.provider.name in PROVIDER_NAMES]
        if not deployments:
            raise gateway.ModelOperationUnsupported()
        deployment = deployments[0]
        record = admit(request=request, tenant=tenant, payload=payload, deployments=[deployment])
        reservation = gateway.reserve_gateway_charge(request=request, tenant=tenant, deployments=[deployment], provider_payload=payload)
        capacity = gateway.reserve_provider_capacity_for_deployment(request=request, deployment=deployment, provider_payload=payload)
        response = adapter_for_provider(deployment.provider.name).systemone(deployment=deployment, payload=payload, operation_id=str(record.pk))
        usage = response.raw.get("usage") or {}
        trace = gateway.routing_trace_for_deployment(deployment)
        trace["systemone"] = {"questions": len(payload["questions"]), "request_bytes": len(json.dumps(
            {k: payload[k] for k in ("model", "state", "questions")}, ensure_ascii=False).encode()),
            "upstream_model": response.model, "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"), "usage_complete": all(usage.get(k) is not None for k in ("input_tokens", "output_tokens")),
            "token_accounting": "reported_only"}
        deployment._nexus_routing_trace = trace
        cost = gateway.calculate_cost(deployment=deployment, total_tokens=response.total_tokens,
            request_tokens=response.request_tokens, response_tokens=response.response_tokens)
        gateway.mark_provider_capacity_used(reservation=capacity, actual_tokens=response.total_tokens)
        # Settlement and dispatch completion are atomic; a crash cannot cause a
        # second upstream call. Logs contain only metadata, never state/answers.
        with transaction.atomic():
            log = gateway.finalize_gateway_success(request=request, tenant=tenant, api_key=api_key,
                router=router, fallback_count=0, deployment=deployment, requested_model=payload["model"],
                provider_response=response, cost=cost, reservation=reservation, capacity_reservation=capacity)
            GatewayDecisionOperation.objects.filter(pk=record.pk).update(state="succeeded", gateway_log=log)
        reservation = capacity = None
        gateway.mark_deployment_runtime_success(deployment=deployment)
        if deployment.runtime_model_offer_id:
            from apps.providers.models import ProviderRuntimeModelOffer
            from django.utils import timezone
            ProviderRuntimeModelOffer.objects.filter(pk=deployment.runtime_model_offer_id,
                upstream_model_id=deployment.upstream_model_id, status__in=["detected", "confirmed"],
                runtime_account__status="active", metadata__discovery_source="explicit").update(
                    health_status="healthy", health_reason="Native System One inference succeeded.", last_health_check_at=timezone.now())
        gateway.mark_provider_account_available(account=deployment.provider_account, request=request, reason="Native System One request succeeded.")
        gateway.attribute_agent_model_usage(request=request, tenant=tenant, provider_response=response,
            actual_usage=response.raw.get("usage"), event_id=f"gateway:{log.pk}")
        return response.raw
    except (APIException, ProviderClientError) as exc:
        code = getattr(exc, "error_code", None) or getattr(exc, "default_code", "SYSTEM_ONE_FAILED")
        if isinstance(exc, ProviderClientError) and deployment is not None:
            gateway.update_provider_account_quota_from_error(request=request, deployment=deployment, error=exc)
        # Personal accounting requires an admitted execution lease. Permission
        # and replay rejection happen before that lease exists; preserve their
        # original 403/409 instead of replacing them with a receipt lookup 404.
        log = None
        if reservation is not None:
            log = gateway.write_failure_log(request=request, tenant=tenant, api_key=api_key, router=router,
                fallback_count=0, deployment=deployment, model=payload["model"], error_code=code,
                latency_ms=gateway.elapsed_ms(started), cost=Decimal("0"))
            log.operation = "systemone"
            log.save(update_fields=["operation"])
        write_audit_log(request=request, actor=gateway.request_actor_user(request), tenant=tenant,
            action="gateway.systemone.failed", resource_type="gateway_request" if log else "gateway_decision",
            resource_id=log.pk if log else record.pk if record else None,
            after={"model": payload["model"], "error_code": code})
        if record is not None:
            GatewayDecisionOperation.objects.filter(pk=record.pk).update(state="failed", gateway_log=log)
        if isinstance(exc, ProviderClientError):
            raise gateway.ProviderUpstreamError(str(exc), code=code) from None
        raise
    finally:
        if capacity is not None:
            gateway.release_provider_capacity(reservation=capacity)
        if reservation is not None:
            gateway.release_gateway_charge_reservation(reservation=reservation)
