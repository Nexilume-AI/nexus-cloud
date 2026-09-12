from __future__ import annotations

import hashlib
import json
import asyncio
from datetime import datetime, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import Count, Max, Q
from asgiref.sync import sync_to_async
from django.http import StreamingHttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_time
from rest_framework import exceptions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.crypto import encrypt_secret
from apps.common.request_context import get_tenant_from_request

from .inbox import ACTIVE_STATES, FINAL_STATES, navigation_for, receipt_for, refresh_item, require_personal_user, serialize_item, visible_items
from .models import InboxItem, InboxPreference, InboxReceipt, WebPushSubscription
from .querying import inbox_query, with_receipts


SALT = "work-inbox-v1"
DEFAULT_CATEGORIES = {"agent": True, "background": True, "approval": True, "operations": True}
DEFAULT_EVENTS = {"action_required": True, "failed": True, "completed": True}


def private(data, status=200):
    response = Response(data, status=status)
    response["Cache-Control"] = "private, no-store"
    return response


def _cursor_scope(request):
    return {"user": str(request.user.pk), "tenant": str(request.tenant_id),
            "filters": {key: request.query_params.get(key, "") for key in ("state", "category", "project", "ownership", "q")}}


def _encode_cursor(request, item):
    return signing.dumps({**_cursor_scope(request), "priority": item.priority, "due": item._due_sort.isoformat(),
                          "at": item.occurred_at.isoformat(), "id": str(item.pk)}, salt=SALT, compress=True)


def _decode_cursor(request, value):
    try:
        data = signing.loads(value, salt=SALT, max_age=3600)
        if any(data.get(key) != expected for key, expected in _cursor_scope(request).items()):
            raise signing.BadSignature()
        return data
    except (signing.BadSignature, ValueError, TypeError):
        raise exceptions.ValidationError("Inbox cursor expired. Refresh and retry.") from None


def _filtered(request):
    queryset = inbox_query(request)
    category = request.query_params.get("category", "")
    if category:
        allowed = {value for value, _ in InboxItem.CATEGORY_CHOICES}
        if category not in allowed:
            raise exceptions.ValidationError({"category": "Unsupported Inbox category."})
        queryset = queryset.filter(category=category)
    ownership = request.query_params.get("ownership", "")
    if ownership:
        if ownership not in {"personal", "role"}:
            raise exceptions.ValidationError({"ownership": "Choose personal or role."})
        queryset = queryset.filter(audience_type="personal" if ownership == "personal" else "role")
    project = request.query_params.get("project", "")
    if project:
        queryset = queryset.filter(project_id=serializers.UUIDField().run_validation(project))
    query = request.query_params.get("q", "").strip()[:120]
    if query:
        from django.db.models import Q
        queryset = queryset.filter(Q(kind__icontains=query) | Q(title_key__icontains=query) | Q(project__name__icontains=query) | Q(safe_context__resource_name__icontains=query) | Q(safe_context__agent_name__icontains=query))
    return queryset


def _summary(request):
    awake = Q(_snoozed_until__isnull=True) | Q(_snoozed_until__lte=timezone.now())
    counts = inbox_query(request).filter(_archived_at__isnull=True).aggregate(
        total=Count("pk"), unread=Count("pk", filter=awake & Q(_read_at__isnull=True)),
        needs_attention=Count("pk", filter=awake & Q(_live_state="needs_action")),
        in_progress=Count("pk", filter=awake & Q(_live_state="in_progress")),
        failed=Count("pk", filter=awake & Q(_live_state="failed")),
        completed_unread=Count("pk", filter=awake & Q(_live_state="completed", _read_at__isnull=True)),
        item_revision=Max("updated_at"), receipt_revision=Max("_updated_at"),
    )
    revisions = [counts.pop(key) for key in ("item_revision", "receipt_revision")]
    revision = max((value for value in revisions if value), default=None)
    return {**counts, "badge_count": counts["needs_attention"], "revision": revision.isoformat() if revision else "0"}


