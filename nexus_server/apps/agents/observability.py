"""Bounded, caller-content-free developer observability.

Cursor pages deliberately do not COUNT or OFFSET the entire history. The signed
cursor is tied to the identity, resource and filters, never an authorization.
"""
import hashlib
import json
from uuid import UUID

from django.core import signing
from django.db.models import Exists, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Substr
from rest_framework import exceptions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.subjects import request_subject
from apps.mobile.models import MobileCommand
from apps.workspaces.models import WorkspaceTerminalSession
from .models import AgentDisplayEvent, AgentRuntimeInvocation, AgentMobileBinding


def optimized_runs(queryset, *, summary=False):
    latest = AgentRuntimeInvocation.objects.filter(display_run_id=OuterRef("pk")).order_by("-turn_index", "-created_at")
    from .context_extension import annotate_run_invocations
    fields = ("turn_index", "tool_name", "error_code", "latency_ms")
    queryset = annotate_run_invocations(queryset=queryset, latest=latest, summary=summary)
    bindings = AgentMobileBinding.objects.select_related("device").only("id", "status", "device_id", "device__id", "device__status", "device__metadata", "device__capabilities", "device__last_seen_at", "device__online_status")
    return queryset.prefetch_related(Prefetch("mobile_binding", queryset=bindings)).annotate(
        _obs_terminal_status=Subquery(WorkspaceTerminalSession.objects.filter(display_run_id=OuterRef("pk")).values("status")[:1]),
        _obs_latest_seq=Subquery(AgentDisplayEvent.objects.filter(run_id=OuterRef("pk")).order_by("-seq").values("seq")[:1]),
        _obs_mobile_failed=Exists(MobileCommand.objects.filter(display_run_id=OuterRef("pk"), status="failed")),
        **{f"_obs_{field}": Subquery(latest.values(field)[:1]) for field in fields},
    )


class Query(serializers.Serializer):
    cursor = serializers.CharField(required=False, allow_blank=True, max_length=2048)
    limit = serializers.IntegerField(required=False, default=50, min_value=1, max_value=100)
    q = serializers.CharField(required=False, allow_blank=True, max_length=160)
    status = serializers.CharField(required=False, allow_blank=True, max_length=32)
    tool = serializers.CharField(required=False, allow_blank=True, max_length=128)
    runtime = serializers.UUIDField(required=False)
    run_id = serializers.UUIDField(required=False)
    since = serializers.DateTimeField(required=False)
    until = serializers.DateTimeField(required=False)


def query_params(request):
    data = {k: v for k, v in request.query_params.items() if v != ""}
    # A date-only bound means midnight UTC, not the server's local timezone.
    for key in ("since", "until"):
        if len(data.get(key, "")) == 10:
            data[key] += "T00:00:00Z"
    query = Query(data=data)
    query.is_valid(raise_exception=True)
    params = query.validated_data
    if params.get("since") and params.get("until") and params["since"] >= params["until"]:
        raise exceptions.ValidationError({"until": "End must be after start."})
    return params


def page(queryset, params, context, *, sequence=False):
    fingerprint = hashlib.sha256(json.dumps([context, {k: str(v) for k, v in params.items() if k not in {"cursor", "limit"}}], sort_keys=True).encode()).hexdigest()
    if params.get("cursor"):
        try:
            saved = signing.loads(params["cursor"], salt="agent-observability-v1", max_age=86400)
            if saved["scope"] != fingerprint:
                raise ValueError()
            if sequence:
                queryset = queryset.filter(seq__lt=int(saved["position"]))
            else:
                date = serializers.DateTimeField().run_validation(saved["position"])
                pk = queryset.model._meta.pk.to_python(saved["id"])
                queryset = queryset.filter(Q(created_at__lt=date) | Q(created_at=date, pk__lt=pk))
        except (signing.BadSignature, ValueError, KeyError, TypeError, exceptions.ValidationError):
            raise exceptions.ValidationError({"cursor": "Invalid or expired cursor. Reload the first page."}) from None
    rows = list(queryset.order_by("-seq")[:params["limit"] + 1] if sequence else queryset.order_by("-created_at", "-pk")[:params["limit"] + 1])
    more = len(rows) > params["limit"]
    rows = rows[:params["limit"]]
    cursor = None
    if more:
        last = rows[-1]
        cursor = signing.dumps({"scope": fingerprint, "position": last.seq if sequence else last.created_at.isoformat(), "id": str(last.pk)}, salt="agent-observability-v1")
    return rows, cursor


