from __future__ import annotations

import ast
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import exceptions, status

from apps.audit.services import log_audit
from apps.common.models import SoftDeleteModel
from apps.deployments.models import ModelGroup
from apps.common.authorization import has_nexus_permission
from apps.common.resource_catalog import (
    discoverable_resource_queryset,
    resolve_ownership_project,
)
from apps.tenancy.models import Tenant
from apps.common.request_context import get_tenant_from_request
from .integration import router_integration

from .models import (
    Router,
    RouterChildBinding,
    RouterDeployment,
    RouterModelGroupBinding,
    RouterOutput,
    RouterProviderPreference,
    RouterVersion,
)


class RouterNotFound(exceptions.APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Router not found."
    default_code = "NOT_FOUND"


def list_routers(*, request):
    tenant = get_tenant_from_request(request)
    return discoverable_resource_queryset(
        visible_routers(user=request.user, tenant=tenant),
        request=request, tenant=tenant, resource_type="router",
    ).select_related("tenant", "project").prefetch_related(
        Prefetch("model_group_bindings", queryset=RouterModelGroupBinding.objects.filter(
            status="active", enabled=True, model_group__isnull=False,
        ).order_by("priority", "created_at"), to_attr="catalog_model_group_bindings"),
        Prefetch("outputs", queryset=RouterOutput.objects.exclude(status="deleted")
            .select_related("router", "model_group").order_by("-is_default", "created_at"), to_attr="catalog_outputs"),
        Prefetch("child_bindings", queryset=RouterChildBinding.objects.exclude(status="deleted")
            .select_related("router", "child_output", "child_output__router", "child_output__model_group")
            .order_by("exposed_model_name", "priority", "created_at"), to_attr="catalog_child_bindings"),
    ).order_by("-created_at", "-pk")


@transaction.atomic
def create_router(
    *,
    request,
    name: str,
    router_type: str = Router.TYPE_EXECUTION,
    strategy: str = Router.STRATEGY_MANUAL_PRIORITY,
    model_group_ids: list | None = None,
    ownership: dict | None = None,
) -> Router:
    tenant = get_tenant_from_request(request)
    require_router_admin(request=request, tenant=tenant)
    from apps.common.resource_limits import enforce_capability

    enforce_capability(
        tenant=tenant,
        code=(
            "models.aggregation_routers"
            if router_type == Router.TYPE_AGGREGATION
            else "models.execution_routers"
        ),
    )
    project, _ownership_inferred = resolve_ownership_project(request=request, tenant=tenant, ownership=ownership)
    router = Router.objects.create(
        tenant=tenant,
        name=name,
        router_type=router_type,
        project=project,
        strategy=strategy,
        status=Router.STATUS_DRAFT,
        created_by=request_user_or_none(request.user),
    )
    if model_group_ids:
        bind_model_groups(request=request, router_id=str(router.id), model_group_ids=model_group_ids)
    log_write(
        request=request,
        action="routers.create",
        router=router,
        metadata={"name": name, "router_type": router_type, "strategy": strategy, "model_group_ids": [str(value) for value in model_group_ids or []]},
    )
    return router


def get_router(*, request, router_id: str) -> Router:
    tenant = get_tenant_from_request(request)
    router = discoverable_resource_queryset(
        visible_routers(user=request.user, tenant=tenant),
        request=request, tenant=tenant, resource_type="router",
    ).select_related("project").filter(id=router_id).first()
    if router is None:
        raise RouterNotFound()
    return router


def get_router_source(*, request, router_id: str) -> dict[str, Any]:
    router = get_router(request=request, router_id=router_id)
    deployed_version = current_deployed_router_version(router=router)
    selected_version = deployed_version or current_uploaded_router_version(router=router)
    if selected_version is None:
        return {
            "router_id": str(router.id),
            "has_source": False,
            "source": "",
            "version": "",
            "version_status": "",
            "is_deployed": False,
            "is_runtime_source": False,
            "source_kind": "none",
            "file_name": "",
            "file_size": 0,
        }
    source = read_router_version_source(version=selected_version)
    is_runtime_source = bool(deployed_version and selected_version.id == deployed_version.id)
    return {
        "router_id": str(router.id),
        "has_source": True,
        "source": source,
        "version": selected_version.version,
        "version_status": selected_version.status,
        "is_deployed": selected_version.status == RouterVersion.STATUS_DEPLOYED,
        "is_runtime_source": is_runtime_source,
        "source_kind": "deployed" if is_runtime_source else "draft",
        "file_name": selected_version.file_name,
        "file_size": selected_version.file_size,
    }


@transaction.atomic
def update_router(*, request, router_id: str, data: dict[str, Any]) -> Router:
    router = get_mutable_router(request=request, router_id=router_id)
    update_fields = ["updated_at"]
    if "name" in data:
        router.name = data["name"]
        update_fields.append("name")
    if "strategy" in data:
        strategy = data["strategy"]
        if strategy == Router.STRATEGY_CUSTOM:
            version = current_deployed_router_version(router=router)
            if version is None:
                raise exceptions.ValidationError("Deploy router.py before enabling Custom policy.")
            ensure_router_file_exists(version=version)
        router.strategy = strategy
        update_fields.append("strategy")
    if "model_group_ids" in data:
        validate_model_groups(tenant=router.tenant, model_group_ids=data["model_group_ids"])
    router.save(update_fields=update_fields)
    if "model_group_ids" in data:
        bind_model_groups(
            request=request,
            router_id=str(router.id),
            model_group_ids=data["model_group_ids"],
        )
    log_write(request=request, action="routers.update", router=router, metadata={"fields": sorted(data.keys())})
    return router


@transaction.atomic
def upload_router_file(*, request, router_id: str, uploaded_file) -> RouterVersion:
    router = get_mutable_router(request=request, router_id=router_id)
    if router.router_type != Router.TYPE_EXECUTION:
        raise exceptions.ValidationError("Custom router.py policies are supported only by Execution Routers.")
    if uploaded_file is None:
        raise exceptions.ValidationError("file is required.")
    if Path(uploaded_file.name).name != "router.py":
        raise exceptions.ValidationError("Only router.py is supported.")
    validate_router_upload(uploaded_file=uploaded_file)
    version_number = router.versions.count() + 1
    version_name = f"v{version_number}"
    storage_path = save_uploaded_file(router=router, uploaded_file=uploaded_file, version=version_name)
    router_version = RouterVersion.objects.create(
        router=router,
        version=version_name,
        router_file_path=storage_path,
        file_name=Path(uploaded_file.name).name,
        file_size=int(getattr(uploaded_file, "size", 0) or 0),
        created_by=request_user_or_none(request.user),
    )
    router.current_version = router_version.version
    router.status = Router.STATUS_DRAFT
    router.save(update_fields=["current_version", "status", "updated_at"])
    log_write(
        request=request,
        action="routers.upload",
        router=router,
        metadata={"version": router_version.version, "file_name": router_version.file_name},
    )
    return router_version


@transaction.atomic
def deploy_router(*, request, router_id: str) -> RouterDeployment:
    router = get_mutable_router(request=request, router_id=router_id)
    router_integration().validate_deployment(request=request, router=router)
    if router.router_type == Router.TYPE_EXECUTION:
        for binding in router.model_group_bindings.filter(
            status=SoftDeleteModel.STATUS_ACTIVE,
            enabled=True,
            model_group__isnull=False,
        ).select_related("model_group"):
            ensure_router_output(
                router=router,
                group=binding.model_group,
                is_default=not router.outputs.filter(status=SoftDeleteModel.STATUS_ACTIVE, enabled=True).exists(),
            )
    child_bindings = list(
        router.child_bindings.filter(status=SoftDeleteModel.STATUS_ACTIVE, enabled=True)
        .select_related("child_output", "child_output__router", "child_output__model_group")
        .order_by("priority", "created_at")
    )
    if router.router_type == Router.TYPE_AGGREGATION:
        if not child_bindings:
            raise exceptions.ValidationError("Add at least one Execution Router mapping before deploying an Aggregation Router.")
        validate_aggregation_bindings(router=router, bindings=child_bindings)
    elif child_bindings:
        raise exceptions.ValidationError("Execution Routers cannot contain child Router outputs.")
    version = router.versions.filter(version=router.current_version).first() or router.versions.order_by("-created_at").first()
    if version is not None:
        ensure_router_file_exists(version=version)
        version.status = RouterVersion.STATUS_DEPLOYED
        version.save(update_fields=["status", "updated_at"])
    elif router.strategy == Router.STRATEGY_CUSTOM:
        raise exceptions.ValidationError("Upload router.py before deploying a Custom policy.")
    elif router.router_type == Router.TYPE_EXECUTION and not router.model_group_bindings.filter(
        status=SoftDeleteModel.STATUS_ACTIVE,
        enabled=True,
        model_group__isnull=False,
    ).exists():
        raise exceptions.ValidationError("Add at least one Model Pool before deploying an Execution Router.")
    deployment = RouterDeployment.objects.create(
        router=router,
        version=version,
        status=RouterDeployment.STATUS_ACTIVE,
        endpoint_url=f"/api/v1/routers/{router.id}/invoke/",
        deployed_by=request_user_or_none(request.user),
    )
    router.status = Router.STATUS_DEPLOYED
    if version is not None:
        router.current_version = version.version
    router.save(update_fields=["status", "current_version", "updated_at"])
    log_write(
        request=request,
        action="routers.deploy",
        router=router,
        metadata={"version": version.version if version else "built-in", "strategy": router.strategy},
    )
    return deployment


@transaction.atomic
def delete_router(*, request, router_id: str) -> None:
    router = get_mutable_router(request=request, router_id=router_id)
    now = timezone.now()
    router.deployments.exclude(status=SoftDeleteModel.STATUS_DELETED).update(
        status=SoftDeleteModel.STATUS_DELETED,
        deleted_at=now,
        updated_at=now,
    )
    router.model_group_bindings.exclude(status=SoftDeleteModel.STATUS_DELETED).update(
        status=SoftDeleteModel.STATUS_DELETED,
        deleted_at=now,
        updated_at=now,
    )
    router.outputs.exclude(status=SoftDeleteModel.STATUS_DELETED).update(
        status=SoftDeleteModel.STATUS_DELETED,
        deleted_at=now,
        updated_at=now,
    )
    router.child_bindings.exclude(status=SoftDeleteModel.STATUS_DELETED).update(
        status=SoftDeleteModel.STATUS_DELETED,
        deleted_at=now,
        updated_at=now,
    )
    log_write(request=request, action="routers.delete", router=router, metadata={"name": router.name})
    router.delete()


def bind_model_groups(*, request, router_id: str, providers: list[str] | None = None, model_group_ids: list | None = None):
    router = get_mutable_router(request=request, router_id=router_id)
    router_integration().validate_pool_bindings(request=request, router=router, providers=providers, model_group_ids=model_group_ids)
    if router.router_type != Router.TYPE_EXECUTION:
        raise exceptions.ValidationError("Model Pools can only be configured on an Execution Router.")
    if (model_group_ids or providers) and router.child_bindings.filter(
        status=SoftDeleteModel.STATUS_ACTIVE,
        enabled=True,
    ).exists():
        raise exceptions.ValidationError("Aggregation Routers cannot also bind Model Pools. Remove child Router outputs first.")
    validate_model_groups(tenant=router.tenant, model_group_ids=model_group_ids or [])
    bindings = []
    for index, model_group_id in enumerate(model_group_ids or []):
        group = ModelGroup.objects.filter(tenant=router.tenant, id=model_group_id, status=SoftDeleteModel.STATUS_ACTIVE).first()
        if group is None:
            raise exceptions.ValidationError("model_group_ids contains a model group that is not available in this tenant.")
        binding, _ = RouterModelGroupBinding.objects.update_or_create(
            router=router,
            provider_name="*",
            model_group_name=group.name,
            defaults={
                "model_group": group,
                "enabled": True,
                "priority": index + 1,
                "weight": 100,
                "status": SoftDeleteModel.STATUS_ACTIVE,
                "deleted_at": None,
            },
        )
        bindings.append(binding)
        ensure_router_output(router=router, group=group, is_default=index == 0)
    offset = len(bindings)
    for index, provider_value in enumerate(providers or []):
        provider_name, model_group_name = parse_provider_value(provider_value)
        group = ModelGroup.objects.filter(tenant=router.tenant, name=model_group_name, status=SoftDeleteModel.STATUS_ACTIVE).first()
        binding, _ = RouterModelGroupBinding.objects.update_or_create(
            router=router,
            provider_name=provider_name,
            model_group_name=model_group_name,
            defaults={
                "model_group": group,
                "enabled": True,
                "priority": offset + index + 1,
                "weight": 100,
                "status": SoftDeleteModel.STATUS_ACTIVE,
                "deleted_at": None,
            },
        )
        bindings.append(binding)
    if model_group_ids is not None and not providers:
        selected_ids = [binding.id for binding in bindings]
        stale = router.model_group_bindings.filter(status=SoftDeleteModel.STATUS_ACTIVE)
        if selected_ids:
            stale = stale.exclude(id__in=selected_ids)
        stale.update(status=SoftDeleteModel.STATUS_DELETED, deleted_at=timezone.now(), updated_at=timezone.now())
        selected_group_ids = [binding.model_group_id for binding in bindings if binding.model_group_id]
        stale_outputs = router.outputs.filter(status=SoftDeleteModel.STATUS_ACTIVE, enabled=True)
        if selected_group_ids:
            stale_outputs = stale_outputs.exclude(model_group_id__in=selected_group_ids)
        stale_outputs.update(enabled=False, updated_at=timezone.now())
    log_write(
        request=request,
        action="routers.model_groups.bind",
        router=router,
        metadata={"providers": providers or [], "model_group_ids": [str(value) for value in model_group_ids or []]},
    )
    return bindings


def ensure_router_output(*, router: Router, group: ModelGroup, is_default: bool = False) -> RouterOutput:
    output = router.outputs.filter(model_group=group).order_by("created_at").first()
    if output is None:
        output = RouterOutput.objects.create(
            router=router,
            model_group=group,
            model_name=group.name,
            enabled=True,
            is_default=is_default,
        )
    else:
        output.model_group = group
        output.enabled = True
        output.status = SoftDeleteModel.STATUS_ACTIVE
        output.deleted_at = None
        if is_default:
            output.is_default = True
        output.save(update_fields=["model_group", "enabled", "is_default", "status", "deleted_at", "updated_at"])
    if output.is_default:
        router.outputs.exclude(id=output.id).update(is_default=False, updated_at=timezone.now())
    return output


def list_router_outputs(*, request, router_id: str):
    router = get_router(request=request, router_id=router_id)
    return router.outputs.exclude(status=SoftDeleteModel.STATUS_DELETED).select_related("router", "model_group").order_by(
        "-is_default", "created_at"
    )


@transaction.atomic
def update_router_output(*, request, router_id: str, output_id: str, data: dict[str, Any]) -> RouterOutput:
    router = get_mutable_router(request=request, router_id=router_id)
    if router.router_type != Router.TYPE_EXECUTION:
        raise exceptions.ValidationError("Only an Execution Router exports Model Pool outputs.")
    output = router.outputs.exclude(status=SoftDeleteModel.STATUS_DELETED).filter(id=output_id).first()
    if output is None:
        raise RouterNotFound("Router output not found.")
    for field in ("model_name", "description", "enabled", "is_default"):
        if field in data:
            setattr(output, field, data[field])
    if data.get("is_default"):
        router.outputs.exclude(id=output.id).update(is_default=False, updated_at=timezone.now())
    try:
        output.save()
    except IntegrityError as exc:
        raise exceptions.ValidationError("This Router already exports that model name.") from exc
    log_write(
        request=request,
        action="routers.outputs.update",
        router=router,
        metadata={"output_id": str(output.id), "fields": sorted(data.keys())},
    )
    return output


def list_router_child_bindings(*, request, router_id: str):
    router = get_router(request=request, router_id=router_id)
    return (
        router.child_bindings.exclude(status=SoftDeleteModel.STATUS_DELETED)
        .select_related("router", "child_output", "child_output__router", "child_output__model_group")
        .order_by("exposed_model_name", "priority", "created_at")
    )


def execution_router_mapping_output(*, outputs: list[RouterOutput]) -> RouterOutput | None:
    """Resolve the single stable model output an Execution Router exposes upstream."""

    defaults = [output for output in outputs if output.is_default]
    if len(defaults) == 1:
        return defaults[0]
    if len(outputs) == 1:
        return outputs[0]
    return None


def list_aggregation_candidates(*, request, router_id: str) -> list[dict[str, Any]]:
    router = get_mutable_router(request=request, router_id=router_id)
    if router.router_type != Router.TYPE_AGGREGATION:
        raise exceptions.ValidationError("Aggregation candidates are only available for an Aggregation Router.")
    bound_output_ids = {
        str(value)
        for value in router.child_bindings.filter(status=SoftDeleteModel.STATUS_ACTIVE, enabled=True).values_list(
            "child_output_id", flat=True
        )
    }
    bound_router_names = {
        str(router_id): exposed_model_name
        for router_id, exposed_model_name in router.child_bindings.filter(
            status=SoftDeleteModel.STATUS_ACTIVE,
            enabled=True,
        ).values_list("child_output__router_id", "exposed_model_name")
    }
    candidates = (
        visible_routers(user=request.user, tenant=router.tenant)
        .filter(router_type=Router.TYPE_EXECUTION)
        .prefetch_related("outputs__model_group", "model_group_bindings")
        .order_by("name", "created_at")
    )
    rows: list[dict[str, Any]] = []
    for child in candidates:
        can_use = can_use_router(user=request.user, router=child)
        outputs = list(router_integration().candidate_outputs(request=request, queryset=child.outputs.exclude(status=SoftDeleteModel.STATUS_DELETED).order_by("-is_default", "created_at")))
        mapping_output = execution_router_mapping_output(outputs=outputs)
        models: list[dict[str, Any]] = []
        for output in outputs:
            code, message = aggregation_candidate_output_state(
                user=request.user,
                tenant=router.tenant,
                output=output,
            )
            models.append(
                {
                    "output_id": str(output.id),
                    "model_name": output.model_name,
                    "model_group_name": output.model_group.name if output.model_group_id else "",
                    "available": not code,
                    "code": code,
                    "message": message,
                    "already_bound": str(output.id) in bound_output_ids,
                }
            )
        if not can_use:
            code = "ROUTER_PERMISSION_REQUIRED"
            message = "Router use permission is required before it can be aggregated."
        elif child.status != Router.STATUS_DEPLOYED:
            code = "EXECUTION_ROUTER_NOT_DEPLOYED"
            message = "Deploy this Execution Router before adding it to the catalog."
        elif mapping_output is None:
            code = "EXECUTION_OUTPUT_UNRESOLVED"
            message = (
                "This Router has no resolved model output. Re-select its Model Pools to repair it."
                if not outputs
                else "This Router must expose one default model before it can be aggregated."
            )
        else:
            code, message = aggregation_candidate_output_state(
                user=request.user,
                tenant=router.tenant,
                output=mapping_output,
            )
            if code == "EXECUTION_MODEL_UNAVAILABLE":
                healthy_alternatives = [
                    model["model_name"]
                    for model in models
                    if model["available"] and model["output_id"] != str(mapping_output.id)
                ]
                if healthy_alternatives:
                    message = (
                        f"Default model {mapping_output.model_name} is unavailable. "
                        f"Move {healthy_alternatives[0]} to first position in this Execution Router and apply its Pool order."
                    )
        already_bound = str(child.id) in bound_router_names
        if not code and already_bound:
            code = "EXECUTION_ROUTER_ALREADY_MAPPED"
            message = f"Already mapped as {bound_router_names[str(child.id)]}. Remove that mapping before replacing it."
        rows.append(
            {
                "router_id": str(child.id),
                "name": child.name,
                "status": child.status,
                "available": not code,
                "code": code,
                "message": message,
                "output_id": str(mapping_output.id) if mapping_output else "",
                "model_name": mapping_output.model_name if mapping_output else "",
                "already_bound": already_bound,
                "bound_model_name": bound_router_names.get(str(child.id), ""),
                "models": models,
            }
        )
    return sorted(rows, key=lambda row: (not row["available"], row["name"].lower()))


def aggregation_candidate_output_state(*, user, tenant: Tenant, output: RouterOutput) -> tuple[str, str]:
    """Return the exact eligibility result shared by discovery and binding."""

    from apps.deployments.candidates import output_has_available_source

    child = output.router
    if not can_use_router(user=user, router=child):
        return "ROUTER_PERMISSION_REQUIRED", "Router use permission is required before this output can be aggregated."
    if child.router_type != Router.TYPE_EXECUTION:
        return "EXECUTION_OUTPUT_UNRESOLVED", "Only Execution Router outputs can be added to an Aggregation Router."
    if child.status != Router.STATUS_DEPLOYED:
        return "EXECUTION_ROUTER_NOT_DEPLOYED", "Deploy this Execution Router before using its output."
    if output.status != SoftDeleteModel.STATUS_ACTIVE or not output.enabled or output.model_group_id is None:
        return "EXECUTION_OUTPUT_UNRESOLVED", "Reconnect this output to a real Model Pool in the Execution Router."
    if not output_has_available_source(tenant=tenant, output=output):
        return "EXECUTION_MODEL_UNAVAILABLE", "The backing Model Pool has no available Source."
    return "", ""


def resolve_aggregation_candidate_output(*, request, router: Router, output_id: str) -> RouterOutput:
    visible_child_ids = visible_routers(user=request.user, tenant=router.tenant).filter(
        router_type=Router.TYPE_EXECUTION
    ).values_list("id", flat=True)
    output = (
        RouterOutput.objects.select_related("router", "model_group")
        .filter(id=output_id, router_id__in=visible_child_ids)
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .first()
    )
    if output is None:
        raise exceptions.ValidationError("The selected Execution Router output is unavailable in this tenant.")
    mapping_outputs = list(
        output.router.outputs.exclude(status=SoftDeleteModel.STATUS_DELETED)
        .select_related("router", "model_group")
        .order_by("-is_default", "created_at")
    )
    mapping_output = execution_router_mapping_output(outputs=mapping_outputs)
    if mapping_output is None or mapping_output.id != output.id:
        raise exceptions.ValidationError(
            "Select the Execution Router's single default model output; Aggregation does not route between its outputs."
        )
    code, message = aggregation_candidate_output_state(
        user=request.user,
        tenant=router.tenant,
        output=output,
    )
    if code == "ROUTER_PERMISSION_REQUIRED":
        raise exceptions.PermissionDenied(message)
    if code:
        raise exceptions.ValidationError(message)
    return output


@transaction.atomic
def add_router_child_binding(*, request, router_id: str, data: dict[str, Any]) -> RouterChildBinding:
    router = get_mutable_router(request=request, router_id=router_id)
    router = Router.objects.select_for_update().get(id=router.id)
    if router.router_type != Router.TYPE_AGGREGATION:
        raise exceptions.ValidationError("Child Router outputs can only be configured on an Aggregation Router.")
    if router.model_group_bindings.filter(status=SoftDeleteModel.STATUS_ACTIVE, enabled=True).exists():
        raise exceptions.ValidationError("Remove this Router's Model Pools before adding child Router outputs.")
    child_output = resolve_aggregation_candidate_output(
        request=request,
        router=router,
        output_id=str(data["child_output_id"]),
    )
    if child_output.router_id == router.id:
        raise exceptions.ValidationError("A Router cannot aggregate its own output.")
    if child_output.router.router_type != Router.TYPE_EXECUTION:
        raise exceptions.ValidationError("An Aggregation Router can only use Execution Router outputs.")
    if child_output.router.status != Router.STATUS_DEPLOYED:
        raise exceptions.ValidationError("Deploy the child Router before aggregating its output.")
    if child_output.router.child_bindings.filter(status=SoftDeleteModel.STATUS_ACTIVE, enabled=True).exists():
        raise exceptions.ValidationError("Router nesting is limited to two levels; an Aggregation Router cannot be a child.")
    if child_output.model_group_id is None:
        raise exceptions.ValidationError("The child Router output is not connected to a Model Pool.")
    active_bindings = router.child_bindings.filter(
        status=SoftDeleteModel.STATUS_ACTIVE,
        enabled=True,
    )
    if active_bindings.filter(exposed_model_name=data["exposed_model_name"]).exists():
        raise exceptions.ValidationError("This Aggregation model name is already mapped to an Execution Router.")
    if active_bindings.filter(child_output__router=child_output.router).exists():
        raise exceptions.ValidationError("This Execution Router is already mapped in this Aggregation Router.")
    priority = data.get("priority")
    if priority is None:
        last_priority = (
            router.child_bindings.filter(
                status=SoftDeleteModel.STATUS_ACTIVE,
                enabled=True,
                exposed_model_name=data["exposed_model_name"],
            )
            .order_by("-priority")
            .values_list("priority", flat=True)
            .first()
        )
        priority = int(last_priority or 0) + 1
    binding, _ = RouterChildBinding.objects.update_or_create(
        router=router,
        exposed_model_name=data["exposed_model_name"],
        child_output=child_output,
        defaults={
            "enabled": True,
            "priority": priority,
            "weight": data.get("weight", 100),
            "status": SoftDeleteModel.STATUS_ACTIVE,
            "deleted_at": None,
        },
    )
    log_write(
        request=request,
        action="routers.children.add",
        router=router,
        metadata={
            "binding_id": str(binding.id),
            "model": binding.exposed_model_name,
            "child_router_id": str(child_output.router_id),
            "child_output_id": str(child_output.id),
        },
    )
    return binding


@transaction.atomic
def update_router_child_binding(
    *, request, router_id: str, binding_id: str, data: dict[str, Any]
) -> RouterChildBinding:
    router = get_mutable_router(request=request, router_id=router_id)
    router = Router.objects.select_for_update().get(id=router.id)
    binding = router.child_bindings.exclude(status=SoftDeleteModel.STATUS_DELETED).filter(id=binding_id).first()
    if binding is None:
        raise RouterNotFound("Child Router binding not found.")
    next_enabled = data.get("enabled", binding.enabled)
    next_model_name = data.get("exposed_model_name", binding.exposed_model_name)
    if next_enabled:
        other_active = router.child_bindings.filter(
            status=SoftDeleteModel.STATUS_ACTIVE,
            enabled=True,
        ).exclude(id=binding.id)
        if other_active.filter(exposed_model_name=next_model_name).exists():
            raise exceptions.ValidationError("This Aggregation model name is already mapped to an Execution Router.")
        if other_active.filter(child_output__router=binding.child_output.router).exists():
            raise exceptions.ValidationError("This Execution Router is already mapped in this Aggregation Router.")
    for field in ("exposed_model_name", "enabled", "priority", "weight"):
        if field in data:
            setattr(binding, field, data[field])
    try:
        binding.save()
    except IntegrityError as exc:
        raise exceptions.ValidationError("This child Router output is already mapped to that model name.") from exc
    log_write(
        request=request,
        action="routers.children.update",
        router=router,
        metadata={"binding_id": str(binding.id), "fields": sorted(data.keys())},
    )
    return binding


@transaction.atomic
def remove_router_child_binding(*, request, router_id: str, binding_id: str) -> None:
    router = get_mutable_router(request=request, router_id=router_id)
    binding = router.child_bindings.exclude(status=SoftDeleteModel.STATUS_DELETED).filter(id=binding_id).first()
    if binding is None:
        raise RouterNotFound("Child Router binding not found.")
    binding.delete()
    log_write(
        request=request,
        action="routers.children.remove",
        router=router,
        metadata={"binding_id": str(binding.id)},
    )


def validate_aggregation_bindings(*, router: Router, bindings: list[RouterChildBinding]) -> None:
    if router.router_type != Router.TYPE_AGGREGATION:
        raise exceptions.ValidationError("Child Router outputs require an Aggregation Router.")
    if router.model_group_bindings.filter(status=SoftDeleteModel.STATUS_ACTIVE, enabled=True).exists():
        raise exceptions.ValidationError("A Router cannot combine local Model Pools with child Router outputs.")
    seen_model_names: set[str] = set()
    seen_child_router_ids: set[str] = set()
    for binding in bindings:
        output = binding.child_output
        if binding.exposed_model_name in seen_model_names:
            raise exceptions.ValidationError("Each Aggregation model name must map to exactly one Execution Router.")
        child_router_id = str(output.router_id)
        if child_router_id in seen_child_router_ids:
            raise exceptions.ValidationError("Each Execution Router can be mapped only once in an Aggregation Router.")
        seen_model_names.add(binding.exposed_model_name)
        seen_child_router_ids.add(child_router_id)
        if output.router_id == router.id:
            raise exceptions.ValidationError("A Router cannot aggregate its own output.")
        if output.status != SoftDeleteModel.STATUS_ACTIVE or not output.enabled or output.model_group_id is None:
            raise exceptions.ValidationError(f"Child output {binding.exposed_model_name} is unavailable.")
        if output.router.status != Router.STATUS_DEPLOYED:
            raise exceptions.ValidationError(f"Child Router for {binding.exposed_model_name} is not deployed.")
        if output.router.router_type != Router.TYPE_EXECUTION:
            raise exceptions.ValidationError("Router nesting is limited to two levels.")
        mapping_output = execution_router_mapping_output(
            outputs=list(
                output.router.outputs.exclude(status=SoftDeleteModel.STATUS_DELETED)
                .order_by("-is_default", "created_at")
            )
        )
        if mapping_output is None or mapping_output.id != output.id:
            raise exceptions.ValidationError(
                "Aggregation mappings must use each Execution Router's single default model output."
            )


def validate_model_groups(*, tenant: Tenant, model_group_ids: list) -> None:
    return router_integration().validate_model_groups(tenant=tenant, model_group_ids=model_group_ids)


def list_router_traces(*, request, router_id: str, limit: int = 20, cursor: str = "") -> dict[str, Any]:
    from .traces import trace_backend
    return trace_backend().list_router_traces(request=request, router_id=router_id, limit=limit, cursor=cursor)


def serialize_router_trace(*, log) -> dict[str, Any]:
    from .traces import trace_backend
    return trace_backend().serialize_router_trace(log=log)


def set_policy(*, request, router_id: str, strategy: str) -> Router:
    router = get_mutable_router(request=request, router_id=router_id)
    if strategy == Router.STRATEGY_CUSTOM:
        version = current_deployed_router_version(router=router)
        if version is None:
            raise exceptions.ValidationError("Deploy router.py before enabling custom strategy.")
        ensure_router_file_exists(version=version)
    router.strategy = strategy
    router.save(update_fields=["strategy", "updated_at"])
    log_write(request=request, action="routers.policy.set", router=router, metadata={"strategy": strategy})
    return router


def set_pricing(*, request, router_id: str, plan_id: str, pricing_json: dict[str, Any] | None = None) -> Any:
    return router_integration().set_pricing(request=request, router_id=router_id, plan_id=plan_id, pricing_json=pricing_json)


def export_router_credentials(*, request, router_id: str) -> dict[str, Any]:
    return router_integration().export_router_credentials(request=request, router_id=router_id)


def first_router_model_group_name(*, router: Router) -> str:
    model_names = router_output_model_names(router=router)
    if model_names:
        return model_names[0]
    binding = (
        router.model_group_bindings.filter(status=SoftDeleteModel.STATUS_ACTIVE, enabled=True)
        .order_by("priority", "created_at")
        .first()
    )
    if binding is not None:
        return binding.model_group.name if binding.model_group_id else binding.model_group_name
    return router.name


def router_output_model_names(*, router: Router) -> list[str]:
    if router.router_type == Router.TYPE_AGGREGATION:
        child_names = list(
            router.child_bindings.filter(status=SoftDeleteModel.STATUS_ACTIVE, enabled=True)
            .order_by("priority", "created_at")
            .values_list("exposed_model_name", flat=True)
        )
        return list(dict.fromkeys(child_names))
    output_names = list(
        router.outputs.filter(status=SoftDeleteModel.STATUS_ACTIVE, enabled=True)
        .order_by("-is_default", "created_at")
        .values_list("model_name", flat=True)
    )
    if output_names:
        return output_names
    return list(
        dict.fromkeys(
            router.model_group_bindings.filter(status=SoftDeleteModel.STATUS_ACTIVE, enabled=True)
            .order_by("priority", "created_at")
            .values_list("model_group_name", flat=True)
        )
    )


def current_deployed_router_version(*, router: Router) -> RouterVersion | None:
    version = router.versions.filter(version=router.current_version, status=RouterVersion.STATUS_DEPLOYED).first()
    if version is not None:
        return version
    return router.versions.filter(status=RouterVersion.STATUS_DEPLOYED).order_by("-created_at").first()


def current_uploaded_router_version(*, router: Router) -> RouterVersion | None:
    version = router.versions.filter(version=router.current_version).first()
    if version is not None:
        return version
    return router.versions.order_by("-created_at").first()


def list_provider_preferences(*, request, router_id: str):
    get_router(request=request, router_id=router_id)
    # Legacy rows remain readable at the database layer for audit/history, but
    # Router configuration no longer exposes or applies Source-level preferences.
    return RouterProviderPreference.objects.none()


def add_provider_preference(*, request, router_id: str, data: dict[str, Any]) -> RouterProviderPreference:
    get_mutable_router(request=request, router_id=router_id)
    raise exceptions.ValidationError(
        "Router configures Model Pools only. Add the Provider Runtime as a Source, add that Source to a Model Pool, then bind the Model Pool to the Router."
    )


def remove_provider_preference(*, request, router_id: str, preference_id: str) -> RouterProviderPreference:
    router = get_mutable_router(request=request, router_id=router_id)
    preference = RouterProviderPreference.objects.filter(tenant=router.tenant, router=router, id=preference_id).first()
    if preference is None:
        raise exceptions.NotFound("Router provider preference not found.")
    preference.delete()
    log_write(
        request=request,
        action="routers.provider_preference.remove",
        router=router,
        metadata={"preference_id": str(preference.id)},
    )
    return preference


def get_mutable_router(*, request, router_id: str) -> Router:
    router = get_router(request=request, router_id=router_id)
    if not can_manage_router(user=request.user, router=router):
        raise exceptions.PermissionDenied("Router admin permission is required.")
    return router


def can_manage_router(*, user, router: Router) -> bool:
    return router_integration().can_manage_router(user=user, router=router)


def can_use_router(*, user, router: Router) -> bool:
    return router_integration().can_use_router(user=user, router=router)


def visible_routers(*, user, tenant: Tenant):
    return router_integration().visible_routers(user=user, tenant=tenant)


def require_router_admin(*, request, tenant: Tenant) -> None:
    if not has_nexus_permission(request.user, tenant, "admin"):
        raise exceptions.PermissionDenied("Tenant admin permission is required.")


def parse_provider_value(value: str) -> tuple[str, str]:
    text = str(value).strip()
    if not text:
        raise exceptions.ValidationError("provider value cannot be blank.")
    if ":" in text:
        provider_name, model_group_name = text.split(":", 1)
    elif "/" in text:
        provider_name, model_group_name = text.split("/", 1)
    else:
        provider_name = text
        model_group_name = text
    provider_name = provider_name.strip()
    model_group_name = model_group_name.strip()
    if not provider_name or not model_group_name:
        raise exceptions.ValidationError("provider value must include provider and model group.")
    return provider_name, model_group_name


def save_uploaded_file(*, router: Router, uploaded_file, version: str) -> str:
    root = Path(settings.NEXUS_ROUTER_STORAGE_ROOT)
    target_dir = root / str(router.tenant_id) / str(router.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{version}_{uuid.uuid4().hex}_{Path(uploaded_file.name).name}"
    target = target_dir / filename
    with target.open("wb") as handle:
        for chunk in uploaded_file.chunks():
            handle.write(chunk)
    return str(target.relative_to(root)).replace("\\", "/")


def ensure_router_file_exists(*, version: RouterVersion) -> None:
    path = safe_router_version_path(version=version)
    if not path.exists():
        raise exceptions.ValidationError("Uploaded router.py file is missing.")


def read_router_version_source(*, version: RouterVersion) -> str:
    path = safe_router_version_path(version=version)
    if not path.exists():
        raise exceptions.ValidationError("Uploaded router.py file is missing.")
    max_size = 1024 * 1024
    if path.stat().st_size > max_size:
        raise exceptions.ValidationError("router.py is too large to display.")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise exceptions.ValidationError("router.py must be UTF-8 encoded.") from exc


def safe_router_version_path(*, version: RouterVersion) -> Path:
    root = Path(settings.NEXUS_ROUTER_STORAGE_ROOT).resolve()
    path = (root / version.router_file_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise exceptions.ValidationError("Router file path is outside router storage root.") from exc
    return path


def validate_router_upload(*, uploaded_file) -> None:
    try:
        content = uploaded_file.read()
    finally:
        uploaded_file.seek(0)
    if isinstance(content, bytes):
        try:
            source = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise exceptions.ValidationError("router.py must be UTF-8 encoded.") from exc
    else:
        source = str(content)
    try:
        tree = ast.parse(source, filename="router.py")
    except SyntaxError as exc:
        raise exceptions.ValidationError(f"router.py syntax error: {exc.msg}") from exc
    route_def = next((node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "route"), None)
    if route_def is None:
        raise exceptions.ValidationError("router.py must define route(request, candidates, context).")
    if isinstance(route_def, ast.AsyncFunctionDef):
        raise exceptions.ValidationError("route() must be a synchronous function.")
    positional_count = len(route_def.args.posonlyargs) + len(route_def.args.args)
    if positional_count < 3:
        raise exceptions.ValidationError("route() must accept request, candidates, and context.")


def log_write(*, request, action: str, router: Router, metadata: dict[str, Any] | None = None) -> None:
    log_audit(
        request=request,
        action=action,
        actor=request_user_or_none(request.user),
        resource_type="router",
        resource_id=router.pk,
        metadata=metadata or {},
    )


def request_user_or_none(principal):
    user_model = get_user_model()
    return principal if isinstance(principal, user_model) else None
