"""Owner-scoped operational policy receipts without commercial credentials."""
import re
from django.db import transaction
from rest_framework import exceptions
from apps.audit.services import write_audit_log
from apps.routers.models import RouterRuntimeInvocation, RouterVersion
from .gateway_lifecycle import context, request_id
from .resource_catalog import PersonalResourceCatalog


@transaction.atomic
def log_runtime_invocation(*, request, tenant, router, version, selected_deployment,
                           result, status_value, error_code, error_message):
    user, tenant_id, _ = context(request, tenant)
    catalog = PersonalResourceCatalog()
    current, _ = catalog._resource(request=request, resource_type="router", obj=router)
    if str(current.tenant_id) != tenant_id:
        raise exceptions.NotFound("Router invocation context is unavailable.")
    # Do not accept a forged/stale version object as authority for a receipt.
    current_version = RouterVersion.objects.filter(pk=version.pk, router=current,
        status=RouterVersion.STATUS_DEPLOYED).first()
    if current_version is None:
        raise exceptions.NotFound("Router version is no longer available.")
    source = None
    if selected_deployment is not None:
        source, _ = catalog._resource(request=request, resource_type="deployment", obj=selected_deployment)
    if status_value not in {RouterRuntimeInvocation.STATUS_SUCCESS, RouterRuntimeInvocation.STATUS_FAILED}:
        raise exceptions.ValidationError("Invalid Router invocation status.")
    success = status_value == RouterRuntimeInvocation.STATUS_SUCCESS
    if success and (source is None or error_code or result.error_code):
        raise exceptions.ValidationError("A successful policy decision must select an available Source.")
    code = str(error_code or "ROUTER_RUNTIME_FAILED")
    if re.fullmatch(r"[A-Z0-9_]{1,64}", code) is None:
        code = "ROUTER_RUNTIME_FAILED"
    code = "" if success else code
    # Custom Python can echo the prompt or a secret in an exception. Persist
    # only structured status and a fixed recovery message, never that body.
    message = "" if success else "Custom Router policy failed. Review the policy and retry deliberately."
    invocation = RouterRuntimeInvocation.objects.create(tenant_id=tenant_id, router=current,
        version=current_version, selected_deployment=source, actor=user, status=status_value,
        error_code=code, error_message=message, latency_ms=result.latency_ms,
        exit_code=result.exit_code, request_id=request_id(request))
    write_audit_log(request=request, actor=user, tenant=current.tenant,
        action="routers.runtime.invoke", resource_type="router", resource_id=current.pk,
        after={"router_runtime_invocation_id": str(invocation.pk), "router_version": current_version.version,
               "status": status_value, "selected_deployment_id": str(source.pk) if source else "",
               "error_code": code, "latency_ms": result.latency_ms})
    return invocation