class AgentObservabilityView(APIView):
    def get(self, request, agent_id, kind="runs", run_id=None):
        from .services import get_agent, require_agent_observability, list_output_artifacts, observability_events_queryset
        from .serializers import AgentDisplayRunSerializer
        agent = get_agent(request=request, agent_id=agent_id)
        require_agent_observability(request=request, agent=agent)
        params = query_params(request)
        subject = request_subject(request)
        context = [str(agent.tenant_id), str(agent.pk), str(run_id or ""), kind, subject.subject_hash]
        if run_id and not agent.display_runs.filter(pk=run_id).exists():
            raise exceptions.NotFound()
        if kind == "runs":
            queryset = optimized_runs(agent.display_runs.all(), summary=True).only(
                "id", "agent_id", "runtime_id", "run_kind", "status", "redaction_status", "title", "started_at", "completed_at", "created_at", "updated_at",
                "caller_subject_hash", "caller_principal_type", "computer_binding_id", "mobile_binding_id")
            if params.get("status"):
                queryset = queryset.filter(status=params["status"])
            if params.get("runtime"):
                queryset = queryset.filter(runtime_id=params["runtime"])
            if params.get("run_id"):
                queryset = queryset.filter(pk=params["run_id"])
            if params.get("tool"):
                # Restrict candidates with the indexed tool catalog first.
                # Evaluating the latest-turn subquery against every Run turns
                # a rare tool search into a full Agent history scan. Keep the
                # final check: an older turn using this tool is not a match.
                candidates = AgentRuntimeInvocation.objects.filter(
                    agent=agent, tool_name=params["tool"], display_run__isnull=False,
                ).order_by().values("display_run_id")
                queryset = queryset.filter(pk__in=Subquery(candidates), _obs_tool_name=params["tool"])
            if params.get("q"):
                try:
                    queryset = queryset.filter(pk=UUID(params["q"]))
                except ValueError:
                    queryset = queryset.filter(title__icontains=params["q"])
        elif kind == "memory":
            # Lineage only: caller memory bodies/JSON are not developer logs.
            queryset = agent.memory_items.exclude(status="deleted").only("id", "created_at", "memory_type", "scope", "consent_status", "sensitivity_level", "status", "source_run_id")
            if params.get("q"):
                queryset = queryset.filter(memory_type__icontains=params["q"])
        elif kind == "logs":
            queryset = agent.logs.defer("message").annotate(_summary=Substr("message", 1, 1024))
            if params.get("q"):
                queryset = queryset.filter(message__icontains=params["q"])
        elif kind == "outputs" and run_id:
            queryset = list_output_artifacts(request=request, agent_id=agent_id, run_id=str(run_id)).only(
                "id", "created_at", "original_file_name", "content_type", "size_bytes", "snapshot_status", "scan_status", "status", "turn_index")
        elif kind == "events" and run_id:
            queryset = observability_events_queryset(AgentDisplayEvent.objects.filter(run_id=run_id)).only("id", "created_at", "seq", "event_type")
        else:
            raise exceptions.NotFound()
        for key, lookup in (("since", "created_at__gte"), ("until", "created_at__lt")):
            if params.get(key):
                queryset = queryset.filter(**{lookup: params[key]})
        rows, cursor = page(queryset, params, context, sequence=kind == "events")
        if kind == "runs":
            # Keep the legacy shape, but never transfer redaction metadata or
            # billing line items on the new summary endpoint.
            items = []
            for run in rows:
                run._obs_summary = True
                run.redaction_metadata = {}
                item = dict(AgentDisplayRunSerializer(run).data)
                item.pop("redaction_metadata", None)
                item.update(tool_name=run._obs_tool_name or "", error_code=run._obs_error_code or "", latency_ms=run._obs_latency_ms)
                items.append(item)
        elif kind == "events":
            items = [{"id": str(row.pk), "seq": row.seq, "type": row.event_type, "created_at": row.created_at} for row in rows]
        elif kind == "memory":
            items = [{"id": str(row.pk), "created_at": row.created_at, "memory_type": row.memory_type, "scope": row.scope, "consent_status": row.consent_status,
                "sensitivity_level": row.sensitivity_level, "status": row.status, "source_run_id": str(row.source_run_id) if row.source_run_id else None} for row in rows]
        elif kind == "logs":
            items = [{"id": str(row.pk), "level": row.level, "message": row._summary, "created_at": row.created_at} for row in rows]
        else:
            items = [{"id": str(row.pk), "created_at": row.created_at, "original_file_name": row.original_file_name, "content_type": row.content_type,
                "size_bytes": row.size_bytes, "snapshot_status": row.snapshot_status, "scan_status": row.scan_status, "status": row.status, "turn_index": row.turn_index} for row in rows]
        return Response({"items": items, "next_cursor": cursor})
