from __future__ import annotations

from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.utils import timezone
from rest_framework import exceptions

from apps.audit.services import log_audit
from apps.common.jwt import issue_token_pair

from .models import AccountProfile, PasswordResetToken
from .identity import password_context


def login_user(*, request, email: str, password: str) -> dict[str, str]:
    user_model = get_user_model()
    user = user_model.objects.filter(email__iexact=email, is_active=True).first()
    if user is None or not user.check_password(password):
        raise exceptions.AuthenticationFailed("Invalid email or password.")

    tenant_id, project_id = password_context(request=request, user=user)
    profile = ensure_profile(user=user, tenant_id=tenant_id, project_id=project_id)
    profile.last_login_at = timezone.now()
    if tenant_id and profile.tenant_id != tenant_id:
        profile.tenant_id = tenant_id
    if project_id and profile.project_id != project_id:
        profile.project_id = project_id
    profile.save(update_fields=["tenant_id", "project_id", "last_login_at", "updated_at"])

    user.last_login = profile.last_login_at
    user.save(update_fields=["last_login"])
    login(request, user)

    log_audit(
        request=request,
        action="accounts.login",
        actor=user,
        resource_type="user",
        resource_id=user.pk,
    )
    token_tenant_id = tenant_id or profile.tenant_id
    token_project_id = project_id or profile.project_id
    return {
        **issue_token_pair(user=user, tenant_id=token_tenant_id, project_id=token_project_id),
        "tenant_id": token_tenant_id,
    }


def logout_user(*, request) -> dict[str, str]:
    log_audit(
        request=request,
        action="accounts.logout",
        actor=request.user,
        resource_type="user",
        resource_id=request.user.pk,
    )
    logout(request)
    return {"status": "logged_out"}


def ensure_profile(*, user, tenant_id: str = "", project_id: str = "") -> AccountProfile:
    profile, _ = AccountProfile.objects.get_or_create(
        user=user,
        defaults={
            "tenant_id": tenant_id,
            "project_id": project_id,
        },
    )
    return profile


def get_profile_for_user(*, user) -> AccountProfile:
    try:
        return user.account_profile
    except AccountProfile.DoesNotExist:
        return AccountProfile(user=user)


def update_profile(*, request, profile: AccountProfile, data: dict) -> AccountProfile:
    for field in ("display_name", "phone", "company"):
        if field in data:
            setattr(profile, field, data[field])
    tenant_id = getattr(request, "tenant_id", "")
    project_id = getattr(request, "project_id", "")
    if tenant_id and profile.tenant_id != tenant_id:
        profile.tenant_id = tenant_id
    if project_id and profile.project_id != project_id:
        profile.project_id = project_id
    profile.save(
        update_fields=[
            "display_name",
            "phone",
            "company",
            "tenant_id",
            "project_id",
            "updated_at",
        ]
    )
    log_audit(
        request=request,
        action="accounts.profile.update",
        actor=request.user,
        resource_type="account_profile",
        resource_id=profile.pk,
        metadata={"fields": sorted(data.keys())},
    )
    return profile


def change_password(*, request, new_password: str) -> dict[str, str]:
    user = request.user
    user.set_password(new_password)
    user.save(update_fields=["password"])
    if hasattr(request, "session"):
        update_session_auth_hash(request, user)
    log_audit(
        request=request,
        action="accounts.password.change",
        actor=user,
        resource_type="user",
        resource_id=user.pk,
    )
    return {"status": "password_changed"}


def request_password_reset(*, request, email: str) -> dict[str, str]:
    user_model = get_user_model()
    user = user_model.objects.get(email__iexact=email, is_active=True)
    tenant_id = getattr(request, "tenant_id", "")
    project_id = getattr(request, "project_id", "")
    reset_token, _raw_token = PasswordResetToken.create_for_user(
        user=user,
        tenant_id=tenant_id,
        project_id=project_id,
    )
    log_audit(
        request=request,
        action="accounts.password_reset.request",
        actor=user,
        resource_type="password_reset_token",
        resource_id=reset_token.pk,
    )
    return {
        "status": "reset_token_created",
        "reset_token_id": str(reset_token.pk),
        "expires_at": reset_token.expires_at.isoformat(),
    }
