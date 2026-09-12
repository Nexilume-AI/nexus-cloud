from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.audit.services import write_audit_log

from .models import ProviderAccount


BLOCKING_QUOTA_STATUSES = {ProviderAccount.QUOTA_LIMITED, ProviderAccount.QUOTA_EXHAUSTED}


def provider_account_quota_available(account: ProviderAccount | None) -> bool:
    if account is None:
        return False
    if account.quota_status not in BLOCKING_QUOTA_STATUSES:
        return True
    if account.quota_reset_at and account.quota_reset_at <= timezone.now():
        return True
    return False


def provider_account_quota_rank(account: ProviderAccount | None) -> int:
    if account is None:
        return 99
    if not provider_account_quota_available(account):
        return 99
    if account.quota_status == ProviderAccount.QUOTA_AVAILABLE:
        return 0
    return 1


def mark_provider_account_available(*, account: ProviderAccount | None, request=None, reason: str = "") -> None:
    if account is None:
        return
    if account.quota_status == ProviderAccount.QUOTA_AVAILABLE and not account.last_quota_error:
        return
    before = quota_snapshot(account)
    account.quota_status = ProviderAccount.QUOTA_AVAILABLE
    account.quota_reset_at = None
    account.last_quota_error = ""
    account.last_quota_checked_at = timezone.now()
    account.save(update_fields=["quota_status", "quota_reset_at", "last_quota_error", "last_quota_checked_at", "updated_at"])
    write_quota_audit(request=request, account=account, action="providers.account.quota.available", before=before, reason=reason)


def mark_provider_account_limited(
    *,
    account: ProviderAccount | None,
    request=None,
    reason: str,
    retry_after_seconds: int | None = None,
) -> None:
    mark_provider_account_quota_state(
        account=account,
        request=request,
        status=ProviderAccount.QUOTA_LIMITED,
        reason=reason,
        reset_after_seconds=retry_after_seconds or int(getattr(settings, "NEXUS_PROVIDER_RATE_LIMIT_RESET_SECONDS", 60)),
    )


def mark_provider_account_exhausted(
    *,
    account: ProviderAccount | None,
    request=None,
    reason: str,
    retry_after_seconds: int | None = None,
) -> None:
    mark_provider_account_quota_state(
        account=account,
        request=request,
        status=ProviderAccount.QUOTA_EXHAUSTED,
        reason=reason,
        reset_after_seconds=retry_after_seconds or int(getattr(settings, "NEXUS_PROVIDER_QUOTA_DEFAULT_RESET_SECONDS", 86400)),
    )


def mark_provider_account_quota_state(
    *,
    account: ProviderAccount | None,
    request=None,
    status: str,
    reason: str,
    reset_after_seconds: int,
) -> None:
    if account is None:
        return
    before = quota_snapshot(account)
    account.quota_status = status
    account.quota_reset_at = timezone.now() + timedelta(seconds=max(reset_after_seconds, 0))
    account.last_quota_error = str(reason or "")[:512]
    account.last_quota_checked_at = timezone.now()
    account.save(update_fields=["quota_status", "quota_reset_at", "last_quota_error", "last_quota_checked_at", "updated_at"])
    write_quota_audit(request=request, account=account, action=f"providers.account.quota.{status}", before=before, reason=reason)


def quota_snapshot(account: ProviderAccount) -> dict:
    return {
        "quota_status": account.quota_status,
        "quota_reset_at": account.quota_reset_at.isoformat() if account.quota_reset_at else "",
        "quota_remaining_tokens": account.quota_remaining_tokens,
        "quota_remaining_requests": account.quota_remaining_requests,
    }


def write_quota_audit(*, request, account: ProviderAccount, action: str, before: dict, reason: str) -> None:
    write_audit_log(
        request=request,
        actor=getattr(request, "user", None),
        tenant=account.tenant,
        action=action,
        resource_type="provider_account",
        resource_id=account.pk,
        before=before,
        after={**quota_snapshot(account), "reason": str(reason or "")[:256]},
    )
