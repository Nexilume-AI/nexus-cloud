from django.db.models import Count, Exists, F, OuterRef, Q, Subquery
from django.db.models.functions import Substr
from django.utils import timezone
from rest_framework import exceptions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AgentDisplayRun, AgentRunInteraction, AgentRunMessage


def completion_unread(run):
    completed = getattr(run, "completed_at", None)
    read = getattr(run, "caller_read_completed_at", None)
    return bool(getattr(run, "status", "") == "completed" and completed and (not read or completed > read))


def attention_state(run):
    if run.status in ("starting", "running", "input_required") and (getattr(run, "_history_waiting", False) or run.status == "input_required"):
        return "input_required"
    if run.status in ("starting", "running"):
        return "running"
    return "completed_unread" if completion_unread(run) else run.status


def history_queryset(queryset, attention, counts):
    active = Q(status__in=["starting", "running", "input_required"])
    queryset = queryset.annotate(
        _history_waiting=Exists(AgentRunInteraction.objects.filter(
            run_id=OuterRef("pk"), status="pending", expires_at__gt=timezone.now())),
        _history_preview=Subquery(AgentRunMessage.objects.filter(run_id=OuterRef("pk"))
            .order_by("-sequence").annotate(snippet=Substr("content", 1, 160)).values("snippet")[:1]),
    )
    waiting = active & (Q(_history_waiting=True) | Q(status="input_required"))
    filters = {
        "all": Q(),
        "running": active & ~waiting,
        "input_required": waiting,
        "completed_unread": Q(status="completed", completed_at__isnull=False)
            & (Q(caller_read_completed_at__isnull=True) | Q(completed_at__gt=F("caller_read_completed_at"))),
        "failed": Q(status__in=["failed", "cancelled", "expired"]),
    }
    if attention not in filters:
        raise exceptions.ValidationError({"attention": "Unknown Run history filter."})
    if counts is not None:
        counts.update(queryset.aggregate(**{name: Count("pk", filter=condition, distinct=True) for name, condition in filters.items()}))
    return queryset.filter(filters[attention])


class CompletionChanged(exceptions.APIException):
    status_code = 409
    default_detail = "This Run changed. Refresh before marking its result as read."
    default_code = "RUN_COMPLETION_CHANGED"


class PrivateRunReadView(APIView):
    def post(self, request, run_id):
        from .services import get_private_display_run
        run = get_private_display_run(request=request, run_id=str(run_id))
        observed = serializers.DateTimeField().run_validation(request.data.get("completed_at"))
        # Compare-and-set prevents a late browser acknowledgement from consuming
        # a later turn's completion. Never mutate operational updated_at/status.
        updated = AgentDisplayRun.objects.filter(pk=run.pk, status="completed",
            completed_at=observed, caller_hidden_at__isnull=True,
        ).update(caller_read_completed_at=observed)
        if not updated:
            raise CompletionChanged()
        # The Run history receipt and Work Inbox receipt represent the same
        # caller acknowledgement. Keep both read surfaces consistent without
        # changing the source Run's lifecycle state.
        from apps.notifications.models import InboxItem, InboxReceipt
        item = InboxItem.objects.filter(
            tenant_id=run.consumer_tenant_id,
            source_type="run",
            source_id=str(run.pk),
            audience_type=InboxItem.AUDIENCE_PERSONAL,
            recipient=request.user,
        ).first()
        if item is not None:
            InboxReceipt.objects.update_or_create(
                item=item, user=request.user, defaults={"read_at": observed},
            )
        return Response({"run_id": str(run.pk), "completed_at": observed.isoformat(), "completion_unread": False})
