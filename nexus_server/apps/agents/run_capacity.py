"""Caller-owned capacity recovery; never delete history or stop other callers."""
from django.core import signing
from django.db import transaction
from rest_framework import exceptions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import write_audit_log
from apps.common.resource_limits import capability_state
from apps.common.subjects import hash_token, request_subject
from apps.tenancy.services import get_tenant_from_request
from .models import AgentDisplayRun, AgentTaskExecution
from .policy import invoke_agent_policy

SALT = "private-run-capacity-recovery-v1"
BATCH_SIZE = 100


def expire_stale_non_invocation_runs(*, now=None) -> int:
    return invoke_agent_policy("expire_stale_non_invocation_runs", now=now)


def recovery_scope(request, agent_id, exclude):
    from .runtime_services import get_runtime_use_agent
    tenant = get_tenant_from_request(request)
    agent = get_runtime_use_agent(request=request, tenant=tenant, agent_id=str(agent_id))
    subject = request_subject(request)
    project = str(getattr(request, "project_id", None) or "")
    identity = [str(tenant.pk), project, subject.subject_hash, str(agent.pk), exclude]
    # Include formerly hidden active Runs. Removing history never released their
    # capacity. All Agents sharing this publisher quota are included, but no
    # other caller, Project, publisher or public Demo can be affected.
    runs = AgentDisplayRun.objects.filter(
        tenant_id=agent.tenant_id, consumer_tenant=tenant,
        consumer_project_id=project or None, caller_subject_hash=subject.subject_hash,
        run_kind=AgentDisplayRun.KIND_INVOCATION, status=AgentDisplayRun.STATUS_RUNNING,
    )
    if exclude:
        runs = runs.exclude(pk=exclude)
    return agent, identity, runs


def capacity_summary(agent):
    value = capability_state(tenant=agent.tenant, code="agents.concurrent_runs")
    return {key: value[key] for key in ("used", "limit", "remaining", "state")}


class RecoveryRequest(serializers.Serializer):
    exclude_run_id = serializers.UUIDField(required=False, allow_null=True)
    confirmation = serializers.BooleanField(required=True)
    preview_token = serializers.CharField(max_length=50000)


class PrivateRunCapacityRecoveryView(APIView):
    def get(self, request, agent_id):
        raw = request.query_params.get("exclude_run_id")
        exclude = str(serializers.UUIDField().run_validation(raw)) if raw else ""
        agent, identity, queryset = recovery_scope(request, agent_id, exclude)
        count = queryset.count()
        runs = list(queryset.select_related("agent").only(
            "id", "agent_id", "agent__name", "write_token", "display_title", "title", "caller_hidden_at",
        ).order_by("created_at", "id")[:BATCH_SIZE])
        snapshot = [[str(run.pk), hash_token(run.write_token)] for run in runs]
        return Response({
            "capacity": capacity_summary(agent), "eligible_count": count,
            "batch_count": len(runs), "has_more": count > len(runs),
            "runs": [{"id": str(run.pk), "agent_name": run.agent.name,
                "title": run.display_title or run.title or "Untitled Run",
                "hidden": bool(run.caller_hidden_at)} for run in runs],
            "preview_token": signing.dumps({"identity": identity, "runs": snapshot}, salt=SALT, compress=True),
        })

    def post(self, request, agent_id):
        from .runtime_services import cancel_private_run
        data = RecoveryRequest(data=request.data)
        data.is_valid(raise_exception=True)
        if not data.validated_data["confirmation"]:
            raise exceptions.ValidationError("Confirm stopping the listed Runs first.")
        exclude = str(data.validated_data.get("exclude_run_id") or "")
        agent, identity, queryset = recovery_scope(request, agent_id, exclude)
        try:
            preview = signing.loads(data.validated_data["preview_token"], salt=SALT, max_age=300)
            if preview["identity"] != identity or len(preview["runs"]) > BATCH_SIZE:
                raise signing.BadSignature()
        except (signing.BadSignature, KeyError, TypeError, ValueError):
            raise exceptions.ValidationError("Recovery preview expired or changed. Review the Runs again.")
        result = {"stopped": 0, "pending": 0, "failed": 0, "skipped": 0}
        for run_id, fingerprint in preview["runs"]:
            try:
                with transaction.atomic():
                    # Same lock order as resume/worker/cancel. A preview of turn N
                    # must never cancel turn N+1, or a Run started after preview.
                    AgentTaskExecution.objects.select_for_update(of=("self",)).filter(task__run_id=run_id).first()
                    run = queryset.select_for_update().filter(pk=run_id).first()
                    if not run or hash_token(run.write_token) != fingerprint:
                        result["skipped"] += 1
                        continue
                    run = cancel_private_run(request=request, run_id=run_id, include_hidden=True)
                    result["pending" if run.status == "running" else "stopped"] += 1
            except Exception:
                # Independent transactions keep successful cancellations even if
                # another Run fails. Never echo upstream error/token material.
                result["failed"] += 1
        write_audit_log(actor=request.user, tenant=get_tenant_from_request(request),
            action="agent.runs.capacity_recovery", resource_type="agent", resource_id=agent.pk,
            request=request, metadata=result.copy())
        return Response({**result, "remaining": queryset.count(), "capacity": capacity_summary(agent)})