def _revision(request):
    # No source refresh, serialization, or full summary on the SSE heartbeat.
    # Recheck authorization after role/membership changes even on an open stream.
    request.__dict__.pop("_inbox_visibility", None)
    rows = visible_items(request)
    items = rows.aggregate(at=Max("updated_at"), total=Count("pk"))
    receipt = InboxReceipt.objects.filter(user=request.user, item_id__in=rows.values("pk")).aggregate(at=Max("updated_at"))["at"]
    return f"{items['at']}:{items['total']}:{receipt}"


class InboxSummaryView(APIView):
    def get(self, request):
        from .push import configuration_status
        push_ready, push_status = configuration_status()
        enabled_devices = WebPushSubscription.objects.filter(user=request.user, enabled=True).count()
        return private({**_summary(request), "retention_days": 90, "push_enabled": push_ready,
                        "push_status": push_status, "enabled_push_devices": enabled_devices})


class InboxListView(APIView):
    def get(self, request):
        from django.db.models import DateTimeField, Value
        from django.db.models.functions import Coalesce
        queryset = _filtered(request).filter(_archived_at__isnull=True).select_related("project").annotate(
            _due_sort=Coalesce("due_at", Value(datetime.max.replace(tzinfo=datetime_timezone.utc)), output_field=DateTimeField()),
        )
        state = request.query_params.get("state", "")
        now = timezone.now()
        awake = Q(_snoozed_until__isnull=True) | Q(_snoozed_until__lte=now)
        if state:
            allowed = {value for value, _ in InboxItem.STATE_CHOICES} | {"snoozed", "completed_unread"}
            if state not in allowed:
                raise exceptions.ValidationError({"state": "Unsupported Inbox state."})
            if state == "snoozed":
                queryset = queryset.filter(_snoozed_until__gt=now)
            elif state == "completed_unread":
                queryset = queryset.filter(awake, _live_state="completed", _read_at__isnull=True)
            else:
                queryset = queryset.filter(awake, _live_state=state)
        else:
            queryset = queryset.filter(awake)
        cursor = request.query_params.get("cursor")
        if cursor:
            data = _decode_cursor(request, cursor)
            queryset = queryset.filter(
                Q(priority__lt=data["priority"]) |
                Q(priority=data["priority"], _due_sort__gt=data["due"]) |
                Q(priority=data["priority"], _due_sort=data["due"], occurred_at__lt=data["at"]) |
                Q(priority=data["priority"], _due_sort=data["due"], occurred_at=data["at"], id__lt=data["id"])
            )
        rows = list(queryset.order_by("-priority", "_due_sort", "-occurred_at", "-id")[:31])
        return private({
            "results": [serialize_item(item, request.user) for item in rows[:30]],
            "next_cursor": _encode_cursor(request, rows[29]) if len(rows) > 30 else None,
            # Old clients retain counts; the current UI shares its summary query.
            "counts": _summary(request) if request.query_params.get("include_counts", "1") != "0" else None,
        })


class InboxDetailView(APIView):
    def get(self, request, item_id):
        item = inbox_query(request).select_related("project").filter(pk=item_id).first()
        if item is None:
            raise exceptions.NotFound("Inbox item not found.")
        return private(serialize_item(item, request.user))


def _get_item(request, item_id):
    item = visible_items(request).select_related("project").filter(pk=item_id).first()
    if item is None:
        raise exceptions.NotFound("Inbox item not found.")
    return refresh_item(item)


class InboxReadView(APIView):
    def post(self, request, item_id):
        item = _get_item(request, item_id)
        receipt = receipt_for(item, request.user)
        if receipt.read_at is None:
            receipt.read_at = timezone.now()
            receipt.save(update_fields=["read_at", "updated_at"])
        return private({"id": str(item.pk), "is_read": True})


