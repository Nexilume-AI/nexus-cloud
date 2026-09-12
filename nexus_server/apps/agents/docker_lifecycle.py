"""Durable single-daemon desired state; Docker I/O never holds a database lock.

Only containers are recovered. An unknown-outcome Agent invocation is never replayed.
Every destructive action targets an exact recorded ID or a fenced generation name.
"""
import copy
import logging
import threading
import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .docker_policy import host_id, resource_limits, container_name
from .models import AgentDeployment, AgentDisplayEvent, AgentExecutionTask, AgentLog, AgentRuntimeDeployment, AgentRuntimeInvocation

logger = logging.getLogger(__name__)
RECONCILE_LOCK_KEY = "agents:docker-runtime-reconcile:v1"


def _lease_seconds() -> int:
    return max(30, int(getattr(settings, "NEXUS_AGENT_RUNTIME_LEASE_SECONDS", 60)))


class _LeaseRenewer:
    """Short renewable lease: fast crash recovery without duplicate Docker I/O."""

    def __init__(self, runtime):
        self.runtime_id = runtime.id
        self.token = str((runtime.docker_lifecycle or {}).get("lease_token") or "")
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def __enter__(self):
        if self.token and str(settings.NEXUS_AGENT_RUNTIME_RUNNER).lower() in {"docker", "controller"}:
            self.thread = threading.Thread(target=self._run, name="agent-runtime-lease", daemon=True)
            self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _run(self):
        interval = max(10, _lease_seconds() // 3)
        while not self.stop_event.wait(interval):
            close_old_connections()
            try:
                with transaction.atomic():
                    runtime = AgentRuntimeDeployment.objects.select_for_update().get(pk=self.runtime_id)
                    state = copy.deepcopy(runtime.docker_lifecycle or {})
                    if state.get("lease_token") != self.token:
                        return
                    state["lease_until"] = (
                        timezone.now() + timedelta(seconds=_lease_seconds())
                    ).isoformat()
                    runtime.docker_lifecycle = state
                    runtime.save(update_fields=["docker_lifecycle", "updated_at"])
            except Exception:
                # The fencing token still prevents a stale operation from
                # committing if another worker takes over after expiry.
                logger.warning("Agent Docker lease renewal failed", exc_info=False)
                return
            finally:
                close_old_connections()


def _query():
    return AgentRuntimeDeployment.objects.select_related("tenant", "agent", "image", "image__version", "project", "agent_deployment")


def _future(value):
    return bool(value and parse_datetime(value) and parse_datetime(value) > timezone.now())


def prepare_state(*, agent, env, image):
    from .runtime_services import AgentRuntimeOperationConflict
    from apps.common.resource_limits import enforce_capability
    current = AgentRuntimeDeployment.objects.select_for_update().filter(agent=agent, env=env).first()
    state = copy.deepcopy(current.docker_lifecycle or {}) if current else {}
    if _future(state.get("lease_until")):
        raise AgentRuntimeOperationConflict()
    if state.get("host_id") and state["host_id"] != host_id():
        raise AgentRuntimeOperationConflict("Deployment belongs to a different Docker worker.")
    limits = resource_limits(SimpleNamespace(agent=agent))
    enforce_capability(tenant=agent.tenant, code="agents.hosted_cpu", requested=Decimal(limits["cpu"]))
    enforce_capability(tenant=agent.tenant, code="agents.hosted_memory_mb", requested=Decimal(limits["memory_bytes"]) / (1024**2))
    previous = None
    if current and current.container_id and current.status == "active":
        previous = {"container_id": current.container_id, "image_id": str(current.image_id),
                    "internal_mcp_url": current.internal_mcp_url, "state": {k: v for k, v in state.items() if k != "previous"}}
    return {"host_id": host_id(), "desired": "running", "generation": uuid.uuid4().hex,
            "resources": limits, "previous": previous, "retiring": state.get("retiring", []), "attempts": 0}


def _claim(runtime_id, *, desired, recovery=False, expected_generation=None):
    from .runtime_services import AgentRuntimeOperationConflict
    with transaction.atomic():
        runtime = _query().select_for_update(of=("self",)).get(pk=runtime_id)
        if runtime.runtime_kind != "docker" or runtime.status == "deleted":
            raise AgentRuntimeOperationConflict("Not an active Docker deployment.")
        if desired == "running" and (runtime.image is None or runtime.image.status != "active"):
            raise AgentRuntimeOperationConflict("This runtime image has been removed. Select an available image before deploying.")
        if desired == "running" and runtime.agent.status in {"deleted", "disabled", "archived"}:
            raise AgentRuntimeOperationConflict("Agent is not enabled for deployment.")
        state = copy.deepcopy(runtime.docker_lifecycle or {})
        if expected_generation is not None and state.get("generation", "") != expected_generation:
            raise AgentRuntimeOperationConflict("This operation belongs to an older deployment generation.")
        if state.get("host_id", host_id()) != host_id() or _future(state.get("lease_until")):
            raise AgentRuntimeOperationConflict()
        if recovery and (_future(state.get("retry_at")) or state.get("permanent_failure")):
            return None
        if desired == "running" and state.get("desired") == "stopped":
            return None
        if desired == "running" and not recovery and runtime.status == "active":
            return None  # duplicate delivery of an already completed deployment Job
        state.setdefault("generation", uuid.uuid4().hex)
        if desired == "running" and "resources" not in state:
            state["resources"] = resource_limits(runtime)
        state.update(host_id=host_id(), desired=desired, lease_token=uuid.uuid4().hex,
                     lease_until=(timezone.now() + timedelta(seconds=_lease_seconds())).isoformat())
        runtime.docker_lifecycle = state
        if desired == "stopped":
            # Reject new calls immediately, even if the Docker daemon is unavailable.
            runtime.health_status = "unhealthy"
        runtime.save(update_fields=["docker_lifecycle", "health_status", "updated_at"])
        return runtime


def request_stop(runtime_id):
    """Persist intent before publishing a queue message; no daemon I/O here."""
    from .runtime_services import AgentRuntimeOperationConflict
    with transaction.atomic():
        runtime = _query().select_for_update(of=("self",)).get(pk=runtime_id)
        state = copy.deepcopy(runtime.docker_lifecycle or {})
        if _future(state.get("lease_until")) or state.get("host_id", host_id()) != host_id():
            raise AgentRuntimeOperationConflict()
        state.update(desired="stopped", host_id=host_id())
        state.pop("retry_at", None)
        state.pop("attention_required", None)
        state.pop("permanent_failure", None)
        runtime.docker_lifecycle, runtime.health_status = state, "unhealthy"
        runtime.save(update_fields=["docker_lifecycle", "health_status", "updated_at"])
        return runtime


def _locked_claim(runtime):
    from .runtime_services import AgentRuntimeOperationConflict
    current = _query().select_for_update(of=("self",)).get(pk=runtime.pk)
    if (current.docker_lifecycle or {}).get("lease_token") != runtime.docker_lifecycle["lease_token"]:
        raise AgentRuntimeOperationConflict("A newer operation owns this deployment.")
    return current


def _release(state):
    state.pop("lease_token", None)
    state.pop("lease_until", None)


def _policy_failure(exc):
    detail = str(getattr(exc, "detail", "") or exc)
    reasons = {
        "AGENT_EGRESS_POLICY_REQUIRED": "Configure and verify the Docker worker egress policy.",
        "AGENT_IMAGE_ADMISSION_REQUIRED": "Configure an image admission verifier on the Docker worker.",
        "AGENT_IMAGE_ADMISSION_REJECTED": "The image was rejected by the configured admission policy.",
        "AGENT_DOCKER_HOST_REQUIRED": "Configure a stable dedicated Docker worker identity.",
        "AGENT_DOCKER_TOPOLOGY_UNSUPPORTED": "A local Docker worker is required; remote daemon topology is not supported.",
    }
    return next((f"{code}: {message}" for code, message in reasons.items() if code in detail), "")


def deploy(*, runtime_id, actor=None, request=None, recovery=False, expected_generation=None):
    from . import runtime_services as service
    runtime = _claim(runtime_id, desired="running", recovery=recovery, expected_generation=expected_generation)
    if runtime is None:
        return _query().get(pk=runtime_id)
    runner = service.get_runtime_runner(runtime)
    before = service.runtime_snapshot(runtime)
    display_run = None
    result = None
    try:
        if not runtime.image.image_digest:
            resolved = runner.resolve_image(image_ref=runtime.image.image_ref)
            if resolved:
                from .models import AgentRuntimeImage
                AgentRuntimeImage.objects.filter(pk=runtime.image_id, image_digest="").update(image_digest=resolved)
                runtime.image.refresh_from_db(fields=["image_digest"])
        display_run, context = service.create_runtime_display_context(runtime=runtime, request=request)
        service.append_display_event(run=display_run, event_type=AgentDisplayEvent.TYPE_RUN_STARTED,
                                     payload={"title": "Runtime deployment", "env": runtime.env})
        with _LeaseRenewer(runtime):
            result = runner.start(deployment=runtime, display_context=context,
                                  workspace_context=service.create_runtime_workspace_context(runtime=runtime, request=request))
        with transaction.atomic():
            current = _locked_claim(runtime)
            state = current.docker_lifecycle
            old = (state.get("previous") or {}).get("container_id") or current.container_id
            if old and old != result.container_id:
                state.setdefault("retiring", []).append({"container_id": old, "before": timezone.now().isoformat()})
            state.pop("previous", None)
            state.pop("retry_at", None)
            state.update(
                attempts=0,
                attention_required=False,
                permanent_failure=False,
                observed_at=timezone.now().isoformat(),
            )
            _release(state)
            current.last_error = ""
            current.save(update_fields=["docker_lifecycle", "last_error", "updated_at"])
            service.complete_runtime_deploy(runtime=current, result=result, display_run=display_run,
                                            before=before, actor=actor, request=request)
    except Exception as exc:
        # Persist failure OUTSIDE the rolled-back transaction. Keep the exact generation
        # for recovery if Docker succeeded but its DB commit did not.
        previous = runtime.docker_lifecycle.get("previous")
        old_healthy = False
        if previous and result is None:
            old = copy.copy(runtime)
            old.container_id, old.internal_mcp_url, old.status = previous["container_id"], previous["internal_mcp_url"], "active"
            try:
                old_healthy = runner.health_check(deployment=old)
            except Exception:
                pass
        with transaction.atomic():
            current = _locked_claim(runtime)
            state = current.docker_lifecycle
            _release(state)
            attempts = int(state.get("attempts", 0)) + 1
            state.update(attempts=attempts, retry_at=(timezone.now() + timedelta(seconds=min(900, 15 * 2**min(attempts, 6)))).isoformat(),
                         attention_required=attempts >= 5)
            # Do not expose exception bodies from images or process arguments.
            message = (
                "Agent Docker deployment failed; automatic recovery will retry."
                if attempts < 5
                else "Agent Docker deployment failed repeatedly; automatic recovery will continue at reduced frequency while the image is reviewed."
            )
            policy_error = _policy_failure(exc)
            if policy_error:
                state["attention_required"] = True
                state["permanent_failure"] = True
                message = policy_error
            if old_healthy:
                state = previous["state"]
                # Cleanup may itself have failed. Keep a durable exact target rather
                # than forgetting the new generation when rolling back to the old one.
                state.setdefault("retiring", []).append({"container_id": container_name(runtime), "before": timezone.now().isoformat()})
                state.setdefault("host_id", host_id())
                state["desired"] = "running"
                current.image_id = previous["image_id"]
                current.container_id, current.internal_mcp_url = previous["container_id"], previous["internal_mcp_url"]
                current.status, current.health_status = "active", "healthy"
                message = "New Agent image failed readiness; the previous deployment remains active."
                if current.agent_deployment_id:
                    AgentDeployment.objects.filter(pk=current.agent_deployment_id).update(status="active", version_id=current.image.version_id)
            else:
                current.status, current.health_status = "failed", "unhealthy"
                if current.agent_deployment_id:
                    AgentDeployment.objects.filter(pk=current.agent_deployment_id).update(status="failed")
            diagnostics = getattr(exc, "runtime_diagnostics", None)
            if isinstance(diagnostics, dict) and diagnostics:
                state["last_diagnostics"] = {
                    key: value for key, value in diagnostics.items() if key != "log_tail"
                }
                summary = _diagnostic_summary(diagnostics)
                if summary:
                    message = f"{message} {summary}"
            current.docker_lifecycle, current.last_error = state, message
            current.save(update_fields=["docker_lifecycle", "last_error", "status", "health_status", "image", "container_id", "internal_mcp_url", "updated_at"])
            AgentLog.objects.create(agent=current.agent, deployment=current.agent_deployment, level="error", message=message)
            if isinstance(diagnostics, dict) and diagnostics.get("log_tail"):
                AgentLog.objects.create(
                    agent=current.agent,
                    deployment=current.agent_deployment,
                    level="error",
                    message="Container output before failure:\n" + str(diagnostics["log_tail"]),
                )
            if display_run:
                service.append_display_event(run=display_run, event_type=AgentDisplayEvent.TYPE_RUN_FAILED, payload={"message": message})
            service.write_runtime_audit(request=request, actor=actor, runtime=current, action="agents.runtime.deploy.failed", before=before)
        raise service.AgentRuntimeError(message) from exc
    # Discovery and retirement may need network I/O; neither holds the row lock.
    try:
        service.refresh_runtime_tool_policy(runtime=current)
    except Exception:
        AgentLog.objects.create(agent=current.agent, deployment=current.agent_deployment, message="MCP tool discovery deferred; retry health check.")
    retire(runtime_id=current.id)
    return current


def retire(*, runtime_id):
    from .runtime_services import get_runtime_runner
    runtime = _query().get(pk=runtime_id)
    for item in list((runtime.docker_lifecycle or {}).get("retiring", [])):
        cutoff = parse_datetime(item["before"])
        if (AgentRuntimeInvocation.objects.filter(deployment=runtime, status="pending", created_at__lte=cutoff).exists()
                or AgentExecutionTask.objects.filter(runtime=runtime, status__in=["working", "input_required", "cancel_requested"], created_at__lte=cutoff).exists()):
            continue
        old = copy.copy(runtime)
        old.container_id = item["container_id"]
        try:
            get_runtime_runner(old).stop(deployment=old)
        except Exception:
            # Keep the durable retirement record for a later retry.
            continue
        with transaction.atomic():
            current = _query().select_for_update(of=("self",)).get(pk=runtime_id)
            state = current.docker_lifecycle
            state["retiring"] = [x for x in state.get("retiring", []) if x["container_id"] != item["container_id"]]
            current.save(update_fields=["docker_lifecycle", "updated_at"])


def stop(*, runtime_id, actor=None, request=None, expected_generation=None):
    from . import runtime_services as service
    runtime = _claim(runtime_id, desired="stopped", expected_generation=expected_generation)
    before = service.runtime_snapshot(runtime)
    try:
        with _LeaseRenewer(runtime):
            for identifier in {runtime.container_id, container_name(runtime), *[x["container_id"] for x in runtime.docker_lifecycle.get("retiring", [])]} - {""}:
                target = copy.copy(runtime)
                target.container_id = identifier
                service.get_runtime_runner(target).stop(deployment=target)
    except Exception as exc:
        with transaction.atomic():
            current = _locked_claim(runtime)
            _release(current.docker_lifecycle)
            current.last_error = "Docker stop failed; the stop request is retained for retry."
            current.health_status = "unhealthy"
            current.save(update_fields=["docker_lifecycle", "last_error", "health_status", "updated_at"])
        raise service.AgentRuntimeError(current.last_error) from exc
    with transaction.atomic():
        current = _locked_claim(runtime)
        _release(current.docker_lifecycle)
        current.docker_lifecycle["retiring"] = []
        current.status, current.health_status, current.last_error = "stopped", "unknown", ""
        current.container_id, current.internal_mcp_url = "", ""
        current.save(update_fields=["docker_lifecycle", "container_id", "internal_mcp_url", "status", "health_status", "last_error", "updated_at"])
        if current.agent_deployment_id:
            AgentDeployment.objects.filter(pk=current.agent_deployment_id).update(status="stopped")
        service.write_runtime_audit(request=request, actor=actor, runtime=current, action="agents.runtime.stop", before=before)
    return current


def reconcile(*, limit=None):
    """Bounded sweep. Stopped/deleted Agents are never resurrected."""
    if limit is None:
        limit = max(1, int(getattr(settings, "NEXUS_AGENT_RUNTIME_RECONCILE_BATCH_SIZE", 100)))
    token = uuid.uuid4().hex
    lock_seconds = max(120, int(getattr(settings, "NEXUS_AGENT_RUNTIME_RECONCILE_LOCK_SECONDS", 300)))
    if not cache.add(RECONCILE_LOCK_KEY, token, timeout=lock_seconds):
        return 0
    try:
        return _reconcile_locked(limit=limit, lock_token=token, lock_seconds=lock_seconds)
    finally:
        if cache.get(RECONCILE_LOCK_KEY) == token:
            cache.delete(RECONCILE_LOCK_KEY)


def _reconcile_locked(*, limit=25, lock_token="", lock_seconds=300):
    from . import runtime_services as service
    from apps.jobs.models import Job
    from apps.jobs.services import succeed_job, fail_job
    count = 0
    candidates = _query().filter(runtime_kind="docker").exclude(status="deleted").filter(
        Q(docker_lifecycle__host_id=host_id()) | Q(docker_lifecycle={})
    ).exclude(agent__status__in=["deleted", "archived", "disabled"]).order_by("updated_at")[:limit]
    for runtime in candidates:
        if lock_token:
            if cache.get(RECONCILE_LOCK_KEY) != lock_token:
                break
            cache.set(RECONCILE_LOCK_KEY, lock_token, timeout=lock_seconds)
        from apps.common.runtime_health import record_agent_reconciler_heartbeat
        record_agent_reconciler_heartbeat(ok=True)
        state = runtime.docker_lifecycle or {}
        if state.get("attention_required") and not _future(state.get("lease_until")):
            for job in Job.objects.filter(resource_type="agent_runtime_deployment", resource_id=str(runtime.id),
                                          job_type="agents.runtime.deploy", status__in=["queued", "running"]):
                fail_job(job_id=job.id, error_code="AGENT_RUNTIME_DEPLOY_FAILED",
                         error_message="Deployment recovery requires image or worker configuration review.")
        if (
            _future(state.get("lease_until"))
            or _future(state.get("retry_at"))
            or state.get("permanent_failure")
        ):
            AgentRuntimeDeployment.objects.filter(pk=runtime.pk, updated_at=runtime.updated_at).update(updated_at=timezone.now())
            continue
        desired = state.get("desired", "running" if runtime.status in {"active", "deploying"} else "stopped")
        try:
            if desired == "stopped":
                if runtime.status != "stopped":
                    stop(runtime_id=runtime.id)
                else:
                    retire(runtime_id=runtime.id)
                    AgentRuntimeDeployment.objects.filter(pk=runtime.pk, updated_at=runtime.updated_at).update(updated_at=timezone.now())
                for job in Job.objects.filter(resource_type="agent_runtime_deployment", resource_id=str(runtime.id),
                                              job_type="agents.runtime.stop", status__in=["queued", "running"]):
                    succeed_job(job_id=job.id, result_json={"runtime_deployment_id": str(runtime.id), "status": "stopped", "reconciled": True})
                continue
            healthy = runtime.status == "active" and service.get_runtime_runner(runtime).health_check(deployment=runtime)
            if not healthy:
                result = deploy(runtime_id=runtime.id, recovery=True)
                count += int(result.status == "active")
            else:
                # Touch only if no newer deploy/stop changed this observation.
                AgentRuntimeDeployment.objects.filter(pk=runtime.id, updated_at=runtime.updated_at).update(health_status="healthy", updated_at=timezone.now())
                retire(runtime_id=runtime.id)
            current = _query().get(pk=runtime.id)
            for job in Job.objects.filter(resource_type="agent_runtime_deployment", resource_id=str(runtime.id),
                                           job_type="agents.runtime.deploy", status__in=["queued", "running"]):
                if current.status == "active":
                    succeed_job(job_id=job.id, result_json={"runtime_deployment_id": str(runtime.id), "status": "active", "reconciled": True})
        except service.AgentRuntimeOperationConflict:
            continue
        except Exception:
            # One unhealthy deployment must not starve all other Agents. Keep diagnostics bounded.
            AgentRuntimeDeployment.objects.filter(pk=runtime.pk, updated_at=runtime.updated_at).update(
                health_status="unhealthy", last_error="Docker reconciliation failed; verify worker and image availability.", updated_at=timezone.now())
    try:
        valid_runtime_ids = {
            str(value)
            for value in AgentRuntimeDeployment.objects.filter(runtime_kind="docker")
            .exclude(status="deleted")
            .values_list("id", flat=True)
        }
        result = service.get_runtime_runner().sweep_orphans(valid_runtime_ids=valid_runtime_ids)
        if result.get("containers") or result.get("networks"):
            logger.warning(
                "Recovered orphaned Agent Docker objects containers=%s networks=%s",
                result.get("containers", 0),
                result.get("networks", 0),
            )
    except Exception:
        logger.warning("Agent Docker orphan sweep failed", exc_info=False)
    return count


def _diagnostic_summary(diagnostics: dict) -> str:
    if not diagnostics.get("available"):
        return "Container diagnostics were unavailable."
    values = [f"status={str(diagnostics.get('status') or 'unknown')[:32]}"]
    values.append(f"exit_code={int(diagnostics.get('exit_code') or 0)}")
    if diagnostics.get("oom_killed"):
        values.append("oom_killed=true")
    restarts = int(diagnostics.get("restart_count") or 0)
    if restarts:
        values.append(f"restart_count={restarts}")
    return "Container diagnostics: " + ", ".join(values) + "."
