from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone
from django.db import connection, close_old_connections, transaction
from django.db.models import F, Q

from apps.common.crypto import decrypt_secret

from .models import InboxItem, InboxPreference, PushDelivery, WebPushSubscription


def configuration_status() -> tuple[bool, str]:
    if not getattr(settings, "NEXUS_WEB_PUSH_ENABLED", False):
        return False, "disabled"
    subject = str(getattr(settings, "NEXUS_VAPID_SUBJECT", "") or "")
    if not all((getattr(settings, "NEXUS_VAPID_PUBLIC_KEY", ""), getattr(settings, "NEXUS_VAPID_PRIVATE_KEY", ""), subject)):
        return False, "vapid_configuration_missing"
    if not subject.startswith(("mailto:", "https://")):
        return False, "vapid_subject_invalid"
    public_base = str(getattr(settings, "NEXUS_PUBLIC_BASE_URL", "") or "")
    if getattr(settings, "NEXUS_PRODUCTION", False) and not public_base.startswith("https://"):
        return False, "https_required"
    return True, "ready"


def _event(item: InboxItem) -> tuple[str, str] | None:
    if item.state == InboxItem.STATE_NEEDS_ACTION:
        return "action_required", "A task needs your attention"
    if item.state == InboxItem.STATE_FAILED:
        return "failed", "A background task failed"
    if item.state == InboxItem.STATE_COMPLETED and item.audience_type == InboxItem.AUDIENCE_PERSONAL:
        return "completed", "Your background task completed"
    return None


def _quiet(pref: InboxPreference, now=None) -> bool:
    if not pref.dnd_enabled or pref.dnd_start is None or pref.dnd_end is None:
        return False
    local = (now or timezone.now()).astimezone(ZoneInfo(pref.timezone))
    current = local.time().replace(tzinfo=None)
    if pref.dnd_start <= pref.dnd_end:
        return pref.dnd_start <= current < pref.dnd_end
    return current >= pref.dnd_start or current < pref.dnd_end


def _allowed(item: InboxItem, pref: InboxPreference) -> bool:
    event = _event(item)
    if event is None:
        return False
    categories = {"agent": True, "background": True, "approval": True, "operations": True, **(pref.category_preferences or {})}
    events = {"action_required": True, "failed": True, "completed": True, **(pref.event_preferences or {})}
    if not categories.get(item.category, True) or not events.get(event[0], True):
        return False
    return not _quiet(pref) or (pref.urgent_bypass and item.priority >= 100)


def _subscription_info(row: WebPushSubscription) -> dict:
    return {"endpoint": decrypt_secret(row.endpoint_encrypted), "keys": {
        "p256dh": decrypt_secret(row.p256dh_encrypted), "auth": decrypt_secret(row.auth_encrypted),
    }}


class _PublicPushSession:
    """pywebpush transport: one public DNS-pinned HTTPS request, no redirects/proxies.

    Provider private-host exceptions must never authorize a caller-supplied Push
    endpoint. Status is sufficient for delivery/retry; discard response bodies
    so a malicious Push service cannot echo credentials into library errors.
    """

    def post(self, url, *, data, headers, timeout):
        from requests import Response
        from urllib.error import HTTPError
        from apps.gateway.provider_http import open_provider_url

        result = Response()
        result._content = b""
        result.reason = "Push delivery status"
        try:
            with open_provider_url(url, method="POST", body=data, headers=headers,
                    timeout=min(5, max(0.1, float(timeout or 5))),
                    public_https_only=True, error_body_limit=0) as response:
                result.status_code = response.status
        except HTTPError as exc:
            # Preserve 404/410 and retry handling without following Location.
            result.status_code = exc.code
        return result


def _deliver(subscription: WebPushSubscription, payload: dict) -> None:
    enabled, reason = configuration_status()
    if not enabled:
        raise RuntimeError(reason)
    from pywebpush import webpush
    webpush(
        subscription_info=_subscription_info(subscription),
        data=json.dumps(payload, separators=(",", ":")),
        vapid_private_key=settings.NEXUS_VAPID_PRIVATE_KEY,
        vapid_claims={"sub": settings.NEXUS_VAPID_SUBJECT},
        timeout=5,
        requests_session=_PublicPushSession(),
    )


def send_test_push(subscription: WebPushSubscription) -> None:
    _deliver(subscription, {"type": "test", "title": "Nexilume AI", "body": "Desktop notifications are ready", "url": "/inbox", "tag": "nexus-inbox-test"})


def claim_deliveries(limit):
    now = timezone.now()
    due = Q(status__in=[PushDelivery.STATUS_PENDING, PushDelivery.STATUS_FAILED], next_attempt_at__lte=now)
    due |= Q(status=PushDelivery.STATUS_SENDING, lease_expires_at__lte=now)
    with transaction.atomic():
        eligible = PushDelivery.objects.filter(due, subscription__enabled=True)
        # A worker crash after the last attempt must not leave an eternal lease.
        eligible.filter(attempts__gte=8).update(status=PushDelivery.STATUS_EXHAUSTED, error_code="retry_exhausted",
                                               lease_token=None, lease_expires_at=None, updated_at=now)
        rows = list(eligible.filter(attempts__lt=8).select_for_update(skip_locked=True, of=("self",)).order_by(
            "next_attempt_at", "pk",
        )[:limit])
        for row in rows:
            row.lease_token = uuid.uuid4()
            row.lease_expires_at = now + timedelta(seconds=60)
            row.status = PushDelivery.STATUS_SENDING
            row.attempts += 1
            row.save(update_fields=["lease_token", "lease_expires_at", "status", "attempts", "updated_at"])
    return [(row.pk, row.lease_token) for row in rows]


