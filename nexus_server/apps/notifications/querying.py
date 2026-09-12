"""Read-only, database-side Inbox state and caller receipts.

Completed history is immutable; active source state is evaluated by indexed
EXISTS expressions. Neither listing nor counting reconciles/writes source data.
"""
from django.db.models import Case, CharField, Exists, F, OuterRef, Q, Subquery, UUIDField, Value, When
from django.db.models.functions import Cast
from django.db.models.functions import Lower, Replace
from django.utils import timezone

from .inbox import FINAL_STATES, visible_items
from .models import InboxReceipt
from .policy import backend


class SourceUUIDCast(Cast):
    """Keep native UUID casts; match Django's hexadecimal SQLite storage."""

    def as_sqlite(self, compiler, connection, **extra_context):
        expression = Lower(Replace(self.source_expressions[0], Value("-"), Value("")),
                           output_field=UUIDField())
        return compiler.compile(expression)


def with_receipts(queryset, user):
    receipt = InboxReceipt.objects.filter(item_id=OuterRef("pk"), user=user)
    return queryset.annotate(**{
        f"_{field}": Subquery(receipt.values(field)[:1])
        for field in ("read_at", "snoozed_until", "archived_at", "updated_at")
    })


def inbox_query(request):
    from apps.agents.models import AgentDisplayRun, AgentRunInteraction, AgentTaskExecution, EdgeNode, AgentRuntimeDeployment
    from apps.datasets.models import DatasetImportJob
    from apps.jobs.models import Job
    from apps.workspaces.models import WorkspaceConnection
    from apps.mobile.models import MobileDevice

    queryset = with_receipts(visible_items(request), request.user).annotate(
        _source_uuid=Case(When(source_id__regex=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
                              then=SourceUUIDCast("source_id", UUIDField())), default=None, output_field=UUIDField()),
    )

    def exists(model, **filters):
        return Exists(model.objects.filter(pk=OuterRef("_source_uuid"), **filters))

    def mapped(model, field, mapping, default="resolved"):
        return Case(*[When(exists(model, **{field: value}), then=Value(state))
                      for value, state in mapping.items()], default=Value(default), output_field=CharField())

    job_states = {"queued": "in_progress", "running": "in_progress", "succeeded": "completed",
                  "failed": "failed", "canceled": "canceled"}
    pending = Exists(AgentRunInteraction.objects.filter(
        run_id=OuterRef("_source_uuid"), status="pending", expires_at__gt=timezone.now(),
        run__status__in=["starting", "running", "input_required"],
    ))
    run_state = Case(
        When(kind="input_required", then=Case(When(pending, then=Value("needs_action")), default=Value("resolved"))),
        When(Q(kind="run_failed") & ~exists(AgentDisplayRun, status="failed"), then=Value("resolved")),
        default=mapped(AgentDisplayRun, "status", {"running": "in_progress", "input_required": "needs_action",
                                                   "starting": "in_progress", "completed": "completed", "failed": "failed"}),
        output_field=CharField(),
    )
    cases = [When(source_type="run", then=run_state),
             When(source_type="job", then=mapped(Job, "status", job_states)),
             When(source_type="dataset_import", then=mapped(DatasetImportJob, "state", {
                 **job_states, "processing": "in_progress", "completed": "completed"})),
             When(source_type="agent_recovery", then=Case(When(Exists(AgentTaskExecution.objects.filter(
                 task_id=OuterRef("_source_uuid"), state="recovery_required")), then=Value("needs_action")), default=Value("resolved")))]
    for source, model, field, states in backend().query_sources():
        cases.append(When(source_type=source, then=mapped(model, field, states)))
    for source, model, condition in [
        ("computer_issue", WorkspaceConnection, Q(last_test_status="failed") & ~Q(status="deleted")),
        ("mobile_issue", MobileDevice, Q(last_seen_at__isnull=False, online_status="offline") & ~Q(status="deleted")),
    ]:
        active = Exists(model.objects.filter(condition, pk=OuterRef("_source_uuid")))
        cases.append(When(source_type=source, then=Case(When(active, then=Value("needs_action")), default=Value("resolved"))))
    now = timezone.now()
    live_node = Q(presence_protocol_version__gte=1, last_presence_at__isnull=False) & ~Q(status="deleted") & ~Q(connection_status="revoked")
    expired = (Q(presence_expires_at__isnull=True) | Q(presence_expires_at__lte=now)) & ~Q(connection_status="offline", connection_status_reason="stopped")
    node_issue = live_node & (expired | Q(connection_status="offline", connection_status_reason="expired") |
                             Q(connection_status="degraded", presence_expires_at__gt=now))
    node_bad = Exists(EdgeNode.objects.filter(node_issue, pk=OuterRef("_source_uuid")))
    cases.append(When(source_type="edge_router_issue", then=Case(When(node_bad, then=Value("needs_action")), default=Value("resolved"))))
    edge_available = Q(edge_registration__status="active", edge_registration__lease_expires_at__gt=now) & (
        Q(edge_registration__node__presence_protocol_version__lt=1) |
        (Q(edge_registration__node__presence_protocol_version__gte=1,
           edge_registration__node__last_presence_at__isnull=False,
           edge_registration__node__presence_expires_at__gt=now,
           edge_registration__node__connection_status__in=["online", "degraded"]) & ~Q(edge_registration__node__status="deleted")))
    runtime_issue = Q(status="failed") | Q(health_status__in=["degraded", "unhealthy"]) | (
        Q(status__in=["active", "deploying"], edge_registration__isnull=False) & ~edge_available)
    runtime_bad = Exists(AgentRuntimeDeployment.objects.filter(runtime_issue, pk=OuterRef("_source_uuid")))
    cases.append(When(source_type="agent_runtime_issue", then=Case(When(runtime_bad, then=Value("needs_action")), default=Value("resolved"))))
    return queryset.annotate(_live_state=Case(
        When(state__in=FINAL_STATES, then=F("state")), *cases, default=F("state"), output_field=CharField(),
    ))
