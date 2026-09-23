from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import Prefetch, Q, Count, OuterRef, Subquery, Case, When, Value, CharField
from django.db.models.functions import Coalesce
from uuid import UUID
from django.utils import timezone
from rest_framework import exceptions, status

from apps.audit.services import log_audit
from apps.common.crypto import encrypt_secret
from apps.common.models import SoftDeleteModel
from apps.deployments.models import Deployment, ModelGroupDeployment
from apps.common.resource_catalog import (
    resolve_ownership_project,
)
from apps.common.request_context import get_tenant_from_request

from .catalog import provider_catalog
from .runtime_integration import runtime_integration

from .models import (
    ProviderAccount,
    ProviderRuntimeAccount,
    ProviderRuntimeModelOffer,
)
from .runtime_runner import get_provider_runtime_runner
from .services import (
    create_provider_account,
    ensure_runtime_for_provider_account,
    get_provider_account,
    get_or_create_provider,
    require_provider_admin,
    update_provider_account,
    visible_provider_accounts,
)


class ProviderConnectionNotFound(exceptions.APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Provider not found."
    default_code = "NOT_FOUND"


class ProviderConnectionConflict(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Provider request conflicts with its current state."
    default_code = "PROVIDER_CONFLICT"


class ProviderMustBeStopped(ProviderConnectionConflict):
    default_detail = "Stop the Provider before changing its Engine or upstream Provider."
    default_code = "PROVIDER_MUST_BE_STOPPED"


class ProviderRepairRequired(ProviderConnectionConflict):
    default_detail = "This Provider needs repair before it can be used."
    default_code = "PROVIDER_REPAIR_REQUIRED"


class ProviderAlreadyExists(ProviderConnectionConflict):
    default_detail = "A Provider with this upstream Provider and account identity already exists."
    default_code = "PROVIDER_ALREADY_EXISTS"


class ProviderRuntimeStopFailed(ProviderConnectionConflict):
    default_detail = "The Provider could not be removed because its runtime did not stop."
    default_code = "PROVIDER_RUNTIME_STOP_FAILED"


def _runtime_queryset(*, summary=False):
    return runtime_integration().connection_runtime_queryset(summary=summary)


def list_provider_connections(*, request, account_id: str | None = None, queryset_only: bool = False, summary: bool = False):
    tenant = get_tenant_from_request(request)
    queryset = (
        ProviderAccount.objects.filter(tenant=tenant)
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .select_related("provider", "tenant")
        .prefetch_related(
            Prefetch("source_runtime_accounts", queryset=_runtime_queryset(summary=summary), to_attr="prefetched_connection_runtimes")
        )
    )
    if account_id is not None:
        target = Q(account_id=account_id)
        try:
            target |= Q(pk=UUID(str(account_id)))
        except (TypeError, ValueError, AttributeError):
            pass
        queryset = queryset.filter(target)
    if queryset_only:
        return _catalog_connections(queryset, request, tenant)
    accounts = list(visible_provider_accounts(queryset).distinct().order_by("name", "account_id"))
    return provider_catalog().filter_rows(accounts=accounts, request=request, tenant=tenant)


def _catalog_connections(queryset, request, tenant):
    """Apply the same discovery policy in SQL, before page materialization."""
    runtimes = ProviderRuntimeAccount.objects.filter(source_provider_account_id=OuterRef("pk")).exclude(status="deleted")
    queryset = visible_provider_accounts(queryset).annotate(
        catalog_runtime_count=Count("source_runtime_accounts", filter=~Q(source_runtime_accounts__status="deleted"), distinct=True),
        catalog_project_id=Subquery(runtimes.values("project_id")[:1]),
        catalog_runtime_status=Subquery(runtimes.values("status")[:1]),
    )
    queryset = provider_catalog().filter_queryset(queryset=queryset, request=request, tenant=tenant)
    params = request.query_params
    query = (params.get("q") or "").strip()
    if len(query) > 200:
        raise exceptions.ValidationError({"q": "Search is limited to 200 characters."})
    if query:
        queryset = queryset.filter(Q(name__icontains=query) | Q(provider__name__icontains=query)
            | Q(source_runtime_accounts__runtime_type__icontains=query)
            | Q(source_runtime_accounts__model_offers__upstream_model_id__icontains=query)
            | Q(source_runtime_accounts__model_offers__canonical_model__key__icontains=query))
    queryset = queryset.annotate(catalog_status=Case(
        When(catalog_runtime_count=0, then=Value("repair_required")),
        When(catalog_runtime_status__in=["failed", "unhealthy", "login_required", "starting", "stopping", "stopped", "created"], then="catalog_runtime_status"),
        When(login_status__in=["failed", "login_required", "logging_in"], then="login_status"),
        default="catalog_runtime_status", output_field=CharField()))
    status_filter = params.get("status", "all")
    states = {"attention": ["failed", "login_required", "unhealthy", "repair_required", "degraded"],
        "healthy": ["active"], "recovery": ["stopped", "repair_required", "failed"]}
    if status_filter in states:
        queryset = queryset.filter(catalog_status__in=states[status_filter])
    elif status_filter != "all":
        raise exceptions.ValidationError({"status": "Unknown Provider status filter."})
    return queryset.distinct()


def get_provider_connection(*, request, account_id: str) -> ProviderAccount:
    for account in list_provider_connections(request=request, account_id=account_id):
        if str(account.id) == str(account_id) or account.account_id == account_id:
            return account
    raise ProviderConnectionNotFound()


def connection_runtime(account: ProviderAccount) -> ProviderRuntimeAccount | None:
    prefetched = getattr(account, "prefetched_connection_runtimes", None)
    if prefetched is not None:
        return prefetched[0] if prefetched else None
    return (
        account.source_runtime_accounts.exclude(status=SoftDeleteModel.STATUS_DELETED)
        .prefetch_related("model_offers__canonical_model")
        .order_by("-created_at")
        .first()
    )


def require_connection_runtime(*, request, account_id: str) -> tuple[ProviderAccount, ProviderRuntimeAccount]:
    account = get_provider_connection(request=request, account_id=account_id)
    runtime = connection_runtime(account)
    if runtime is None:
        raise ProviderRepairRequired()
    return account, runtime


def repair_provider_connection(*, request, account_id: str) -> ProviderAccount:
    account = get_provider_connection(request=request, account_id=account_id)
    require_provider_admin(request=request, tenant=account.tenant)
    if connection_runtime(account) is None:
        ensure_runtime_for_provider_account(request=request, account=account)
    return get_provider_connection(request=request, account_id=account_id)


@transaction.atomic
def create_provider_connection(*, request, data: dict[str, Any], create_only: bool = False, refresh_result: bool = True) -> ProviderAccount:
    tenant = get_tenant_from_request(request)
    ownership = data.pop("ownership", None)
    project, _ownership_inferred = resolve_ownership_project(request=request, tenant=tenant, ownership=ownership)
    engine = data.pop("engine")
    upstream_provider = data.pop("upstream_provider", "")
    account_data = provider_account_data(engine=engine, upstream_provider=upstream_provider, data=data)
    account = create_provider_account(request=request, data=account_data, create_only=create_only, ownership=ownership)
    runtime = connection_runtime(account)
    if runtime is None:
        raise ProviderRepairRequired()
    runtime.project = project
    runtime.save(update_fields=["project", "updated_at"])
    return get_provider_connection(request=request, account_id=str(account.id)) if refresh_result else account


@transaction.atomic
def update_provider_connection(*, request, account_id: str, data: dict[str, Any]) -> ProviderAccount:
    account = get_provider_connection(request=request, account_id=account_id)
    require_provider_admin(request=request, tenant=account.tenant)
    runtime = connection_runtime(account)
    engine = data.pop("engine", runtime.runtime_type if runtime else _engine_for_account(account))
    upstream_provider = data.pop("upstream_provider", account.provider.name)
    if engine == ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI and upstream_provider not in {"openai", "claude"}:
        raise exceptions.ValidationError({"upstream_provider": "Choose OpenAI or Claude for CLIProxyAPI."})
    critical_change = bool(
        runtime
        and (engine != runtime.runtime_type or upstream_provider != account.provider.name)
    )
    if critical_change and runtime.status not in {
        ProviderRuntimeAccount.STATUS_CREATED,
        ProviderRuntimeAccount.STATUS_STOPPED,
        ProviderRuntimeAccount.STATUS_FAILED,
    }:
        raise ProviderMustBeStopped()

    credential_change = engine == ProviderRuntimeAccount.RUNTIME_DIRECT_API and (
        "key" in data
        or ("url" in data and str(data["url"] or "").rstrip("/") != str(account.url or "").rstrip("/"))
        or (runtime and runtime.runtime_type != ProviderRuntimeAccount.RUNTIME_DIRECT_API)
    )
    if credential_change:
        candidate_url = str(data.get("url", account.url) or "").strip()
        candidate_key = str(data["key"]) if "key" in data else account.encrypted_key
        if not candidate_url or not candidate_key:
            raise exceptions.ValidationError({"key": "API URL and API key are required for Direct API."})
        # Saving configuration must not depend on upstream availability or a
        # /models endpoint. Connectivity is checked by a separate model refresh.

    update_data: dict[str, Any] = {}
    for field in ("name", "url", "key"):
        if field in data:
            update_data[field] = data[field]
    update_data.update(_account_mode_fields(engine))

    if upstream_provider != account.provider.name:
        provider = get_or_create_provider(upstream_provider)
        duplicate = (
            ProviderAccount.objects.filter(
                tenant=account.tenant,
                provider=provider,
                account_id=account.account_id,
            )
            .exclude(id=account.id)
            .exclude(status=SoftDeleteModel.STATUS_DELETED)
            .exists()
        )
        if duplicate:
            raise ProviderAlreadyExists()
        account.provider = provider
        account.save(update_fields=["provider", "updated_at"])

    account = update_provider_account(
        request=request,
        account_identifier=str(account.id),
        data=update_data,
    )
    if runtime is None:
        ensure_runtime_for_provider_account(request=request, account=account)
    elif critical_change:
        runtime.runtime_type = engine
        runtime.name = f"{account.name or account.account_id}-{engine.replace('_', '-')}"
        runtime.last_error = ""
        runtime.save(update_fields=["runtime_type", "name", "last_error", "updated_at"])
    if credential_change:
        runtime = connection_runtime(account)
        if runtime is not None:
            # Old probes describe the previous credentials, not the saved ones.
            # Preserve lifecycle state, including a user-stopped connection.
            now = timezone.now()
            ProviderRuntimeAccount.objects.filter(pk=runtime.pk).update(
                last_health_check_at=None, last_error="", updated_at=now,
            )
            runtime.model_offers.exclude(status=SoftDeleteModel.STATUS_DELETED).update(
                health_status=ProviderRuntimeModelOffer.HEALTH_UNKNOWN,
                health_reason="Connection changed; refresh models to verify.",
                last_health_check_at=None, updated_at=now,
            )
    return get_provider_connection(request=request, account_id=str(account.id))


def provider_account_data(*, engine: str, upstream_provider: str, data: dict[str, Any]) -> dict[str, Any]:
    provider = upstream_provider.strip().lower()
    if engine == ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI and provider not in {"openai", "claude"}:
        raise exceptions.ValidationError({"upstream_provider": "Choose OpenAI or Claude for CLIProxyAPI."})
    if not provider:
        provider = "openai-compatible" if engine == ProviderRuntimeAccount.RUNTIME_DIRECT_API else "openai"
    return {
        **data,
        "provider": provider,
        **_account_mode_fields(engine),
    }


def _account_mode_fields(engine: str) -> dict[str, str]:
    if engine == ProviderRuntimeAccount.RUNTIME_DIRECT_API:
        return {"auth_mode": ProviderAccount.AUTH_API_KEY, "preferred_runtime_type": ""}
    if engine == ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI:
        return {
            "auth_mode": ProviderAccount.AUTH_INTERACTIVE_LOGIN,
            "preferred_runtime_type": ProviderAccount.PREFERRED_RUNTIME_CLIPROXYAPI,
        }
    return {
        "auth_mode": ProviderAccount.AUTH_INTERACTIVE_LOGIN,
        "preferred_runtime_type": ProviderAccount.PREFERRED_RUNTIME_CODEX_PROXY,
    }


def _engine_for_account(account: ProviderAccount) -> str:
    if account.auth_mode == ProviderAccount.AUTH_API_KEY:
        return ProviderRuntimeAccount.RUNTIME_DIRECT_API
    if account.preferred_runtime_type == ProviderAccount.PREFERRED_RUNTIME_CLIPROXYAPI:
        return ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI
    return ProviderRuntimeAccount.RUNTIME_CODEX_PROXY


def provider_connection_deletion_impact(*, request, account_id: str) -> dict[str, Any]:
    return runtime_integration().connection_deletion_impact(request=request, account_id=account_id)


def remove_provider_connection(*, request, account_id: str, confirmation_name: str) -> dict[str, Any]:
    tenant = get_tenant_from_request(request)
    existing_account = get_provider_account(tenant=tenant, identifier=account_id)
    runtime_integration().validate_source_account(account=existing_account, tenant=tenant)
    if existing_account.status == SoftDeleteModel.STATUS_DELETED:
        require_provider_admin(request=request, tenant=tenant)
        return {"id": str(existing_account.id), "status": SoftDeleteModel.STATUS_DELETED}
    account = get_provider_connection(request=request, account_id=account_id)
    require_provider_admin(request=request, tenant=account.tenant)
    impact = provider_connection_deletion_impact(request=request, account_id=account_id)
    expected_name = str(impact["provider_name"])
    if impact["requires_name_confirmation"] and confirmation_name.strip() != expected_name:
        raise exceptions.ValidationError({"confirmation_name": "Enter the Provider name exactly to confirm removal."})

    runtime = connection_runtime(account)
    previous_runtime_status = runtime.status if runtime is not None else ""
    if runtime is not None:
        with transaction.atomic():
            lifecycle_runtime = ProviderRuntimeAccount.objects.select_for_update().get(id=runtime.id)
            previous_runtime_status = lifecycle_runtime.status
            lifecycle_runtime.status = ProviderRuntimeAccount.STATUS_STOPPING
            lifecycle_runtime.save(update_fields=["status", "updated_at"])
            runtime = lifecycle_runtime
    if runtime and runtime.runtime_type != ProviderRuntimeAccount.RUNTIME_DIRECT_API:
        try:
            get_provider_runtime_runner().stop(runtime=runtime)
        except Exception as exc:
            with transaction.atomic():
                lifecycle_runtime = ProviderRuntimeAccount.objects.select_for_update().filter(id=runtime.id).first()
                if lifecycle_runtime is not None and lifecycle_runtime.status == ProviderRuntimeAccount.STATUS_STOPPING:
                    lifecycle_runtime.status = previous_runtime_status
                    lifecycle_runtime.save(update_fields=["status", "updated_at"])
            raise ProviderRuntimeStopFailed(str(exc) or ProviderRuntimeStopFailed.default_detail) from exc

    with transaction.atomic():
        locked_account = (
            ProviderAccount.objects.select_for_update(of=("self",))
            .select_related("provider")
            .get(id=account.id)
        )
        locked_runtime = None
        if runtime:
            locked_runtime = ProviderRuntimeAccount.objects.select_for_update().filter(id=runtime.id).first()
        now = timezone.now()
        if locked_runtime:
            if locked_runtime.status != ProviderRuntimeAccount.STATUS_STOPPING:
                raise ProviderRuntimeStopFailed("Provider lifecycle changed while removal was in progress.")
            runtime_integration().remove_contributions(runtime=locked_runtime, now=now)
            ProviderRuntimeModelOffer.objects.filter(runtime_account=locked_runtime).update(
                status=SoftDeleteModel.STATUS_DELETED,
                deleted_at=now,
            )
            sources = list(locked_runtime.sources.exclude(status=SoftDeleteModel.STATUS_DELETED))
            for source in sources:
                ModelGroupDeployment.objects.filter(deployment=source).exclude(
                    status=SoftDeleteModel.STATUS_DELETED
                ).update(status=SoftDeleteModel.STATUS_DELETED, deleted_at=now)
                source.delete()
            locked_runtime.share_mode = ProviderRuntimeAccount.SHARE_PRIVATE
            locked_runtime.status = SoftDeleteModel.STATUS_DELETED
            locked_runtime.deleted_at = now
            locked_runtime.container_id = ""
            locked_runtime.internal_login_url = ""
            locked_runtime.internal_api_url = ""
            locked_runtime.encrypted_proxy_api_key = ""
            locked_runtime.last_error = ""
            locked_runtime.save(update_fields=[
                "share_mode", "status", "deleted_at", "container_id", "internal_login_url",
                "internal_api_url", "encrypted_proxy_api_key", "last_error", "updated_at",
            ])
        locked_account.encrypted_key = encrypt_secret("")
        locked_account.encrypted_username = encrypt_secret("")
        locked_account.encrypted_password = encrypt_secret("")
        locked_account.last_login_error = ""
        locked_account.delete()
        locked_account.save(update_fields=[
            "encrypted_key", "encrypted_username", "encrypted_password", "last_login_error", "updated_at",
        ])
        log_audit(
            request=request,
            action="providers.connection.remove",
            actor=request.user,
            resource_type="provider_connection",
            resource_id=locked_account.pk,
            metadata={
                "provider": locked_account.provider.name,
                **runtime_integration().connection_removal_metadata(impact=impact),
            },
        )
    return {"id": str(account.id), "status": SoftDeleteModel.STATUS_DELETED}