class InboxReadAllView(APIView):
    @transaction.atomic
    def post(self, request):
        now = timezone.now()
        # Bounded continuation with a fixed snapshot: never acknowledge arrivals
        # newer than the user's first click. Each page rechecks visibility.
        token = request.data.get("cursor")
        if token:
            try:
                snapshot = signing.loads(token, salt="inbox-read-all", max_age=3600)
                if snapshot["user"] != str(request.user.pk) or snapshot["tenant"] != str(request.tenant_id):
                    raise signing.BadSignature()
            except (signing.BadSignature, KeyError, TypeError, ValueError):
                raise exceptions.ValidationError("Read snapshot expired. Refresh and retry.") from None
        else:
            snapshot = {"user": str(request.user.pk), "tenant": str(request.tenant_id), "at": now.isoformat(), "after": None}
        queryset = with_receipts(visible_items(request), request.user).filter(
            _archived_at__isnull=True, created_at__lte=snapshot["at"], _read_at__isnull=True,
        )
        if snapshot["after"]:
            queryset = queryset.filter(pk__gt=snapshot["after"])
        rows = list(queryset.order_by("pk").only("id")[:501])
        items = rows[:500]
        existing = {receipt.item_id: receipt for receipt in InboxReceipt.objects.select_for_update().filter(user=request.user, item__in=items)}
        create = []
        update = []
        for item in items:
            receipt = existing.get(item.pk)
            if receipt is None:
                create.append(InboxReceipt(item=item, user=request.user, read_at=now))
            elif receipt.read_at is None:
                receipt.read_at = now
                receipt.updated_at = now
                update.append(receipt)
        InboxReceipt.objects.bulk_create(create, ignore_conflicts=True)
        if update:
            InboxReceipt.objects.bulk_update(update, ["read_at", "updated_at"], batch_size=500)
        snapshot["after"] = str(items[-1].pk) if items else None
        return private({"marked_read": len(create) + len(update), "next_cursor":
                        signing.dumps(snapshot, salt="inbox-read-all") if len(rows) > 500 else None})


class InboxSnoozeView(APIView):
    def post(self, request, item_id):
        item = _get_item(request, item_id)
        if item.state in FINAL_STATES:
            raise exceptions.ValidationError("Completed or resolved work cannot be snoozed.")
        until = parse_datetime(str(request.data.get("until", "")))
        if until is None or until <= timezone.now() or until > timezone.now() + timedelta(days=30):
            raise exceptions.ValidationError({"until": "Choose a time within the next 30 days."})
        receipt = receipt_for(item, request.user)
        receipt.snoozed_until = until
        receipt.save(update_fields=["snoozed_until", "updated_at"])
        return private({"id": str(item.pk), "state": "snoozed", "snoozed_until": until})


class InboxArchiveView(APIView):
    def post(self, request, item_id):
        item = _get_item(request, item_id)
        if item.state not in FINAL_STATES:
            raise exceptions.ValidationError("Unresolved work cannot be archived. Use Remind later instead.")
        receipt = receipt_for(item, request.user)
        receipt.archived_at = timezone.now()
        receipt.save(update_fields=["archived_at", "updated_at"])
        return private({"id": str(item.pk), "archived": True})


class InboxOpenView(APIView):
    def post(self, request, item_id):
        tenant = get_tenant_from_request(request)
        item = _get_item(request, item_id)
        receipt = receipt_for(item, request.user)
        if receipt.read_at is None:
            receipt.read_at = timezone.now()
            receipt.save(update_fields=["read_at", "updated_at"])
        return private({"url": navigation_for(item), "tenant_id": str(tenant.pk), "project_id": str(item.project_id) if item.project_id else None})


def _preference(pref):
    from .push import configuration_status
    push_ready, push_status = configuration_status()
    categories = {**DEFAULT_CATEGORIES, **(pref.category_preferences or {})}
    events = {**DEFAULT_EVENTS, **(pref.event_preferences or {})}
    return {"categories": categories, "events": events, "timezone": pref.timezone,
            "dnd_enabled": pref.dnd_enabled, "dnd_start": pref.dnd_start, "dnd_end": pref.dnd_end,
            "urgent_bypass": pref.urgent_bypass,
            "push_enabled": push_ready, "push_status": push_status,
            "vapid_public_key": getattr(settings, "NEXUS_VAPID_PUBLIC_KEY", "") if push_ready else ""}


