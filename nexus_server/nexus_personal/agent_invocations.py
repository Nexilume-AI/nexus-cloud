"""Real personal execution lifecycle. No prices, wallets or commercial reports."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions
from apps.agents.models import AgentDisplayRun, AgentRuntimeDeployment, AgentRuntimeInvocation, AgentTaskExecution
from apps.agents.services import _run_message_turn
from apps.agents.task_execution import assert_execution_lease, task_deadline
from apps.common.subjects import request_subject, subject_digest
from apps.common.resource_limits import enforce_capability
from apps.tenancy.models import Tenant
from .agent_runtime_policy import PersonalAgentRuntimePolicy, PersonalRunContextExtension
from .models import PersonalInstallation, PersonalInvocationUsage
from .resource_limits import PersonalCapacityExceeded
from .services import require_personal_distribution


def _installation():
    require_personal_distribution()
    return PersonalInstallation.objects.get(slot=1)


def _run(row, run, turn_index):
    if (run.run_kind != "invocation" or run.tenant_id != row.tenant_id
            or run.consumer_tenant_id != row.tenant_id or run.consumer_project_id != row.project_id
            or run.caller_principal_type != "user" or run.caller_principal_id != str(row.owner_id)
            or run.caller_subject_hash != subject_digest(f"{row.tenant_id}|user|{row.owner_id}")
            or type(turn_index) is not int or turn_index < 1 or _run_message_turn(run) != turn_index):
        raise exceptions.NotFound("Invocation context not found.")


def _expire(run, now):
    AgentDisplayRun.objects.filter(pk=run.pk).update(write_token="", context_token_hash="",
        context_token_expires_at=now, interaction_token_expires_at=now,
        workspace_delegate_token_expires_at=now, browser_delegate_token_expires_at=now,
        mobile_delegate_token_expires_at=now)


class PersonalInvocationLifecycle:
    def _follow_up_invocation(self, *, run, task, state):
        from apps.agents.follow_ups import conflict, turn
        row = _installation()
        PersonalRunContextExtension().issue(agent=run.agent, tool_name=task.tool_name, now=timezone.now())
        _run(row, run, turn(task))
        invocation = run.runtime_invocations.filter(turn_index=turn(task)).first()
        if (not invocation or invocation.actor_id != row.owner_id or invocation.status != state
                or invocation.tool_name != task.tool_name or invocation.agent_id != run.agent_id
                or invocation.deployment_id != task.runtime_id):
            conflict("FOLLOW_UP_CONTEXT_CHANGED")
        return invocation

    def capture_follow_up(self, *, run, task):
        invocation = self._follow_up_invocation(run=run, task=task, state="pending")
        return {"invocation_authority": {"id": str(invocation.pk), "run_id": str(run.pk),
            "turn_index": invocation.turn_index, "tool_name": invocation.tool_name}}

    def recheck_follow_up(self, *, run, task, authority):
        from apps.agents.follow_ups import conflict
        invocation = self._follow_up_invocation(run=run, task=task, state="success")
        expected = {"id": str(invocation.pk), "run_id": str(run.pk),
            "turn_index": invocation.turn_index, "tool_name": invocation.tool_name}
        if authority.get("invocation_authority") != expected:
            conflict("FOLLOW_UP_CONTEXT_CHANGED")

    def verify_follow_up_reservation(self, *, run, task, authority):
        from apps.agents.follow_ups import conflict
        from apps.agents.models import AgentExecutionTask
        previous = authority.get("invocation_authority")
        if not isinstance(previous, dict) or type(previous.get("turn_index")) is not int:
            conflict("FOLLOW_UP_CONTEXT_CHANGED")
        current = AgentDisplayRun.objects.get(pk=run.pk)
        current_task = AgentExecutionTask.objects.get(run=current)
        invocation = self._follow_up_invocation(run=current, task=current_task, state="pending")
        if (previous.get("run_id") != str(run.pk) or previous.get("tool_name") != invocation.tool_name
                or invocation.turn_index != previous["turn_index"] + 1):
            conflict("FOLLOW_UP_CONTEXT_CHANGED")

    def pricing_snapshot(self, **kwargs):
        raise exceptions.NotFound("Pricing is not part of the personal distribution.")

    estimate = pricing_snapshot
    report = pricing_snapshot

    def preflight(self, *, tenant, agent):
        row = _installation()
        if str(tenant.pk) != str(row.tenant_id):
            raise exceptions.NotFound("Agent not found.")
        PersonalRunContextExtension().issue(agent=agent, tool_name="", now=timezone.now())

    def begin(self, *, request, tenant, agent, runtime, api_key, tool_name, display_run, turn_index,
              execution_profile_id="", execution_model="", reasoning_effort=""):
        assert_execution_lease()
        actor = PersonalAgentRuntimePolicy().runtime_actor_user(request=request, api_key=api_key)
        from .agent_credentials import enforce_agent
        agent_credential = enforce_agent(request, agent.pk)
        self.preflight(tenant=tenant, agent=agent)
        denied = None
        with transaction.atomic():
            row = _installation()
            Tenant.objects.select_for_update().get(pk=row.tenant_id)
            run = AgentDisplayRun.objects.select_for_update().get(pk=display_run.pk)
            _run(row, run, turn_index)
            if (run.agent_id != agent.pk or request_subject(request).subject_hash != run.caller_subject_hash
                    or runtime is None or run.runtime_id != runtime.pk
                    or not AgentRuntimeDeployment.objects.filter(pk=runtime.pk, agent_id=agent.pk,
                        tenant_id=row.tenant_id, project_id=row.project_id, status="active").exists()):
                raise exceptions.NotFound("Invocation runtime not found.")
            existing = AgentRuntimeInvocation.objects.filter(display_run=run, turn_index=turn_index).first()
            if existing is not None:
                if existing.tool_name != tool_name or existing.actor_id != actor.pk:
                    raise exceptions.ValidationError("Conflicting invocation replay.")
                receipt = PersonalInvocationUsage.objects.filter(invocation=existing).first()
                if receipt is None or receipt.agent_credential_id != getattr(agent_credential, 'pk', None):
                    raise exceptions.PermissionDenied('Invocation credential authority changed.')
                return existing
            if run.status != "running":
                raise exceptions.ValidationError("This invocation is already closed.")
            deadline = task_deadline(tenant)
            now = timezone.now()
            reserved_ms = max(1, int((deadline - now).total_seconds() * 1000))
            try:
                enforce_capability(tenant=tenant, code="agents.runs_per_30_days")
                enforce_capability(tenant=tenant, code="agents.run_minutes_per_30_days",
                    requested=Decimal(reserved_ms) / Decimal(60000))
            except PersonalCapacityExceeded as exc:
                # Context creation may already have committed. Close that Run
                # on failed admission instead of leaking a concurrent slot.
                from apps.agents.runtime_services import finish_invocation_display_run
                finish_invocation_display_run(run=run, succeeded=False, error_code="PERSONAL_CAPACITY_EXCEEDED")
                _expire(run, now)
                denied = exc
            if denied is None:
                invocation = AgentRuntimeInvocation.objects.create(tenant=tenant, project_id=row.project_id,
                    agent=agent, deployment=runtime, display_run=run, actor=actor, tool_name=tool_name,
                    status="pending", turn_index=turn_index, request_id=str(getattr(request, "request_id", ""))[:64],
                    execution_profile_id=str(execution_profile_id)[:64], execution_model=str(execution_model)[:128],
                    reasoning_effort=str(reasoning_effort)[:16])
                PersonalInvocationUsage.objects.create(invocation=invocation, tenant=tenant, run_id=run.pk,
                    turn_index=turn_index, started_at=now,
                    agent_credential_id=getattr(agent_credential, 'pk', None),
                    expires_at=now + timedelta(milliseconds=reserved_ms, minutes=5), reserved_ms=reserved_ms)
        if denied is not None:
            raise denied
        return invocation

    def finalize(self, *, request, invocation, succeeded, error_code, latency_ms):
        try:
            return self._finalize(request=request,invocation=invocation,succeeded=succeeded,
                error_code=error_code,latency_ms=latency_ms)
        except (exceptions.AuthenticationFailed,exceptions.PermissionDenied):
            # A dispatched MCP call can finish after its Computer key is
            # revoked. Keep denial, but close its original receipt instead of
            # leaving reserved capacity behind. Never replay the external call.
            credential_id=getattr(request,'_nexus_personal_agent_credential_id',None)
            receipt=PersonalInvocationUsage.objects.filter(invocation_id=invocation.pk,
                agent_credential_id=credential_id,finished_at__isnull=True,
                invocation__actor_id=getattr(getattr(request,'user',None),'pk',None)).first() if credential_id else None
            if succeeded is True and receipt is not None:
                with transaction.atomic():
                    closed=self._finalize(request=None,invocation=invocation,succeeded=False,
                        error_code='AGENT_CREDENTIAL_REVOKED',latency_ms=latency_ms)
                    from apps.agents.runtime_services import finish_invocation_display_run
                    run=AgentDisplayRun.objects.get(pk=closed.display_run_id)
                    if closed.status=='failed' and closed.error_code=='AGENT_CREDENTIAL_REVOKED':
                        if run.status=='completed':
                            # The shared synchronous pipeline closes the Run
                            # before finalizing admission. Correct that result
                            # under the existing Run lock, without reopening it
                            # to SDK writes or rewriting its historical events.
                            from apps.agents.models import AgentDisplayEvent
                            from apps.agents.services import normalize_agui_event
                            payload=normalize_agui_event(event_type=AgentDisplayEvent.TYPE_RUN_FAILED,
                                payload={'code':'AGENT_CREDENTIAL_REVOKED','message':'Agent access was revoked before completion could be accepted.'})
                            sequence=(run.events.order_by('-seq').values_list('seq',flat=True).first() or 0)+1
                            AgentDisplayEvent.objects.create(tenant_id=run.tenant_id,agent_id=run.agent_id,
                                run=run,seq=sequence,event_type=AgentDisplayEvent.TYPE_RUN_FAILED,payload_json=payload)
                            AgentDisplayRun.objects.filter(pk=run.pk).update(status='failed',completed_at=timezone.now())
                        else:
                            finish_invocation_display_run(run=run,succeeded=False,error_code='AGENT_CREDENTIAL_REVOKED')
                    _expire(run,timezone.now())
            raise

    @transaction.atomic
    def _finalize(self, *, request, invocation, succeeded, error_code, latency_ms):
        assert_execution_lease()
        if type(succeeded) is not bool or type(latency_ms) is not int or not 0 <= latency_ms <= 2**31 - 1:
            raise exceptions.ValidationError("Invalid invocation result.")
        row = _installation()
        Tenant.objects.select_for_update().get(pk=row.tenant_id)
        current = AgentRuntimeInvocation.objects.filter(pk=invocation.pk, tenant_id=row.tenant_id).first()
        if current is None or current.display_run_id is None:
            raise exceptions.NotFound("Invocation not found.")
        run = AgentDisplayRun.objects.select_for_update().get(pk=current.display_run_id)
        _run(row, run, current.turn_index)
        current = AgentRuntimeInvocation.objects.select_for_update().get(pk=current.pk)
        if request is not None and str(getattr(request.user, "pk", "")) != str(row.owner_id):
            raise exceptions.NotFound("Invocation not found.")
        if succeeded:
            if request is None:
                raise exceptions.PermissionDenied("Successful completion requires the execution owner.")
            PersonalAgentRuntimePolicy().runtime_actor_user(request=request,
                api_key=getattr(request, "api_key", None))
            from .agent_credentials import enforce_agent
            credential = enforce_agent(request, current.agent_id)
            receipt = PersonalInvocationUsage.objects.filter(invocation=current).first()
            if receipt is None or receipt.agent_credential_id != getattr(credential, 'pk', None):
                raise exceptions.PermissionDenied('Invocation credential authority changed.')
        if current.status != "pending":
            return current
        receipt = PersonalInvocationUsage.objects.select_for_update().get(invocation=current)
        now = timezone.now()
        if succeeded and (run.status == "failed" or receipt.expires_at <= now):
            succeeded, error_code = False, "INVOCATION_EXPIRED"
        current.status = "success" if succeeded else "failed"
        current.error_code = "" if succeeded else str(error_code or "INVOCATION_FAILED")[:64]
        current.latency_ms = latency_ms
        current.save(update_fields=["status", "error_code", "latency_ms", "updated_at"])
        receipt.latency_ms = latency_ms
        receipt.finished_at = now
        receipt.save(update_fields=["latency_ms", "finished_at"])
        from apps.audit.services import write_audit_log
        write_audit_log(request=request, actor=current.actor, tenant=current.tenant,
            action="agents.runtime.invoke", resource_type="agent", resource_id=current.agent_id,
            after={"run_id": str(run.pk), "turn_index": current.turn_index,
                "status": current.status, "error_code": current.error_code, "latency_ms": latency_ms})
        return current

    def record(self, *, request, tenant, agent, runtime, api_key, tool_name, status_value,
               error_code, latency_ms, cost, display_run=None, turn_index=1):
        # Compatibility for the shared legacy failure recorder, not pricing.
        if isinstance(cost, (float, bool)) or cost != 0 or status_value not in {"success", "failed"}:
            raise exceptions.ValidationError("Personal invocation records do not accept costs.")
        if display_run is None:
            raise exceptions.ValidationError("A caller-owned Run is required.")
        existing = AgentRuntimeInvocation.objects.filter(display_run=display_run, turn_index=turn_index).first()
        if existing is None:
            raise exceptions.ValidationError("Invocation must be admitted before execution.")
        if (api_key is not None or existing.tenant_id != tenant.pk or existing.agent_id != agent.pk
                or existing.deployment_id != getattr(runtime, "pk", None) or existing.tool_name != tool_name):
            raise exceptions.NotFound("Invocation not found.")
        return self.finalize(request=request, invocation=existing, succeeded=status_value == "success",
            error_code=error_code, latency_ms=latency_ms)

    @transaction.atomic
    def renew_lease(self, *, run, expires_at):
        assert_execution_lease()
        row = _installation()
        Tenant.objects.select_for_update().get(pk=row.tenant_id)
        run = AgentDisplayRun.objects.select_for_update().get(pk=run.pk)
        _run(row, run, _run_message_turn(run))
        now = timezone.now()
        for receipt in PersonalInvocationUsage.objects.select_for_update().filter(run_id=run.pk,
                turn_index=_run_message_turn(run), finished_at__isnull=True):
            ceiling = receipt.started_at + timedelta(milliseconds=receipt.reserved_ms, minutes=5)
            if receipt.expires_at <= now or expires_at <= now:
                raise exceptions.ValidationError("Invocation lease expired.")
            receipt.expires_at = max(receipt.expires_at, min(expires_at, ceiling))
            receipt.save(update_fields=["expires_at"])

    def release_stale(self, *, now=None, timeout_seconds=None):
        # Receipt expiry is authoritative; the compatibility timeout only
        # controls orphan contexts that never reached invocation admission.
        timeout = 300 if timeout_seconds is None else timeout_seconds
        if type(timeout) is not int or not 60 <= timeout <= 86400:
            raise exceptions.ValidationError("Invalid orphan invocation timeout.")
        current = now or timezone.now()
        row = _installation()
        receipts = PersonalInvocationUsage.objects.filter(tenant_id=row.tenant_id,
            finished_at__isnull=True, expires_at__lte=current).exclude(
            invocation__display_run__execution_task__execution__isnull=False)
        count = 0
        for receipt_id in list(receipts.order_by("expires_at").values_list("pk", flat=True)[:100]):
            with transaction.atomic():
                Tenant.objects.select_for_update().get(pk=row.tenant_id)
                receipt = PersonalInvocationUsage.objects.get(pk=receipt_id)
                if receipt.finished_at is not None or receipt.expires_at > current:
                    continue
                run = AgentDisplayRun.objects.select_for_update().filter(pk=receipt.run_id).first()
                if run is not None and AgentTaskExecution.objects.filter(task__run=run).exists():
                    continue
                if receipt.invocation_id and run is not None and _run_message_turn(run) == receipt.turn_index:
                    from apps.agents.runtime_services import finish_invocation_display_run
                    self.finalize(request=None, invocation=receipt.invocation, succeeded=False,
                        error_code="INVOCATION_EXPIRED", latency_ms=receipt.reserved_ms)
                    finish_invocation_display_run(run=run, succeeded=False, error_code="INVOCATION_EXPIRED")
                    _expire(run, current)
                else:
                    if receipt.invocation_id:
                        AgentRuntimeInvocation.objects.filter(pk=receipt.invocation_id, status="pending").update(
                            status="failed", error_code="INVOCATION_EXPIRED", latency_ms=receipt.reserved_ms,
                            updated_at=current)
                    receipt.finished_at = current
                    receipt.latency_ms = receipt.reserved_ms
                    receipt.save(update_fields=["finished_at", "latency_ms"])
                count += 1
        orphan_ids = list(AgentDisplayRun.objects.filter(tenant_id=row.tenant_id,
            run_kind="invocation", status="running", created_at__lt=current - timedelta(seconds=timeout),
            runtime_invocations__isnull=True, execution_task__isnull=True).order_by("created_at").values_list("pk", flat=True)[:100])
        for run_id in orphan_ids:
            with transaction.atomic():
                Tenant.objects.select_for_update().get(pk=row.tenant_id)
                run = AgentDisplayRun.objects.select_for_update().get(pk=run_id)
                if (run.status != "running" or run.runtime_invocations.exists()
                        or hasattr(run, "execution_task")):
                    continue
                _run(row, run, 1)
                from apps.agents.runtime_services import finish_invocation_display_run
                finish_invocation_display_run(run=run, succeeded=False, error_code="INVOCATION_NOT_DISPATCHED")
                _expire(run, current)
                count += 1
        return count
