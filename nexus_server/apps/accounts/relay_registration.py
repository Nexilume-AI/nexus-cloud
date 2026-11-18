"""Bound model provisioning, using the same permission-checked Cloud services.

Only an approved immutable manifest reaches here. No general API delegation,
foreign resource IDs, or user-supplied tenant/owner are accepted from the device.
The caller holds its RelayDevice row lock through registration/revocation.
"""
import hashlib
from rest_framework import exceptions
from apps.common.crypto import decrypt_secret


def register(request, device, spec, key):
    from apps.providers.connection_inputs import ProviderConnectionCreateSerializer
    from apps.providers.connection_services import create_provider_connection, get_provider_connection, connection_runtime
    from apps.providers.runtime_services import start_provider_runtime, refresh_provider_runtime_models
    from apps.providers.models import ProviderAccount, ProviderRuntimeModelOffer
    from apps.deployments.services import create_deployment
    from apps.deployments.models import Deployment, ModelGroup
    from apps.routers.services import create_router, deploy_router, update_router_output, get_router
    from apps.routers.models import RouterOutput

    tag = "relay-sdk-" + hashlib.sha256(f"{device.id}:{spec['model']}".encode()).hexdigest()[:24]
    account = ProviderAccount.objects.filter(tenant_id=device.tenant_id, account_id=tag).first()
    if account is None:
        validator = ProviderConnectionCreateSerializer(data={"name": spec["name"], "account_id": tag,
            "engine": "direct_api", "upstream_provider": spec["protocol"], "url": spec["endpoint"], "key": key})
        validator.is_valid(raise_exception=True)
        account = create_provider_connection(request=request, data=dict(validator.validated_data))
    account = get_provider_connection(request=request, account_id=str(account.id))
    if account.status != "active" or account.url.rstrip("/") != spec["endpoint"].rstrip("/"):
        raise exceptions.ValidationError("Provider changed or disabled; repair explicitly in Cloud.")
    if decrypt_secret(account.encrypted_key) != key:
        raise exceptions.ValidationError("Endpoint credential differs; register again with the original device storage.")
    runtime = connection_runtime(account)
    if runtime.status == "created":
        start_provider_runtime(request=request, runtime_id=str(runtime.id))
        runtime.refresh_from_db()
    if runtime.status != "active":
        raise exceptions.ValidationError("Provider runtime is not active.")
    offer = ProviderRuntimeModelOffer.objects.filter(runtime_account=runtime, upstream_model_id=spec["model"]).first()
    if offer is None:
        refresh_provider_runtime_models(request=request, runtime_id=str(runtime.id))
        offer = ProviderRuntimeModelOffer.objects.filter(runtime_account=runtime, upstream_model_id=spec["model"]).first()
    if offer is None or offer.status in {"disabled", "deleted"} or offer.health_status != "healthy":
        raise exceptions.ValidationError("Approved model is missing or unhealthy in the endpoint catalog.")
    source = Deployment.objects.filter(tenant_id=device.tenant_id, deployment_id=tag).first()
    if source is None:
        source = create_deployment(request=request, data={"source_type": "provider_runtime",
            "deployment_id": tag, "provider_runtime_id": str(runtime.id), "model_offer_id": str(offer.id),
            "model_group_name": tag, "visibility": "private"})
    if source.status != "active" or source.provider_runtime_id != runtime.id or source.runtime_model_offer_id != offer.id:
        raise exceptions.ValidationError("Source was changed or disabled; it was not restored.")
    pool = ModelGroup.objects.get(tenant_id=device.tenant_id, name=tag)
    receipt = device.registrations.get(spec["model"], {})
    if receipt.get("router_id"):
        router = get_router(request=request, router_id=receipt["router_id"])
    else:
        router = create_router(request=request, name=f"{spec['name']} · SDK {str(device.id)[:6]}",
            model_group_ids=[str(pool.id)])
    outputs = list(RouterOutput.objects.filter(router=router).exclude(status="deleted"))
    if len(outputs) != 1 or outputs[0].model_group_id != pool.id:
        raise exceptions.ValidationError("Router bindings changed; refusing to overwrite.")
    if outputs[0].model_name != spec["model"]:
        if outputs[0].model_name != tag:
            raise exceptions.ValidationError("Router output was renamed; refusing to overwrite.")
        update_router_output(request=request, router_id=str(router.id), output_id=str(outputs[0].id), data={"model_name": spec["model"]})
    if router.status == "draft":
        deploy_router(request=request, router_id=str(router.id))
    elif router.status != "deployed":
        raise exceptions.ValidationError("Router is disabled.")
    receipt = {"model": spec["model"], "provider_id": str(account.id), "runtime_id": str(runtime.id),
        "source_id": str(source.id), "pool_id": str(pool.id), "router_id": str(router.id), "status": "registered"}
    device.registrations[spec["model"]] = receipt
    device.save(update_fields=["registrations", "updated_at"])
    return receipt


def disable_sources(device):
    # Only the exact immutable resources created by this grant; no name matching.
    from apps.deployments.models import Deployment
    from apps.providers.models import ProviderAccount, ProviderRuntimeAccount
    # Existing Providers attached for transport are not owned by this grant.
    # Revocation blocks their Relay immediately, without rewriting user resources.
    receipts = [r for r in device.registrations.values() if r.get("source_id") and not r.get("transport")]
    Deployment.objects.filter(tenant_id=device.tenant_id, id__in=[r["source_id"] for r in receipts], status="active").update(status="disabled")
    ProviderAccount.objects.filter(tenant_id=device.tenant_id, id__in=[r["provider_id"] for r in receipts], status="active").update(status="disabled")
    ProviderRuntimeAccount.objects.filter(tenant_id=device.tenant_id, id__in=[r["runtime_id"] for r in receipts], status="active").update(status="stopped")