class InboxPreferenceView(APIView):
    def get(self, request):
        require_personal_user(request)
        tenant = get_tenant_from_request(request)
        pref, _ = InboxPreference.objects.get_or_create(tenant=tenant, user=request.user)
        return private(_preference(pref))

    def put(self, request):
        require_personal_user(request)
        tenant = get_tenant_from_request(request)
        pref, _ = InboxPreference.objects.get_or_create(tenant=tenant, user=request.user)
        categories = request.data.get("categories", {})
        events = request.data.get("events", {})
        if not isinstance(categories, dict) or set(categories) - set(DEFAULT_CATEGORIES):
            raise exceptions.ValidationError({"categories": "Unsupported notification category."})
        if not isinstance(events, dict) or set(events) - set(DEFAULT_EVENTS):
            raise exceptions.ValidationError({"events": "Unsupported notification event."})
        if any(not isinstance(value, bool) for value in categories.values()) or any(not isinstance(value, bool) for value in events.values()):
            raise exceptions.ValidationError("Notification switches must be true or false.")
        zone = str(request.data.get("timezone", pref.timezone or "UTC"))
        try:
            ZoneInfo(zone)
        except ZoneInfoNotFoundError:
            raise exceptions.ValidationError({"timezone": "Use a valid IANA timezone."}) from None
        dnd_enabled = request.data.get("dnd_enabled", False)
        urgent_bypass = request.data.get("urgent_bypass", False)
        if not isinstance(dnd_enabled, bool) or not isinstance(urgent_bypass, bool):
            raise exceptions.ValidationError("Quiet hour switches must be true or false.")
        start = parse_time(str(request.data.get("dnd_start", ""))) if request.data.get("dnd_start") else None
        end = parse_time(str(request.data.get("dnd_end", ""))) if request.data.get("dnd_end") else None
        if dnd_enabled and (start is None or end is None):
            raise exceptions.ValidationError("Quiet hours require a start and end time.")
        pref.category_preferences = {key: bool(value) for key, value in categories.items()}
        pref.event_preferences = {key: bool(value) for key, value in events.items()}
        pref.timezone, pref.dnd_enabled, pref.dnd_start, pref.dnd_end = zone, dnd_enabled, start, end
        pref.urgent_bypass = urgent_bypass
        pref.save()
        # Reconsider preference-skipped deliveries after an explicit settings
        # change. The worker still rechecks source state, permission, and DND.
        from .models import PushDelivery
        PushDelivery.objects.filter(
            subscription__user=request.user,
            subscription__enabled=True,
            status=PushDelivery.STATUS_SKIPPED,
            error_code="preference_disabled",
        ).update(status=PushDelivery.STATUS_PENDING, attempts=0, next_attempt_at=timezone.now(), error_code="")
        return private(_preference(pref))