def _send_claimed(claim):
    pk, lease = claim
    delivery = PushDelivery.objects.filter(pk=pk, lease_token=lease, status=PushDelivery.STATUS_SENDING,
        subscription__enabled=True).select_related("item__tenant", "subscription__user").first()
    if delivery is None:
        return "skipped"
    now = timezone.now()

    def finish(status, code="", *, next_at=None):
        # A source transition can reset this delivery while it is in flight.
        # The previous generation must never overwrite the new pending event.
        updated = PushDelivery.objects.filter(pk=pk, lease_token=lease, status=PushDelivery.STATUS_SENDING).update(
            status=status, error_code=code, next_attempt_at=next_at or now,
            sent_at=now if status == PushDelivery.STATUS_SENT else None,
            lease_token=None, lease_expires_at=None, updated_at=now,
        )
        return updated

    try:
        from .inbox import refresh_item
        item, subscription = refresh_item(delivery.item), delivery.subscription
        if item.audience_type == InboxItem.AUDIENCE_PERSONAL:
            audience_allowed = item.recipient_id == subscription.user_id
        else:
            from .inbox import user_can_receive_role_item
            audience_allowed = user_can_receive_role_item(item, subscription.user)
        if not audience_allowed:
            finish(PushDelivery.STATUS_SKIPPED, "permission_revoked")
            return "skipped"
        from .models import InboxReceipt
        receipt = InboxReceipt.objects.filter(item=item, user=subscription.user).first()
        if receipt and receipt.snoozed_until and receipt.snoozed_until > now:
            PushDelivery.objects.filter(pk=pk, lease_token=lease).update(attempts=F("attempts") - 1)
            finish(PushDelivery.STATUS_PENDING, "snoozed", next_at=receipt.snoozed_until)
            return "skipped"
        preference, _ = InboxPreference.objects.get_or_create(tenant=item.tenant, user=subscription.user)
        event = _event(item)
        if event is None:
            finish(PushDelivery.STATUS_SKIPPED, "source_inactive")
            return "skipped"
        if not _allowed(item, preference):
            PushDelivery.objects.filter(pk=pk, lease_token=lease).update(attempts=F("attempts") - 1)
            quiet = _quiet(preference)
            finish(PushDelivery.STATUS_PENDING if quiet else PushDelivery.STATUS_SKIPPED,
                   "quiet_hours" if quiet else "preference_disabled", next_at=now + timedelta(minutes=15))
            return "skipped"
        payload = {"type": event[0], "title": "Nexilume AI", "body": event[1],
                   "item_id": str(item.pk), "url": f"/inbox?item={item.pk}", "tag": f"nexus-inbox-{item.pk}"}
        if not PushDelivery.objects.filter(pk=pk, lease_token=lease, status=PushDelivery.STATUS_SENDING,
            lease_expires_at__gt=timezone.now() + timedelta(seconds=10), subscription__enabled=True,
            subscription__user__is_active=True).exists():
            return "skipped"
        try:
            _deliver(subscription, payload)
        except Exception as exc:  # pywebpush exposes response status inconsistently across versions.
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            code = f"http_{status_code}" if status_code else "delivery_failed"
            if status_code in {404, 410}:
                subscription.enabled, subscription.disabled_at = False, now
                subscription.failure_count += 1
                subscription.save(update_fields=["enabled", "disabled_at", "failure_count", "updated_at"])
                finish(PushDelivery.STATUS_EXHAUSTED, code)
            else:
                subscription.failure_count += 1
                subscription.save(update_fields=["failure_count", "updated_at"])
                finish(PushDelivery.STATUS_EXHAUSTED if delivery.attempts >= 8 else PushDelivery.STATUS_FAILED,
                       code, next_at=now + timedelta(minutes=min(60, 2 ** delivery.attempts)))
            return "failed"
        recorded = finish(PushDelivery.STATUS_SENT)
        if subscription.failure_count:
            subscription.failure_count = 0
            subscription.save(update_fields=["failure_count", "updated_at"])
        if recorded:
            InboxReceipt.objects.update_or_create(item=item, user=subscription.user, defaults={"last_push_at": now})
        return "sent"
    except Exception:
        finish(PushDelivery.STATUS_EXHAUSTED if delivery.attempts >= 8 else PushDelivery.STATUS_FAILED,
               "processing_failed", next_at=now + timedelta(minutes=min(60, 2 ** delivery.attempts)))
        return "failed"


def _thread_send(claim):
    close_old_connections()
    try:
        return _send_claimed(claim)
    finally:
        connection.close()


def deliver_pending_pushes(limit=100) -> dict:
    counts = {"sent": 0, "failed": 0, "skipped": 0}
    limit = max(0, min(100, int(limit)))
    workers = max(1, min(8, int(getattr(settings, "NEXUS_INBOX_PUSH_CONCURRENCY", 4))))
    # TestCase and transactional callers must retain their own DB connection.
    if connection.in_atomic_block:
        workers = 1
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for offset in range(0, limit, workers):
            claims = claim_deliveries(min(workers, limit - offset))
            if not claims:
                break
            results = map(_send_claimed, claims) if workers == 1 else executor.map(_thread_send, claims)
            for result in results:
                counts[result] += 1
    return counts
