"""Image selection is not deployment. Removal preserves runtime/build lineage."""
from django.db import OperationalError, transaction
from django.utils import timezone
from rest_framework import exceptions

from apps.jobs.models import Job
from .models import Agent, AgentExecutionTask, AgentRuntimeDeployment, AgentRuntimeImage


def image_usage_snapshot(agent_id, *, lock=False):
    agent = Agent.objects.only("tenant_id", "current_image_id").get(pk=agent_id)
    runtimes = AgentRuntimeDeployment.objects.filter(agent_id=agent_id).order_by("pk")
    if lock:
        # A lifecycle commit locks its runtime before updating Agent. Never
        # wait for that row while holding Agent, which would invert lock order.
        runtimes = runtimes.select_for_update(nowait=True)
    return {
        "default_id": agent.current_image_id,
        "runtimes": list(runtimes),
        "jobs": list(Job.objects.filter(
            tenant_id=agent.tenant_id, input_json__agent_id=str(agent_id), job_type="agents.runtime.deploy",
            status__in=[Job.STATUS_QUEUED, Job.STATUS_RUNNING],
        ).values_list("input_json__image_id", flat=True)),
        "tasks": list(AgentExecutionTask.objects.filter(agent_id=agent_id).exclude(
            status__in=["completed", "failed", "cancelled"],
        ).values_list("runtime_id", "request_json__agent_version")),
    }


def image_usage(image, snapshot):
    reasons, deployed = set(), []
    for runtime in snapshot["runtimes"]:
        state = runtime.docker_lifecycle or {}
        same = runtime.image_id == image.pk
        if same:
            if runtime.status == "active":
                deployed.append(runtime.env)
                reasons.add("DEPLOYED")
            if runtime.status == "deploying":
                reasons.add("DEPLOYING")
            if state.get("desired") == "running" and runtime.status != "active":
                reasons.add("RECOVERY_REFERENCE")
            if runtime.container_id and runtime.status not in {"active", "deploying"}:
                reasons.add("CONTAINER_REFERENCE")
            if state.get("lease_token"):
                reasons.add("RUNTIME_OPERATION")
            if any(runtime_id == runtime.pk for runtime_id, _ in snapshot["tasks"]):
                reasons.add("RUN_REFERENCE")
        if str((state.get("previous") or {}).get("image_id") or "") == str(image.pk):
            reasons.add("ROLLBACK_REFERENCE")
        # Old retirement records only contain a container ID. Do not claim that
        # an image is unused until that generation has actually been retired.
        if state.get("retiring"):
            reasons.add("RETIREMENT_PENDING")
    if str(image.pk) in snapshot["jobs"] or any(not identifier for identifier in snapshot["jobs"]):
        reasons.add("DEPLOYMENT_QUEUED")
    if image.version_id and any(version == image.version.version for _, version in snapshot["tasks"]):
        reasons.add("RUN_REFERENCE")
    if image.status == "deleted":
        reasons.add("REMOVED")
    return {"is_default": snapshot["default_id"] == image.pk,
            "deployed_environments": sorted(deployed), "can_delete": not reasons,
            "blocking_reasons": sorted(reasons)}


class RuntimeImageInUse(exceptions.APIException):
    status_code = 409
    default_code = "RUNTIME_IMAGE_IN_USE"
    default_detail = "This image is still required by a deployment or Run. Stop or finish that operation before removing it."


def delete_runtime_image(*, request, agent_id, image_id):
    from .runtime_services import get_mutable_runtime_agent
    # Authorization may provision built-in IAM roles on first access. Finish
    # that work before the deletion transaction, as failed-build deletion does.
    agent = get_mutable_runtime_agent(request=request, agent_id=agent_id)
    return _delete_runtime_image(request=request, agent=agent, image_id=image_id)


@transaction.atomic
def _delete_runtime_image(*, request, agent, image_id):
    from .runtime_services import log_write
    # All selection/deploy preparation paths use the same Agent lock. Lock
    # runtimes before images as the lifecycle controller does when committing.
    agent = Agent.objects.select_for_update().get(pk=agent.pk)
    try:
        with transaction.atomic():
            snapshot = image_usage_snapshot(agent.pk, lock=True)
    except OperationalError as exc:
        if getattr(exc.__cause__, "sqlstate", "") == "55P03":
            raise RuntimeImageInUse("A runtime operation is committing. Refresh and try again.") from None
        raise
    image = AgentRuntimeImage.objects.select_for_update(of=("self",)).select_related("version").filter(
        pk=image_id, agent=agent, tenant=agent.tenant,
    ).exclude(status="deleted").first()
    if image is None:
        raise exceptions.NotFound("Agent runtime image not found.")
    if not image_usage(image, snapshot)["can_delete"]:
        raise RuntimeImageInUse()
    if agent.current_image_id == image.pk:
        # Prefer the existing production deployment, never deploy another image.
        replacement = next((runtime.image_id for runtime in snapshot["runtimes"]
                            if runtime.env == "prod" and runtime.status == "active" and runtime.image_id != image.pk), None)
        if replacement and not AgentRuntimeImage.objects.filter(pk=replacement, agent=agent, status="active").exists():
            replacement = None
        agent.current_image_id = replacement
        agent.save(update_fields=["current_image", "updated_at"])
    image.status, image.deleted_at = "deleted", timezone.now()
    image.save(update_fields=["status", "deleted_at", "updated_at"])
    log_write(request=request, action="agents.runtime.image.delete", agent=agent,
              metadata={"image_id": str(image.pk), "default_image_id": str(agent.current_image_id or ""),
                        "removal": "registration_only"})
    return {"deleted_id": str(image.pk), "default_image_id": str(agent.current_image_id) if agent.current_image_id else None,
            "artifacts_retained": True}
