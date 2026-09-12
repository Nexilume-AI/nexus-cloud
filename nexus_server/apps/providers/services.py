from __future__ import annotations

import uuid
from decimal import Decimal

from django.db import transaction
from rest_framework import exceptions, status

from apps.audit.services import log_audit
from apps.audit.snapshots import snapshot_resource
from apps.common.crypto import encrypt_secret
from apps.common.models import SoftDeleteModel
from apps.deployments.models import Deployment
from apps.common.authorization import has_nexus_permission
from apps.common.resource_catalog import resolve_ownership_project
from apps.tenancy.models import Tenant
from apps.common.request_context import get_tenant_from_request

from .models import Provider, ProviderAccount, ProviderRuntimeAccount


class ProviderAccountNotFound(exceptions.APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Provider account not found."
    default_code = "NOT_FOUND"


def list_providers(*, request):
    tenant = get_tenant_from_request(request)
    provider_ids = visible_provider_accounts(
        ProviderAccount.objects.filter(
            tenant=tenant,
            status=SoftDeleteModel.STATUS_ACTIVE,
        )
    ).values_list("provider_id", flat=True)
    return Provider.objects.filter(status=SoftDeleteModel.STATUS_ACTIVE).filter(id__in=provider_ids) | Provider.objects.filter(
        status=SoftDeleteModel.STATUS_ACTIVE,
        name__in=["openai", "qwen", "deepseek"],
    )


@transaction.atomic
def create_provider_account(*, request, data: dict, create_only: bool = False, ownership: dict | None = None) -> ProviderAccount:
    tenant = get_tenant_from_request(request)
    project, _ = resolve_ownership_project(request=request, tenant=tenant, ownership=ownership)
    require_provider_create(request=request, tenant=tenant, project=project)
    provider = get_or_create_provider(data["provider"])
    account_id = data.get("account_id") or generate_provider_account_id(tenant=tenant, provider=provider)
    account = ProviderAccount.objects.filter(
        tenant=tenant,
        provider=provider,
        account_id=account_id,
    ).first()
    if create_only and account is not None:
        raise exceptions.ValidationError("This Provider identity already exists. Preview the import again.")
    if account is not None:
        # Legacy create supports upsert. Creation permission alone must not grant
        # the ability to overwrite another connection's credentials or ownership.
        require_provider_admin(request=request, tenant=tenant)
    before = provider_account_snapshot(account) if account is not None else None
    if account is None:
        from apps.common.resource_limits import enforce_capability

        enforce_capability(tenant=tenant, code="models.provider_connections")
        account = ProviderAccount.objects.create(
            tenant=tenant,
            provider=provider,
            name=(data.get("name") or account_id).strip(),
            account_id=account_id,
            url=data.get("url", ""),
            encrypted_key=encrypt_secret(data.get("key", "")),
            encrypted_username=encrypt_secret(data.get("username", "")),
            encrypted_password=encrypt_secret(data.get("password", "")),
            auth_mode=data.get("auth_mode", ProviderAccount.AUTH_API_KEY),
            preferred_runtime_type=normalized_preferred_runtime_type(data=data),
            login_status=initial_login_status(data.get("auth_mode", ProviderAccount.AUTH_API_KEY)),
            created_by=request.user,
        )
    else:
        account.name = (data.get("name") or account.name or account_id).strip()
        account.url = data.get("url", "")
        account.encrypted_key = encrypt_secret(data.get("key", ""))
        account.encrypted_username = encrypt_secret(data.get("username", ""))
        account.encrypted_password = encrypt_secret(data.get("password", ""))
        account.auth_mode = data.get("auth_mode", ProviderAccount.AUTH_API_KEY)
        account.preferred_runtime_type = normalized_preferred_runtime_type(data=data)
        account.login_status = initial_login_status(account.auth_mode)
        account.last_login_error = ""
        account.last_login_at = None
        account.status = SoftDeleteModel.STATUS_ACTIVE
        account.deleted_at = None
        account.created_by = request.user
        account.save(
            update_fields=[
                "url",
                "name",
                "encrypted_key",
                "encrypted_username",
                "encrypted_password",
                "auth_mode",
                "preferred_runtime_type",
                "login_status",
                "last_login_error",
                "last_login_at",
                "status",
                "deleted_at",
                "created_by",
                "updated_at",
            ]
        )
    log_audit(
        request=request,
        action="providers.account.create",
        actor=request.user,
        resource_type="provider_account",
        resource_id=account.pk,
        before=before,
        after=provider_account_snapshot(account),
        metadata={"provider": provider.name, "account_id": account.account_id},
    )
    runtime = ensure_runtime_for_provider_account(request=request, account=account)
    if before is None and runtime.project_id != (project.pk if project else None):
        # The legacy account endpoint must preserve the same authorized scope.
        runtime.project = project
        runtime.save(update_fields=["project", "updated_at"])
    return account


def remove_provider_account(*, request, account_identifier: str) -> ProviderAccount:
    tenant = get_tenant_from_request(request)
    require_provider_admin(request=request, tenant=tenant)
    account = get_provider_account(tenant=tenant, identifier=account_identifier)
    from .connection_services import remove_provider_connection

    remove_provider_connection(
        request=request,
        account_id=str(account.id),
        confirmation_name=account.name or account.account_id,
    )
    account.refresh_from_db()
    return account


@transaction.atomic
def update_provider_account(*, request, account_identifier: str, data: dict) -> ProviderAccount:
    tenant = get_tenant_from_request(request)
    require_provider_admin(request=request, tenant=tenant)
    account = get_provider_account(tenant=tenant, identifier=account_identifier)
    before = provider_account_snapshot(account)
    changed: list[str] = []
    credentials_changed = False

    if "name" in data:
        account.name = data["name"].strip()
        changed.append("name")
    if "url" in data:
        account.url = data.get("url", "")
        changed.append("url")
    if "pricing_rate" in data:
        account.pricing_rate = data["pricing_rate"]
        changed.append("pricing_rate")
    if "auth_mode" in data:
        account.auth_mode = data["auth_mode"]
        changed.append("auth_mode")
    if "preferred_runtime_type" in data:
        account.preferred_runtime_type = normalized_preferred_runtime_type(data={**data, "auth_mode": account.auth_mode})
        changed.append("preferred_runtime_type")
    if "key" in data:
        account.encrypted_key = encrypt_secret(data.get("key", ""))
        changed.append("encrypted_key")
        credentials_changed = True
    if "username" in data:
        account.encrypted_username = encrypt_secret(data.get("username", ""))
        changed.append("encrypted_username")
        credentials_changed = True
    if "password" in data:
        account.encrypted_password = encrypt_secret(data.get("password", ""))
        changed.append("encrypted_password")
        credentials_changed = True

    if credentials_changed or "auth_mode" in data:
        if "auth_mode" in data and "preferred_runtime_type" not in data:
            account.preferred_runtime_type = normalized_preferred_runtime_type(data={"auth_mode": account.auth_mode})
            changed.append("preferred_runtime_type")
        account.login_status = initial_login_status(account.auth_mode)
        account.last_login_error = ""
        account.last_login_at = None
        changed.extend(["login_status", "last_login_error", "last_login_at"])

    if account.auth_mode == ProviderAccount.AUTH_API_KEY and not account.url:
        raise exceptions.ValidationError("url is required for api_key auth_mode.")
    if account.auth_mode == ProviderAccount.AUTH_API_KEY and not account.encrypted_key:
        raise exceptions.ValidationError("key is required for api_key auth_mode.")
    if account.auth_mode == ProviderAccount.AUTH_USERNAME_PASSWORD_LOGIN and (
        not account.encrypted_username or not account.encrypted_password
    ):
        raise exceptions.ValidationError("username and password are required for username_password_login.")
    if account.auth_mode == ProviderAccount.AUTH_INTERACTIVE_LOGIN and not account.preferred_runtime_type:
        account.preferred_runtime_type = ProviderAccount.PREFERRED_RUNTIME_CODEX_PROXY
        changed.append("preferred_runtime_type")
    if account.auth_mode == ProviderAccount.AUTH_API_KEY and account.preferred_runtime_type:
        account.preferred_runtime_type = ""
        changed.append("preferred_runtime_type")

    account.save(update_fields=sorted(set(changed + ["updated_at"])))
    log_audit(
        request=request,
        action="providers.account.update",
        actor=request.user,
        resource_type="provider_account",
        resource_id=account.pk,
        before=before,
        after=provider_account_snapshot(account),
        metadata={"fields": sorted(data.keys()), "provider": account.provider.name, "account_id": account.account_id},
    )
    if {"auth_mode", "preferred_runtime_type", "url", "pricing_rate"}.intersection(data.keys()):
        ensure_runtime_for_provider_account(request=request, account=account)
    return account


def provider_account_status(*, request, provider_name: str | None):
    tenant = get_tenant_from_request(request)
    queryset = ProviderAccount.objects.filter(tenant=tenant).exclude(status=SoftDeleteModel.STATUS_DELETED).select_related("provider")
    if provider_name:
        queryset = queryset.filter(provider__name=provider_name)
    return visible_provider_accounts(queryset).distinct().order_by("provider__name", "account_id")


def visible_provider_accounts(queryset):
    return queryset.exclude(account_id__startswith="runtime_", runtime_accounts__isnull=False)


def set_provider_pricing(*, request, account_identifier: str, pricing_rate: Decimal) -> ProviderAccount:
    tenant = get_tenant_from_request(request)
    require_provider_admin(request=request, tenant=tenant)
    account = get_provider_account(tenant=tenant, identifier=account_identifier)
    before = provider_account_snapshot(account)
    account.pricing_rate = pricing_rate
    account.save(update_fields=["pricing_rate", "updated_at"])
    log_audit(
        request=request,
        action="providers.account.pricing.set",
        actor=request.user,
        resource_type="provider_account",
        resource_id=account.pk,
        before=before,
        after=provider_account_snapshot(account),
        metadata={"pricing_rate": str(pricing_rate)},
    )
    return account


def get_provider_account(*, tenant: Tenant, identifier: str) -> ProviderAccount:
    account = None
    if is_uuid(identifier):
        account = ProviderAccount.objects.filter(tenant=tenant, id=identifier).first()
    if account is None:
        account = ProviderAccount.objects.filter(tenant=tenant, account_id=identifier).first()
    if account is None:
        raise ProviderAccountNotFound()
    return account


def is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def get_or_create_provider(name: str) -> Provider:
    provider, _ = Provider.objects.get_or_create(
        name=name,
        defaults={"display_name": name.title()},
    )
    return provider


def initial_login_status(auth_mode: str) -> str:
    if auth_mode == ProviderAccount.AUTH_USERNAME_PASSWORD_LOGIN:
        return ProviderAccount.LOGIN_REQUIRED
    if auth_mode == ProviderAccount.AUTH_INTERACTIVE_LOGIN:
        return ProviderAccount.LOGIN_REQUIRED
    return ProviderAccount.LOGIN_UNKNOWN


def generate_provider_account_id(*, tenant: Tenant, provider: Provider) -> str:
    base = "default"
    candidate = base
    suffix = 2
    while ProviderAccount.objects.filter(tenant=tenant, provider=provider, account_id=candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def runtime_type_for_auth_mode(auth_mode: str) -> str:
    if auth_mode == ProviderAccount.AUTH_API_KEY:
        return ProviderRuntimeAccount.RUNTIME_DIRECT_API
    if auth_mode == ProviderAccount.AUTH_USERNAME_PASSWORD_LOGIN:
        return ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI
    return ProviderRuntimeAccount.RUNTIME_CODEX_PROXY


def runtime_type_for_provider_account(account: ProviderAccount) -> str:
    if account.auth_mode == ProviderAccount.AUTH_API_KEY:
        return ProviderRuntimeAccount.RUNTIME_DIRECT_API
    if account.auth_mode == ProviderAccount.AUTH_USERNAME_PASSWORD_LOGIN:
        return ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI
    if account.preferred_runtime_type == ProviderAccount.PREFERRED_RUNTIME_CLIPROXYAPI:
        return ProviderRuntimeAccount.RUNTIME_CLIPROXYAPI
    return ProviderRuntimeAccount.RUNTIME_CODEX_PROXY


def normalized_preferred_runtime_type(*, data: dict) -> str:
    auth_mode = data.get("auth_mode", ProviderAccount.AUTH_API_KEY)
    if auth_mode == ProviderAccount.AUTH_API_KEY:
        return ""
    if auth_mode == ProviderAccount.AUTH_USERNAME_PASSWORD_LOGIN:
        return ProviderAccount.PREFERRED_RUNTIME_CLIPROXYAPI
    preferred = data.get("preferred_runtime_type") or ProviderAccount.PREFERRED_RUNTIME_CODEX_PROXY
    if preferred not in {
        ProviderAccount.PREFERRED_RUNTIME_CODEX_PROXY,
        ProviderAccount.PREFERRED_RUNTIME_CLIPROXYAPI,
    }:
        raise exceptions.ValidationError("preferred_runtime_type must be codex_proxy or cliproxyapi.")
    return preferred


def default_model_for_provider(provider_name: str) -> str:
    defaults = {
        "openai": "gpt-4o",
        "qwen": "qwen-max",
        "deepseek": "deepseek-chat",
        "codex-login": "gpt-5-codex",
    }
    return defaults.get(provider_name, f"{provider_name}-model")


def runtime_name_for_provider_account(*, account: ProviderAccount, runtime_type: str) -> str:
    return f"{account.name or account.account_id}-{runtime_type.replace('_', '-')}"


@transaction.atomic
def ensure_runtime_for_provider_account(*, request, account: ProviderAccount, model: str = "") -> ProviderRuntimeAccount:
    account = ProviderAccount.objects.select_for_update().select_related("provider").get(id=account.id)
    runtime_type = runtime_type_for_provider_account(account)
    model = model.strip() or default_model_for_provider(account.provider.name)
    runtime = (
        ProviderRuntimeAccount.objects.filter(source_provider_account=account)
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .order_by("-created_at")
        .first()
    )
    if runtime is None:
        runtime = ProviderRuntimeAccount.objects.create(
            tenant=account.tenant,
            owner=request.user,
            source_provider_account=account,
            name=runtime_name_for_provider_account(account=account, runtime_type=runtime_type),
            runtime_type=runtime_type,
            public_login_path="",
        )
        runtime.public_login_path = f"/api/v1/provider-runtimes/{runtime.id}/login/"
        runtime.save(update_fields=["public_login_path", "updated_at"])
        log_audit(
            request=request,
            action="providers.runtime.create",
            actor=request.user,
            resource_type="provider_runtime",
            resource_id=runtime.pk,
            metadata={"runtime_type": runtime.runtime_type, "status": runtime.status, "source_account_id": str(account.id)},
        )
        return runtime

    if runtime.status != ProviderRuntimeAccount.STATUS_ACTIVE:
        runtime.runtime_type = runtime_type
        runtime.name = runtime_name_for_provider_account(account=account, runtime_type=runtime_type)
        runtime.save(update_fields=["runtime_type", "name", "updated_at"])
    return runtime


def ensure_provider_catalog() -> None:
    for name in ("openai", "qwen", "deepseek"):
        get_or_create_provider(name)


def can_create_provider(*, request, tenant: Tenant, project=None) -> bool:
    return has_nexus_permission(
        request.user, tenant, "provider.create",
        resource_type="project" if project is not None else None,
        resource_id=str(project.pk) if project is not None else None,
    )


def require_provider_create(*, request, tenant: Tenant, project=None) -> None:
    if not can_create_provider(request=request, tenant=tenant, project=project):
        raise exceptions.PermissionDenied("Provider creation permission is required in the selected scope.")


def require_provider_admin(*, request, tenant: Tenant) -> None:
    if not has_nexus_permission(request.user, tenant, "admin"):
        raise exceptions.PermissionDenied("Tenant admin permission is required.")


def provider_account_snapshot(account: ProviderAccount | None) -> dict | None:
    if account is None:
        return None
    provider_name = account.provider.name if getattr(account, "provider_id", None) else ""
    return snapshot_resource("provider_account", account, extra={"provider": provider_name})
