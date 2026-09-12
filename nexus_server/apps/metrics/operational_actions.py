"""Operational request links and actions; distribution owns target authority."""
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.audit.services import write_audit_log
from . import policy
from .operational_catalog import identifier


class MonitoringRequestView(APIView):
    def get(self, request, request_id):
        from apps.gateway.models import GatewayRequestLog
        from apps.agents.models import AgentRuntimeInvocation
        tenant, project = policy.context(request)
        if not request_id or len(request_id) > 64:
            raise NotFound()
        gateways = GatewayRequestLog.objects.filter(tenant=tenant, request_id=request_id)
        agents = AgentRuntimeInvocation.objects.filter(tenant=tenant, request_id=request_id)
        if project:
            gateways, agents = gateways.filter(project_id=project), agents.filter(project_id=project)
        # Links, not new authority: the destination still enforces its own trace permissions.
        rows = [{"kind": "gateway", "id": str(row.pk), "request_id": row.request_id, "status": row.status,
            "router_id": str(row.router_id or ""), "pool_id": str(row.selected_model_group_id or ""),
            "source_id": str(row.consumer_source_id or ""), "latency_ms": row.latency_ms} for row in gateways[:100]]
        rows += [{"kind": "agent", "id": str(row.pk), "request_id": row.request_id, "status": row.status,
            "agent_id": str(row.agent_id), "run_id": str(row.display_run_id or ""), "latency_ms": row.latency_ms} for row in agents[:100]]
        if not rows:
            raise NotFound("No retained request records in this scope.")
        return Response({"items": rows, "limit_per_surface": 100, "detail_policy": "Links only; no prompts, tokens or terminal content."}, headers={"Cache-Control": "private, no-store"})


class MonitoringActionView(APIView):
    @transaction.atomic
    def post(self, request, kind, record_id):
        from datetime import timedelta
        from .http_host import action_target
        tenant, project = policy.context(request, "metrics.manage")
        action = request.data.get("action")
        qs, serializer = action_target(request=request, kind=kind)
        row = qs.select_for_update(of=("self",)).filter(pk=identifier(record_id)).first()
        if row is None:
            raise NotFound()
        now = timezone.now()
        if kind == "rules" and action in {"pause", "resume", "mute", "unmute"}:
            if action in {"pause", "resume"}:
                row.status = "disabled" if action == "pause" else "active"
            else:
                row.muted_until = now + timedelta(hours=1) if action == "mute" else None
            row.save(update_fields=["status", "muted_until", "updated_at"])
        elif kind == "incidents" and action == "acknowledge" and not row.is_test:
            if not row.acknowledged_at:
                from apps.audit.services import audit_actor
                row.acknowledged_at, row.acknowledged_by = now, audit_actor(request.user)
                row.save(update_fields=["acknowledged_at", "acknowledged_by", "updated_at"])
        elif kind in {"deliveries", "notifications"} and action == "retry":
            if row.delivery_status != "failed" or not row.delivery_key:
                raise ValidationError("Only a failed durable delivery can be retried.")
            row.delivery_status, row.next_attempt_at, row.error_message = "pending", now, ""
            row.lease_id, row.lease_until = "", None
            row.attempts = 0
            row.save(update_fields=["delivery_status", "next_attempt_at", "error_message", "lease_id", "lease_until", "attempts", "updated_at"])
        else:
            raise ValidationError({"action": "Unsupported action for this record."})
        write_audit_log(actor=request.user, tenant=tenant, action=f"monitoring.{kind}.{action}", resource_type=kind, resource_id=row.pk, request=request)
        return Response(serializer(row, context={"request": request}).data, headers={"Cache-Control": "private, no-store"})
