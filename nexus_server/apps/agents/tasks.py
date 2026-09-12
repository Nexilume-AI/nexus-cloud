from __future__ import annotations

try:
    from celery import shared_task
except ImportError:  # pragma: no cover - fallback for minimal local environments
    class _EagerTask:
        def __init__(self, func):
            self.func = func

        def delay(self, *args, **kwargs):
            return self.func(*args, **kwargs)

        def __call__(self, *args, **kwargs):
            return self.func(*args, **kwargs)

    def shared_task(func):
        return _EagerTask(func)

from .models import Agent, AgentDeployment, AgentEndpoint, AgentLog


@shared_task
def reconcile_docker_agent_runtimes():
    from .docker_lifecycle import reconcile
    from django.conf import settings
    from apps.common.runtime_health import record_agent_reconciler_heartbeat
    if settings.NEXUS_AGENT_RUNTIME_RUNNER != "docker":
        if settings.NEXUS_AGENT_RUNTIME_RUNNER != "controller":
            return 0
    try:
        result = reconcile()
    except Exception:
        record_agent_reconciler_heartbeat(ok=False, error_code="AGENT_RECONCILER_FAILED")
        raise
    record_agent_reconciler_heartbeat(ok=True)
    return result


@shared_task
def deploy_runtime_job(job_id: str, runtime_id: str) -> dict[str, str]:
    from apps.jobs.services import fail_job, start_job, succeed_job
    from apps.jobs.models import Job

    from .runtime_services import perform_runtime_deploy

    job = Job.objects.get(pk=job_id)
    if job.status not in {Job.STATUS_QUEUED, Job.STATUS_RUNNING}:
        return {"runtime_deployment_id": str(runtime_id), "status": "already_completed"}
    start_job(job_id=job_id)
    try:
        runtime = perform_runtime_deploy(runtime_id=runtime_id, expected_generation=job.input_json.get("docker_generation"))
    except Exception as exc:
        fail_job(job_id=job_id, error_code="AGENT_RUNTIME_DEPLOY_FAILED", error_message=str(exc))
        raise
    succeed_job(
        job_id=job_id,
        result_json={
            "runtime_deployment_id": str(runtime.id),
            "agent_id": str(runtime.agent_id),
            "status": runtime.status,
            "health_status": runtime.health_status,
        },
    )
    return {"runtime_deployment_id": str(runtime.id), "status": runtime.status}


@shared_task
def stop_runtime_job(job_id: str, runtime_id: str) -> dict[str, str]:
    from apps.jobs.services import fail_job, start_job, succeed_job
    from apps.jobs.models import Job

    from .runtime_services import perform_runtime_stop

    job = Job.objects.get(pk=job_id)
    if job.status not in {Job.STATUS_QUEUED, Job.STATUS_RUNNING}:
        return {"runtime_deployment_id": str(runtime_id), "status": "already_completed"}
    start_job(job_id=job_id)
    try:
        runtime = perform_runtime_stop(runtime_id=runtime_id, expected_generation=job.input_json.get("docker_generation"))
    except Exception as exc:
        fail_job(job_id=job_id, error_code="AGENT_RUNTIME_STOP_FAILED", error_message=str(exc))
        raise
    succeed_job(
        job_id=job_id,
        result_json={
            "runtime_deployment_id": str(runtime.id),
            "agent_id": str(runtime.agent_id),
            "status": runtime.status,
            "health_status": runtime.health_status,
        },
    )
    return {"runtime_deployment_id": str(runtime.id), "status": runtime.status}


@shared_task
def runtime_health_check_job(job_id: str, runtime_id: str) -> dict[str, str]:
    from apps.jobs.services import fail_job, start_job, succeed_job

    from .runtime_services import perform_runtime_health_check

    start_job(job_id=job_id)
    try:
        runtime = perform_runtime_health_check(runtime_id=runtime_id)
    except Exception as exc:
        fail_job(job_id=job_id, error_code="AGENT_RUNTIME_HEALTH_CHECK_FAILED", error_message=str(exc))
        raise
    succeed_job(
        job_id=job_id,
        result_json={
            "runtime_deployment_id": str(runtime.id),
            "agent_id": str(runtime.agent_id),
            "status": runtime.status,
            "health_status": runtime.health_status,
        },
    )
    return {"runtime_deployment_id": str(runtime.id), "status": runtime.status, "health_status": runtime.health_status}


@shared_task
def expire_edge_node_presence_task() -> int:
    from .edge_presence import expire_edge_node_presence

    return expire_edge_node_presence()


from .task_extension import load_task_extensions
_extensions = load_task_extensions()


def __getattr__(name):
    for module in _extensions:
        if not name.startswith('_') and hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)