class PushSubscriptionView(APIView):
    def get(self, request):
        require_personal_user(request)
        rows = WebPushSubscription.objects.filter(user=request.user).order_by("-last_seen_at")
        return private({"results": [{"id": str(row.pk), "device_name": row.device_name, "enabled": row.enabled,
                                     "last_seen_at": row.last_seen_at} for row in rows]})

    def post(self, request):
        require_personal_user(request)
        from .push import configuration_status
        push_ready, push_status = configuration_status()
        if not push_ready:
            error = exceptions.APIException(f"Desktop notifications are unavailable: {push_status}.")
            error.status_code = 503
            error.default_code = "push_unavailable"
            raise error
        endpoint = str(request.data.get("endpoint", ""))
        keys = request.data.get("keys") or {}
        p256dh, auth = str(keys.get("p256dh", "")), str(keys.get("auth", ""))
        if not endpoint.startswith("https://") or len(endpoint) > 4096 or not p256dh or not auth:
            raise exceptions.ValidationError("A valid browser PushSubscription is required.")
        digest = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
        existing = WebPushSubscription.objects.filter(endpoint_hash=digest).first()
        if existing and existing.user_id != request.user.pk and existing.enabled:
            raise exceptions.NotFound("Push subscription not found.")
        subscription, _ = WebPushSubscription.objects.update_or_create(endpoint_hash=digest, defaults={
            "user": request.user, "endpoint_encrypted": encrypt_secret(endpoint), "p256dh_encrypted": encrypt_secret(p256dh),
            "auth_encrypted": encrypt_secret(auth), "device_name": str(request.data.get("device_name") or "This browser")[:80],
            "user_agent": request.headers.get("User-Agent", "")[:256], "enabled": True, "disabled_at": None,
            "failure_count": 0, "last_seen_at": timezone.now(),
        })
        # A newly enabled browser should receive only currently actionable or
        # failed work it can see. Historical completion notices are never
        # replayed as desktop notifications.
        from .models import PushDelivery
        active_items = inbox_query(request).filter(
            _live_state__in=[InboxItem.STATE_NEEDS_ACTION, InboxItem.STATE_FAILED], _archived_at__isnull=True,
        ).filter(Q(_snoozed_until__isnull=True) | Q(_snoozed_until__lte=timezone.now()))
        from itertools import islice
        ids = active_items.values_list("pk", flat=True).iterator(chunk_size=200)
        while batch := list(islice(ids, 200)):
            PushDelivery.objects.bulk_create(
                [PushDelivery(item_id=pk, subscription=subscription) for pk in batch], ignore_conflicts=True, batch_size=200,
            )
            PushDelivery.objects.filter(subscription=subscription, item_id__in=batch,
                status__in=[PushDelivery.STATUS_FAILED, PushDelivery.STATUS_EXHAUSTED, PushDelivery.STATUS_SKIPPED]).update(
                    status=PushDelivery.STATUS_PENDING, attempts=0, next_attempt_at=timezone.now(), error_code="",
                    lease_token=None, lease_expires_at=None, updated_at=timezone.now(),
                )
        return private({"id": str(subscription.pk), "device_name": subscription.device_name, "enabled": True}, status=201)

    def delete(self, request):
        require_personal_user(request)
        subscription_id = serializers.UUIDField().run_validation(request.data.get("id"))
        row = WebPushSubscription.objects.filter(pk=subscription_id, user=request.user).first()
        if row is None:
            raise exceptions.NotFound("Push subscription not found.")
        row.enabled, row.disabled_at = False, timezone.now()
        row.save(update_fields=["enabled", "disabled_at", "updated_at"])
        return private({}, status=204)


class PushSubscriptionTestView(APIView):
    def post(self, request):
        require_personal_user(request)
        subscription_id = serializers.UUIDField().run_validation(request.data.get("id"))
        row = WebPushSubscription.objects.filter(pk=subscription_id, user=request.user, enabled=True).first()
        if row is None:
            raise exceptions.NotFound("Push subscription not found.")
        from .push import send_test_push
        try:
            send_test_push(row)
        except Exception:
            error = exceptions.APIException("Desktop notification test could not be delivered.")
            error.status_code = 503
            error.default_code = "push_test_failed"
            raise error from None
        return private({"sent": True})


class InboxStreamView(APIView):
    def get(self, request):
        require_personal_user(request)
        get_tenant_from_request(request)
        initial = _revision(request)

        async def stream():
            revision = initial
            yield f"event: ready\ndata: {json.dumps({'revision': revision})}\n\n"
            for _ in range(30):
                await asyncio.sleep(10)
                current = await sync_to_async(_revision, thread_sensitive=True)(request)
                if current != revision:
                    revision = current
                    yield f"event: invalidate\ndata: {json.dumps({'revision': revision})}\n\n"
                else:
                    yield ": heartbeat\n\n"

        response = StreamingHttpResponse(stream(), content_type="text/event-stream")
        response["Cache-Control"] = "private, no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        return response
