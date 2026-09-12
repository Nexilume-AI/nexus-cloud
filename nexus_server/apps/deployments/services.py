from __future__ import annotations

import uuid
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import exceptions, status

from apps.audit.services import log_audit
from apps.audit.snapshots import snapshot_resource
from apps.common.models import SoftDeleteModel
from apps.common.project_scope import current_project_id, scope_queryset_to_current_project
from apps.common.resource_catalog import discoverable_resource_queryset
from apps.common.authorization import has_nexus_permission
from apps.providers.models import ProviderAccount
from apps.providers.services import get_or_create_provider
from apps.tenancy.models import Project, Tenant
from apps.common.request_context import get_tenant_from_request

from .models import CanonicalModel, Deployment, ModelGroup, ModelGroupDeployment, ModelGroupRoutingRevision
from .health import check_deployment_health
from .integration import deployment_integration


class DeploymentNotFound(exceptions.APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Deployment not found."
    default_code = "NOT_FOUND"


class ManualSourceCreationDisabled(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Manual Source creation is disabled. Create Sources from a discovered Provider Runtime Model Offer."
    default_code = "MANUAL_SOURCE_CREATION_DISABLED"


class SourceImmutable(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Sources are immutable. Delete this Source and create a replacement when its identity, visibility, or pricing must change."
    default_code = "SOURCE_IMMUTABLE"


class ModelGroupNotFound(exceptions.APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Model group not found."
    default_code = "NOT_FOUND"


class ModelGroupRoutingConflict(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "MODEL_POOL_ROUTING_CONFLICT"

    def __init__(self, *, current_revision: int):
        super().__init__(
            detail={
                "code": self.default_code,
                "message": "The Model Pool routing policy changed while you were editing it. Reload the latest policy and review your draft.",
                "current_revision": current_revision,
            },
            code=self.default_code,
        )


class ModelGroupRoutingInvalid(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "MODEL_POOL_ROUTING_INVALID"

    def __init__(self, message: str):
        super().__init__(detail={"code": self.default_code, "message": message}, code=self.default_code)


@transaction.atomic
def create_deployment(*, request, data: dict) -> Deployment:
    tenant = get_tenant_from_request(request)
    require_deployment_admin(request=request, tenant=tenant)
    if data.get("source_type") == "provider_runtime":
        return create_deployment_from_provider_runtime(request=request, tenant=tenant, data=data)
    return deployment_integration().create_additional_source(request=request, tenant=tenant, data=data)


@transaction.atomic
def create_model_sources_batch(*, request, data: dict) -> list[Deployment]:
    """Create one single-model Source per selected Offer as one atomic operation."""

    tenant = get_tenant_from_request(request)
    require_deployment_admin(request=request, tenant=tenant)
    origin = data["origin"]
    runtime_id = origin["provider_runtime_id"]
    created: list[Deployment] = []
    for item in data["sources"]:
        source_data = {
            "deployment_id": item["source_id"],
            "model_offer_id": item["model_offer_id"],
            "visibility": item.get("visibility") or Deployment.VISIBILITY_PRIVATE,
        }
        if item.get("model_group_id"):
            source_data["model_group_id"] = item["model_group_id"]
        else:
            source_data["model_group_name"] = item["new_pool"]["name"]
            source_data["visibility"] = item["new_pool"].get("visibility") or source_data["visibility"]

        if origin["type"] == "provider_runtime":
            source_data.update({"source_type": "provider_runtime", "provider_runtime_id": runtime_id})
        else:
            source_data = deployment_integration().batch_source_data(runtime_id=runtime_id, item=item, source_data=source_data)
        created.append(create_deployment(request=request, data=source_data))
    return created


def create_deployment_from_marketplace(*, request, tenant: Tenant, data: dict) -> Deployment:
    return deployment_integration().create_deployment_from_marketplace(request=request, tenant=tenant, data=data)

def resolve_marketplace_model_group(*, request, tenant: Tenant, data: dict, canonical_model: CanonicalModel) -> ModelGroup:
    return deployment_integration().resolve_marketplace_model_group(request=request, tenant=tenant, data=data, canonical_model=canonical_model)

def resolve_model_group(
    *,
    request,
    tenant: Tenant,
    data: dict,
    canonical_model: CanonicalModel,
    default_name: str,
    default_visibility: str,
    default_project: Project | None,
    require_name: bool = False,
) -> ModelGroup:
    model_group_id = data.get("model_group_id")
    if model_group_id:
        queryset = ModelGroup.objects.filter(tenant=tenant, id=model_group_id)
        queryset = scope_queryset_to_request_ownership(queryset=queryset, request=request)
        group = queryset.filter(status=SoftDeleteModel.STATUS_ACTIVE).first()
        if group is None:
            raise exceptions.ValidationError("Selected Model Pool was not found.")
    else:
        model_group_name = str(data.get("model_group_name") or default_name).strip()
        if require_name and not model_group_name:
            raise exceptions.ValidationError("Model Pool name is required.")
        if not model_group_name:
            model_group_name = canonical_model.key
        group, _ = ModelGroup.objects.get_or_create(
            tenant=tenant,
            name=model_group_name,
            defaults={
                "display_name": model_group_name,
                "canonical_model": canonical_model,
                "visibility": data.get("visibility") or default_visibility,
                "project": default_project,
                "created_by": request.user,
            },
        )
        project_id = current_project_id(request)
        if project_id and str(group.project_id or "") != project_id:
            raise exceptions.ValidationError("Selected Model Pool was not found.")
    deployment_integration().validate_model_group(request=request, group=group)
    if group.canonical_model_id != canonical_model.id:
        raise exceptions.ValidationError("Selected Model Pool serves a different model.")
    return group


def resolve_request_project(*, request, tenant: Tenant) -> Project | None:
    return deployment_integration().resolve_request_project(request=request, tenant=tenant)

def scope_queryset_to_request_ownership(*, queryset, request):
    return deployment_integration().scope_queryset_to_request_ownership(queryset=queryset, request=request)

def marketplace_source_id(*, contribution) -> str:
    return deployment_integration().marketplace_source_id(contribution=contribution)

def create_deployment_from_provider_runtime(*, request, tenant: Tenant, data: dict) -> Deployment:
    from apps.providers.models import ProviderRuntimeAccount, ProviderRuntimeModelOffer
    from apps.providers.runtime_services import prepare_runtime_model_offer_for_use

    runtime_queryset = ProviderRuntimeAccount.objects.select_related(
        "source_provider_account", "provider_account"
    ).filter(tenant=tenant, id=data["provider_runtime_id"])
    runtime = scope_queryset_to_request_ownership(queryset=runtime_queryset, request=request).filter(
        status=SoftDeleteModel.STATUS_ACTIVE
    ).first()
    if runtime is None:
        raise exceptions.ValidationError("Provider runtime not found.")
    if runtime.status != ProviderRuntimeAccount.STATUS_ACTIVE:
        raise exceptions.ValidationError("Only active provider runtimes can be added as model sources.")
    if not runtime.internal_api_url:
        raise exceptions.ValidationError("Provider runtime API URL is not available.")

    provider_account = runtime.provider_account
    if runtime.runtime_type == ProviderRuntimeAccount.RUNTIME_DIRECT_API:
        provider_account = runtime.source_provider_account
        if provider_account is None:
            raise exceptions.ValidationError("Direct API runtime requires a source provider account.")
    elif provider_account is None:
        if not runtime.encrypted_proxy_api_key:
            raise exceptions.ValidationError("Provider runtime proxy key is not available.")
        provider = get_or_create_provider("codex-login")
        provider_account, _ = ProviderAccount.objects.update_or_create(
            tenant=tenant,
            provider=provider,
            account_id=f"runtime_{runtime.id}",
            defaults={
                "url": runtime.internal_api_url,
                "encrypted_key": runtime.encrypted_proxy_api_key,
                "status": SoftDeleteModel.STATUS_ACTIVE,
                "deleted_at": None,
                "created_by": request.user,
            },
        )

    offer = ProviderRuntimeModelOffer.objects.select_related("canonical_model").filter(
        id=data["model_offer_id"],
        runtime_account=runtime,
    ).exclude(
        status=SoftDeleteModel.STATUS_DELETED,
    ).first()
    if offer is None:
        raise exceptions.ValidationError("Model offer not found for this Provider Runtime.")
    offer = prepare_runtime_model_offer_for_use(
        offer=offer,
        actor=request.user,
        allow_degraded=False,
    )

    provider = provider_account.provider
    deployment_id = data.get("deployment_id") or f"runtime-{str(runtime.id)[:8]}-{offer.canonical_model.key}"
    deployment = create_or_restore_deployment(
        tenant=tenant,
        deployment_id=deployment_id,
        defaults={
            "provider": provider,
            "provider_account": provider_account,
            "canonical_model": offer.canonical_model,
            "upstream_model_id": offer.upstream_model_id,
            "provider_runtime": runtime,
            "runtime_model_offer": offer,
            "endpoint": runtime.internal_api_url,
            "visibility": data.get("visibility") or Deployment.VISIBILITY_PRIVATE,
            "project": runtime.project,
            "pricing_rate": data.get("pricing_rate") or offer.price_per_1k_tokens or 0,
            "health_status": Deployment.HEALTH_HEALTHY,
            "health_reason": "",
            "created_by": request.user,
        },
    )
    group = resolve_model_group(
        request=request,
        tenant=tenant,
        data=data,
        canonical_model=offer.canonical_model,
        default_name=offer.canonical_model.key,
        default_visibility=deployment.visibility,
        default_project=runtime.project,
    )
    link_model_group_deployment(model_group=group, deployment=deployment, actor=request.user)
    runtime.provider_account = provider_account
    runtime.save(update_fields=["provider_account", "updated_at"])
    log_audit(
        request=request,
        action="deployments.create",
        actor=request.user,
        resource_type="deployment",
        resource_id=deployment.pk,
        after=deployment_snapshot(deployment),
        metadata={
            "source_type": "provider_runtime",
            "provider_runtime_id": str(runtime.id),
            "deployment_id": deployment.deployment_id,
            "provider": provider.name,
            "model_offer_id": str(offer.id),
            "canonical_model": offer.canonical_model.key,
            "upstream_model_id": offer.upstream_model_id,
        },
    )
    return deployment


def create_or_restore_deployment(*, tenant: Tenant, deployment_id: str, defaults: dict) -> Deployment:
    deployment_integration().validate_restoration(tenant=tenant, deployment_id=deployment_id, defaults=defaults)
    active_exists = (
        Deployment.objects.filter(tenant=tenant, deployment_id=deployment_id)
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .exists()
    )
    if active_exists:
        raise exceptions.ValidationError("Deployment/source ID already exists.")
    deployment, _ = Deployment.objects.update_or_create(
        tenant=tenant,
        deployment_id=deployment_id,
        defaults={
            **defaults,
            "status": SoftDeleteModel.STATUS_ACTIVE,
            "deleted_at": None,
        },
    )
    return deployment


def link_model_group_deployment(*, model_group: ModelGroup, deployment: Deployment, actor=None) -> None:
    if model_group.tenant_id != deployment.tenant_id:
        raise exceptions.ValidationError("Model Pool and Source must belong to the same Workspace.")
    if model_group.project_id != deployment.project_id:
        raise exceptions.ValidationError("Model Pool and Source must belong to the same Project scope.")
    if model_group.canonical_model_id != deployment.canonical_model_id:
        raise exceptions.ValidationError("Selected Model Pool serves a different model.")
    ModelGroupDeployment.objects.update_or_create(
        model_group=model_group,
        deployment=deployment,
        defaults={"status": SoftDeleteModel.STATUS_ACTIVE, "deleted_at": None},
    )
    links = _routing_links(group=model_group)
    current_sources = [_routing_source_snapshot(link) for link in links]
    latest = model_group.routing_revisions.order_by("-revision").first()
    if latest is None or latest.sources != current_sources:
        if latest is not None:
            model_group.routing_revision += 1
            model_group.save(update_fields=["routing_revision", "updated_at"])
        _record_routing_revision(group=model_group, links=links, actor=actor)


def list_deployments(*, request):
    tenant = get_tenant_from_request(request)
    return discoverable_resource_queryset(
        visible_deployments(user=request.user, tenant=tenant), request=request,
        tenant=tenant, resource_type="deployment",
    )


def get_deployment(*, request, deployment_identifier: str) -> Deployment:
    tenant = get_tenant_from_request(request)
    queryset = discoverable_resource_queryset(
        visible_deployments(user=request.user, tenant=tenant), request=request,
        tenant=tenant, resource_type="deployment",
    )
    deployment = queryset.filter(deployment_id=deployment_identifier).first()
    if deployment is None and is_uuid(deployment_identifier):
        deployment = queryset.filter(id=deployment_identifier).first()
    if deployment is None:
        raise DeploymentNotFound()
    return deployment


def is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def update_deployment(*, request, deployment_identifier: str, data: dict) -> Deployment:
    tenant = get_tenant_from_request(request)
    require_deployment_admin(request=request, tenant=tenant)
    get_deployment(request=request, deployment_identifier=deployment_identifier)
    raise SourceImmutable()


def sync_deployment_model_group(*, request, deployment: Deployment) -> None:
    group, _ = ModelGroup.objects.get_or_create(
        tenant=deployment.tenant,
        name=deployment.canonical_model.key,
        defaults={
            "display_name": deployment.canonical_model.display_name or deployment.canonical_model.key,
            "canonical_model": deployment.canonical_model,
            "visibility": deployment.visibility,
            "team": deployment.team,
            "project": deployment.project,
            "created_by": request.user,
        },
    )
    ModelGroupDeployment.objects.filter(deployment=deployment).exclude(model_group=group).update(
        status=SoftDeleteModel.STATUS_DELETED,
        deleted_at=timezone.now(),
    )
    ModelGroupDeployment.objects.update_or_create(
        model_group=group,
        deployment=deployment,
        defaults={"status": SoftDeleteModel.STATUS_ACTIVE, "deleted_at": None},
    )


def disable_deployment(*, request, deployment_identifier: str) -> Deployment:
    tenant = get_tenant_from_request(request)
    require_deployment_admin(request=request, tenant=tenant)
    deployment = get_deployment(request=request, deployment_identifier=deployment_identifier)
    before = deployment_snapshot(deployment)
    deployment.status = SoftDeleteModel.STATUS_DISABLED
    deployment.save(update_fields=["status", "updated_at"])
    log_audit(
        request=request,
        action="deployments.disable",
        actor=request.user,
        resource_type="deployment",
        resource_id=deployment.pk,
        before=before,
        after=deployment_snapshot(deployment),
    )
    return deployment


@transaction.atomic
def delete_deployment(*, request, deployment_identifier: str) -> Deployment:
    tenant = get_tenant_from_request(request)
    require_deployment_admin(request=request, tenant=tenant)
    deployment = get_deployment(request=request, deployment_identifier=deployment_identifier)
    before = deployment_snapshot(deployment)
    now = timezone.now()
    # PostgreSQL rejects SELECT FOR UPDATE when the locked query itself uses
    # DISTINCT. Resolve the unique identifiers first, then lock the concrete
    # Pool rows so membership removal and revision creation remain atomic.
    affected_group_ids = list(
        ModelGroupDeployment.objects.filter(deployment=deployment)
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .values_list("model_group_id", flat=True)
        .distinct()
    )
    affected_groups = list(
        ModelGroup.objects.select_for_update()
        .filter(pk__in=affected_group_ids)
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .order_by("pk")
    )
    ModelGroupDeployment.objects.filter(deployment=deployment).exclude(status=SoftDeleteModel.STATUS_DELETED).update(
        status=SoftDeleteModel.STATUS_DELETED,
        deleted_at=now,
    )
    for group in affected_groups:
        group.routing_revision += 1
        group.save(update_fields=["routing_revision", "updated_at"])
        _record_routing_revision(group=group, links=_routing_links(group=group), actor=request.user)
    deployment_integration().remove_contributions(deployment=deployment, now=now)
    deployment.delete()
    log_audit(
        request=request,
        action="deployments.delete",
        actor=request.user,
        resource_type="deployment",
        resource_id=deployment.pk,
        before=before,
        after=deployment_snapshot(deployment),
    )
    return deployment


def set_visibility(*, request, deployment_identifier: str, visibility: str) -> Deployment:
    tenant = get_tenant_from_request(request)
    require_deployment_admin(request=request, tenant=tenant)
    get_deployment(request=request, deployment_identifier=deployment_identifier)
    raise SourceImmutable()


def set_pricing(*, request, deployment_identifier: str, pricing_rate: Decimal) -> Deployment:
    tenant = get_tenant_from_request(request)
    require_deployment_admin(request=request, tenant=tenant)
    get_deployment(request=request, deployment_identifier=deployment_identifier)
    raise SourceImmutable()


def deployment_status(*, request):
    tenant = get_tenant_from_request(request)
    return scope_queryset_to_current_project(visible_deployments(user=request.user, tenant=tenant), request).order_by("deployment_id")


def run_deployment_health_check(*, request, deployment_identifier: str) -> Deployment:
    tenant = get_tenant_from_request(request)
    require_deployment_admin(request=request, tenant=tenant)
    deployment = get_deployment(request=request, deployment_identifier=deployment_identifier)
    result = check_deployment_health(deployment=deployment, actor=request.user, request=request)
    return result.deployment


def list_visible_models(*, request):
    tenant = get_tenant_from_request(request)
    groups = visible_model_groups(user=request.user, tenant=tenant).select_related("project").prefetch_related("deployment_links")
    return discoverable_resource_queryset(groups, request=request, tenant=tenant, resource_type="model_group")


def get_model_group(*, request, model_group_identifier: str) -> ModelGroup:
    tenant = get_tenant_from_request(request)
    queryset = discoverable_resource_queryset(
        visible_model_groups(user=request.user, tenant=tenant),
        request=request, tenant=tenant, resource_type="model_group",
    )
    group = queryset.filter(name=model_group_identifier).first()
    if group is None and is_uuid(model_group_identifier):
        group = queryset.filter(id=model_group_identifier).first()
    if group is None:
        raise ModelGroupNotFound()
    return group


def _routing_links(*, group: ModelGroup, lock: bool = False) -> list[ModelGroupDeployment]:
    queryset = ModelGroupDeployment.objects.filter(model_group=group).exclude(status=SoftDeleteModel.STATUS_DELETED)
    if lock:
        queryset = queryset.select_for_update()
    return list(queryset.select_related("deployment", "deployment__provider").order_by("created_at"))


def _routing_source_snapshot(link: ModelGroupDeployment) -> dict:
    return {
        "id": str(link.id),
        "deployment_id": str(link.deployment_id),
        "enabled": bool(link.enabled),
        "priority": int(link.priority),
        "weight": int(link.weight),
        "fallback_order": int(link.fallback_order),
    }


def _apply_routing_draft(*, links: list[ModelGroupDeployment], sources: list[dict] | None) -> list[dict]:
    current = {link.id: _routing_source_snapshot(link) for link in links}
    if sources is None:
        return list(current.values())
    requested_ids = {item["id"] for item in sources}
    if requested_ids != set(current):
        raise ModelGroupRoutingInvalid("The policy draft must include every current Source exactly once. Reload the Pool before applying changes.")
    drafts = []
    for item in sources:
        draft = {**current[item["id"]]}
        for field in ("enabled", "priority", "weight", "fallback_order"):
            if field in item:
                draft[field] = item[field]
        drafts.append(draft)
    return drafts


def _validate_routing_policy(*, strategy: str, drafts: list[dict], links: list[ModelGroupDeployment]) -> None:
    active_deployment_ids = {
        str(link.deployment_id)
        for link in links
        if link.deployment.status == SoftDeleteModel.STATUS_ACTIVE
    }
    enabled = [draft for draft in drafts if draft["enabled"] and draft["deployment_id"] in active_deployment_ids]
    if not enabled:
        raise ModelGroupRoutingInvalid("A production Pool must retain at least one enabled, active Source.")
    if strategy == ModelGroup.ROUTING_WEIGHTED and sum(int(item["weight"]) for item in enabled) <= 0:
        raise ModelGroupRoutingInvalid("Weighted traffic requires a positive total weight across enabled Sources.")
    if strategy == ModelGroup.ROUTING_FALLBACK:
        orders = [int(item["fallback_order"]) for item in enabled]
        if len(orders) != len(set(orders)):
            raise ModelGroupRoutingInvalid("Fallback order must be unique across enabled Sources.")


def _record_routing_revision(*, group: ModelGroup, links: list[ModelGroupDeployment], actor) -> None:
    ModelGroupRoutingRevision.objects.create(
        model_group=group,
        revision=group.routing_revision,
        routing_strategy=group.routing_strategy,
        routing_config=group.routing_config or {},
        sources=[_routing_source_snapshot(link) for link in links],
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )


@transaction.atomic
def update_model_group_routing(*, request, model_group_identifier: str, data: dict) -> ModelGroup:
    tenant = get_tenant_from_request(request)
    require_deployment_admin(request=request, tenant=tenant)
    visible_group = get_model_group(request=request, model_group_identifier=model_group_identifier)
    group = ModelGroup.objects.select_for_update().get(pk=visible_group.pk)
    if int(data["expected_revision"]) != int(group.routing_revision):
        raise ModelGroupRoutingConflict(current_revision=group.routing_revision)
    links = _routing_links(group=group, lock=True)
    if not group.routing_revisions.filter(revision=group.routing_revision).exists():
        _record_routing_revision(group=group, links=links, actor=group.created_by)
    drafts = _apply_routing_draft(links=links, sources=data.get("sources"))
    strategy = data["routing_strategy"]
    _validate_routing_policy(strategy=strategy, drafts=drafts, links=links)

    before = {
        "revision": group.routing_revision,
        "routing_strategy": group.routing_strategy,
        "routing_config": group.routing_config,
        "sources": [_routing_source_snapshot(link) for link in links],
    }
    draft_by_id = {item["id"]: item for item in drafts}
    for link in links:
        draft = draft_by_id[str(link.id)]
        changed = []
        for field in ("enabled", "priority", "weight", "fallback_order"):
            if getattr(link, field) != draft[field]:
                setattr(link, field, draft[field])
                changed.append(field)
        if changed:
            link.save(update_fields=[*changed, "updated_at"])

    group.routing_strategy = strategy
    group.routing_config = data.get("routing_config") or {}
    group.routing_revision += 1
    group.save(update_fields=["routing_strategy", "routing_config", "routing_revision", "updated_at"])
    _record_routing_revision(group=group, links=links, actor=request.user)
    log_audit(
        request=request,
        action="models.routing.update",
        actor=request.user,
        resource_type="model_group",
        resource_id=group.pk,
        before=before,
        after={
            "revision": group.routing_revision,
            "routing_strategy": group.routing_strategy,
            "routing_config": group.routing_config,
            "sources": [_routing_source_snapshot(link) for link in links],
        },
        metadata={"routing_strategy": group.routing_strategy, "revision": group.routing_revision},
    )
    return group


@transaction.atomic
def update_model_group_source_routing(*, request, model_group_identifier: str, source_id: str, data: dict) -> ModelGroupDeployment:
    """Compatibility endpoint with the same revision fence as the atomic policy API."""

    tenant = get_tenant_from_request(request)
    require_deployment_admin(request=request, tenant=tenant)
    visible_group = get_model_group(request=request, model_group_identifier=model_group_identifier)
    group = ModelGroup.objects.select_for_update().get(pk=visible_group.pk)
    if int(data["expected_revision"]) != int(group.routing_revision):
        raise ModelGroupRoutingConflict(current_revision=group.routing_revision)
    links = _routing_links(group=group, lock=True)
    link = next((candidate for candidate in links if candidate.id == source_id), None)
    if link is None:
        raise exceptions.NotFound("Model source link not found.")
    draft_item = {"id": link.id, **{field: data[field] for field in ("enabled", "priority", "weight", "fallback_order") if field in data}}
    drafts = _apply_routing_draft(
        links=links,
        sources=[{"id": candidate.id, **(draft_item if candidate.id == link.id else {})} for candidate in links],
    )
    _validate_routing_policy(strategy=group.routing_strategy, drafts=drafts, links=links)
    draft = next(item for item in drafts if item["id"] == str(link.id))
    update_fields = []
    for field in ("enabled", "priority", "weight", "fallback_order"):
        if getattr(link, field) != draft[field]:
            setattr(link, field, draft[field])
            update_fields.append(field)
    if update_fields:
        link.save(update_fields=[*update_fields, "updated_at"])
    group.routing_revision += 1
    group.save(update_fields=["routing_revision", "updated_at"])
    _record_routing_revision(group=group, links=links, actor=request.user)
    log_audit(
        request=request,
        action="models.source_routing.update",
        actor=request.user,
        resource_type="model_group_deployment",
        resource_id=link.pk,
        metadata={"model_group": group.name, "deployment_id": link.deployment.deployment_id, "fields": update_fields, "revision": group.routing_revision},
    )
    return link


def preview_model_group_routing(*, request, model_group_identifier: str, data: dict) -> dict:
    tenant = get_tenant_from_request(request)
    require_deployment_admin(request=request, tenant=tenant)
    group = get_model_group(request=request, model_group_identifier=model_group_identifier)
    if int(data["expected_revision"]) != int(group.routing_revision):
        raise ModelGroupRoutingConflict(current_revision=group.routing_revision)
    links = _routing_links(group=group)
    deployment_integration().validate_routing_preview(request=request, group=group, links=links)
    drafts = _apply_routing_draft(links=links, sources=data.get("sources"))
    strategy = data["routing_strategy"]
    _validate_routing_policy(strategy=strategy, drafts=drafts, links=links)
    links_by_deployment = {str(link.deployment_id): link for link in links}
    enabled = [item for item in drafts if item["enabled"] and links_by_deployment[item["deployment_id"]].deployment.status == SoftDeleteModel.STATUS_ACTIVE]
    total_weight = sum(int(item["weight"]) for item in enabled)

    def sort_key(item):
        deployment = links_by_deployment[item["deployment_id"]].deployment
        if strategy == ModelGroup.ROUTING_LOWEST_COST:
            return (deployment.pricing_rate, item["priority"], deployment.created_at)
        if strategy == ModelGroup.ROUTING_LOWEST_LATENCY:
            return (deployment.last_latency_ms or 10_000_000, item["priority"], deployment.created_at)
        if strategy == ModelGroup.ROUTING_BEST_HEALTH:
            health_rank = {Deployment.HEALTH_HEALTHY: 0, Deployment.HEALTH_DEGRADED: 1, Deployment.HEALTH_UNKNOWN: 2, Deployment.HEALTH_UNHEALTHY: 3}
            return (health_rank.get(deployment.health_status, 9), item["priority"], deployment.created_at)
        if strategy == ModelGroup.ROUTING_WEIGHTED:
            return (-item["weight"], item["priority"], deployment.created_at)
        return (item["fallback_order"], item["priority"], deployment.created_at)

    ordered = sorted(enabled, key=sort_key)
    candidates = []
    for rank, item in enumerate(ordered, start=1):
        deployment = links_by_deployment[item["deployment_id"]].deployment
        candidates.append({
            "source_id": str(deployment.id),
            "source": deployment.deployment_id,
            "provider": deployment.provider.name,
            "rank": rank,
            "health_status": deployment.health_status,
            "last_checked_at": deployment.last_checked_at,
            "latency_ms": deployment.last_latency_ms,
            "price_per_1k_tokens": str(deployment.pricing_rate),
            "traffic_share_percent": round((int(item["weight"]) / total_weight) * 100, 2) if strategy == ModelGroup.ROUTING_WEIGHTED and total_weight else None,
        })

    router_ids = deployment_integration().affected_router_ids(request=request, group=group)
    healthy_count = sum(1 for item in ordered if links_by_deployment[item["deployment_id"]].deployment.health_status == Deployment.HEALTH_HEALTHY)
    risks = []
    if len(enabled) == 1:
        risks.append({"code": "SINGLE_SOURCE", "severity": "warning", "message": "This Pool has a single enabled Source and no provider fallback."})
    if healthy_count == 0:
        risks.append({"code": "NO_HEALTHY_SOURCE", "severity": "critical", "message": "No enabled Source currently reports healthy."})
    if not router_ids:
        risks.append({"code": "NO_ROUTER", "severity": "info", "message": "No Router currently consumes this Pool."})
    return {
        "valid": True,
        "expected_revision": group.routing_revision,
        "next_revision": group.routing_revision + 1,
        "routing_strategy": strategy,
        "candidate_count": len(candidates),
        "healthy_candidate_count": healthy_count,
        "affected_router_count": len(router_ids),
        "risks": risks,
        "candidates": candidates,
    }


def list_model_group_routing_history(*, request, model_group_identifier: str) -> list[dict]:
    tenant = get_tenant_from_request(request)
    require_deployment_admin(request=request, tenant=tenant)
    group = get_model_group(request=request, model_group_identifier=model_group_identifier)
    return [
        {
            "revision": item.revision,
            "routing_strategy": item.routing_strategy,
            "routing_config": item.routing_config,
            "sources": item.sources,
            "created_at": item.created_at,
            "created_by": item.created_by.get_full_name() or item.created_by.get_username() if item.created_by else "System",
            "is_current": item.revision == group.routing_revision,
        }
        for item in group.routing_revisions.select_related("created_by").order_by("-revision")[:25]
    ]


def rollback_model_group_routing(*, request, model_group_identifier: str, expected_revision: int, target_revision: int) -> ModelGroup:
    tenant = get_tenant_from_request(request)
    require_deployment_admin(request=request, tenant=tenant)
    group = get_model_group(request=request, model_group_identifier=model_group_identifier)
    snapshot = group.routing_revisions.filter(revision=target_revision).first()
    if snapshot is None:
        raise exceptions.NotFound("Routing revision not found.")
    return update_model_group_routing(
        request=request,
        model_group_identifier=model_group_identifier,
        data={
            "expected_revision": expected_revision,
            "routing_strategy": snapshot.routing_strategy,
            "routing_config": snapshot.routing_config,
            "sources": [{**item, "id": uuid.UUID(str(item["id"]))} for item in snapshot.sources],
        },
    )


def visible_deployments(*, user, tenant: Tenant):
    return deployment_integration().visible_deployments(user=user, tenant=tenant)

def visible_model_groups(*, user, tenant: Tenant):
    return deployment_integration().visible_model_groups(user=user, tenant=tenant)

def require_deployment_admin(*, request, tenant: Tenant) -> None:
    if not has_nexus_permission(request.user, tenant, "admin"):
        raise exceptions.PermissionDenied("Tenant admin permission is required.")


def get_active_canonical_model(model_id) -> CanonicalModel:
    model = CanonicalModel.objects.filter(id=model_id, status=CanonicalModel.STATUS_ACTIVE).first()
    if model is None:
        raise exceptions.ValidationError("Canonical Model was not found or is inactive.")
    return model


def deployment_snapshot(deployment: Deployment) -> dict:
    provider_name = deployment.provider.name if getattr(deployment, "provider_id", None) else ""
    provider_account = deployment.provider_account.account_id if getattr(deployment, "provider_account_id", None) else ""
    return snapshot_resource(
        "deployment",
        deployment,
        extra={
            "provider": provider_name,
            "provider_account": provider_account,
            "canonical_model": deployment.canonical_model.key if deployment.canonical_model_id else "",
            "upstream_model_id": deployment.upstream_model_id,
            "provider_runtime_id": str(deployment.provider_runtime_id or ""),
            "runtime_model_offer_id": str(deployment.runtime_model_offer_id or ""),
        },
    )
