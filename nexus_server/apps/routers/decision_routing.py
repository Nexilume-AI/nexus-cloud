"""System One may rank eligible Pools, never grant access or add a target."""
import time
from cryptography.fernet import InvalidToken
from rest_framework import exceptions
from apps.common.resource_catalog import resource_access_payload
from apps.providers.connection_services import require_connection_runtime
from apps.providers.models import ProviderAccount, ProviderRuntimeAccount, ProviderRuntimeModelOffer
from apps.gateway.system_one import PROVIDER_NAMES, decide
from apps.gateway.provider_adapters import ProviderClientError


def auto_model_name(router):
    return f"nexus-auto-{router.pk}"


def is_auto_output(output):
    return output.model_group_id is None and output.router.strategy == "system_one" and output.model_name == auto_model_name(output.router)


def sync_auto_output(router):
    if router.router_type != "execution":
        return
    name = auto_model_name(router)
    if router.strategy != "system_one":
        router.outputs.filter(model_name=name, model_group__isnull=True).update(enabled=False, is_default=False)
        return
    from .models import RouterOutput
    output, _ = RouterOutput.objects.get_or_create(router=router, model_name=name, defaults={"model_group": None})
    if output.model_group_id is not None:
        raise exceptions.ValidationError("The reserved Auto model name conflicts with a Pool output.")
    output.enabled = True; output.is_default = True; output.status = "active"; output.deleted_at = None
    output.save(update_fields=["enabled", "is_default", "status", "deleted_at"])
    router.outputs.exclude(pk=output.pk).update(is_default=False)


def output_groups(output):
    if not is_auto_output(output):
        return [output.model_group] if output.model_group is not None else []
    return [b.model_group for b in output.router.model_group_bindings.filter(status="active", enabled=True)
            .select_related("model_group") if b.model_group is not None and b.model_group.status == "active"]


def validate_config(*, request, router, config):
    if router.router_type not in ("execution", "aggregation") or not isinstance(config, dict):
        raise exceptions.ValidationError("Invalid Router decision configuration.")
    if router.router_type == 'aggregation' and router.child_bindings.filter(status='active', enabled=True, exposed_model_name='auto').exists():
        raise exceptions.ValidationError('Rename the existing auto mapping before enabling intelligent routing.')
    if not config.get("provider_connection_id") or not config.get("model"):
        raise exceptions.ValidationError("Choose a System One Provider connection and model.")
    account, runtime = require_connection_runtime(request=request, account_id=config["provider_connection_id"])
    if not resource_access_payload(request=request, resource_type="provider_connection", obj=account).get("can_use"):
        raise exceptions.PermissionDenied("Provider use permission is required for decision routing.")
    resolve_provider(router=router, config=config, account=account, runtime=runtime)
    return config


def resolve_provider(*, router, config, account=None, runtime=None):
    if account is None:
        account = ProviderAccount.objects.filter(pk=config.get("provider_connection_id"), tenant_id=router.tenant_id, status="active").select_related("provider").first()
    if account is None or account.tenant_id != router.tenant_id or account.status != "active" or account.provider.name not in PROVIDER_NAMES:
        raise exceptions.ValidationError("An active System One Provider in this Organization is required.")
    if runtime is None:
        rows = list(account.source_runtime_accounts.exclude(status="deleted")[:2])
        runtime = rows[0] if len(rows) == 1 else None
    if runtime is None or runtime.tenant_id != router.tenant_id or runtime.project_id not in (None, router.project_id) or runtime.runtime_type != "direct_api" or runtime.status != "active":
        raise exceptions.ValidationError("The decision Provider must be active and belong to the Router's Project or Organization.")
    offer = ProviderRuntimeModelOffer.objects.filter(runtime_account=runtime, upstream_model_id=config.get("model")).first()
    # A Provider catalog is discovery, not an authoritative inference allowlist.
    # Only an explicit, privileged configuration can name an unlisted model.
    # Existing disabled/deleted/unavailable Offers remain authoritative denials.
    if offer is None and config.get("model_source") == "explicit":
        return account, runtime
    if offer is not None and offer.status not in {"detected", "confirmed"}:
        raise exceptions.ValidationError("This decision model is disabled or unavailable. An explicit ID cannot override its state.")
    if offer is None or "systemone" not in (offer.metadata or {}).get("model_contract", {}).get("operations", []):
        raise exceptions.ValidationError("Choose an advertised System One model or explicitly specify an unlisted model ID.")
    return account, runtime


def select_pools(*, router, pools, payload):
    started = time.monotonic()
    trace = {"algorithm": "system_one", "status": "skipped", "reason": "single_eligible_pool"}
    if not 2 <= len(pools) <= 32:
        trace["reason"] = "single_eligible_pool" if len(pools) < 2 else "candidate_limit"
        return pools, trace
    try:
        config = router.decision_config or {}
        account, runtime = resolve_provider(router=router, config=config)
        from apps.common.crypto import decrypt_secret
        # Text only, bounded latest user intent. Never send tools, tool results,
        # system prompts, images, API keys, full conversation or raw Pool data.
        latest = next((row.get("content") for row in reversed(payload.get("messages", [])) if row.get("role") == "user"), "")
        if isinstance(latest, list):
            latest = "\n".join(part.get("text", "") for part in latest if isinstance(part, dict) and part.get("type") == "text")
        task = str(latest or "")[:4000]
        key = decrypt_secret(account.encrypted_key)
        if not key:
            raise exceptions.ValidationError("Missing decision credential.")
        task = task.replace(key, "[redacted]")
        choices = {f"pool_{i}": {"name": str(pool["group"].display_name or pool["group"].name)[:128],
                                "priority_rank": i + 1, "task_hint": str(pool["binding"].routing_hint)[:128]} for i, pool in enumerate(pools)}
        choice = decide(base_url=runtime.internal_api_url, key=key, model=config["model"], state={"task": task},
                        choices=choices, timeout=min(8, max(.5, config.get("timeout_ms", 8000) / 1000)))
        index = list(choices).index(choice)
        trace.update(status="selected", reason="decision_model", selected_pool_id=str(pools[index]["id"]))
        pools = [pools[index], *pools[:index], *pools[index + 1:]]
    except (ProviderClientError, exceptions.APIException, InvalidToken, ValueError, TypeError, KeyError):
        trace.update(status="fallback", reason="decision_unavailable_or_invalid")
    trace["latency_ms"] = int((time.monotonic() - started) * 1000)
    return pools, trace
