from __future__ import annotations

import base64
import hashlib
import json
import posixpath
import queue
import re
import secrets
import threading
import time
import uuid
from dataclasses import replace
from decimal import Decimal
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import close_old_connections, transaction
from django.db.models import Q, Sum
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError as JsonSchemaValidationError
from rest_framework import exceptions, status

from .runtime_policy import invoke_runtime_policy
from .policy import invoke_agent_policy
from .context_extension import issue_run_context_extension
from apps.audit.services import log_audit, write_audit_log
from apps.common.invocation_lifecycle import (
    InvocationFundingDenied,
    agent_pricing_snapshot,
    begin_runtime_invocation,
    calculate_agent_cost,
    finalize_runtime_invocation,
    invocation_preflight,
    record_invocation,
    release_stale_invocation_reservations,
    report_invocation_billing,
)
from apps.common.models import SoftDeleteModel
from apps.common.subjects import hash_token, request_subject
from apps.common.authorization import has_nexus_permission
from apps.jobs.models import Job
from apps.mobile.models import MobileDevice
from apps.tenancy.models import Project, Tenant
from apps.common.request_context import get_tenant_from_request

from .models import (
    Agent,
    AgentComputerBinding,
    AgentDeployment,
    AgentDisplayEvent,
    AgentDisplayAsset,
    AgentDisplayRun,
    AgentExecutionTask,
    AgentLog,
    AgentMCPSession,
    AgentMemoryItem,
    AgentModelUsage,
    AgentOutputArtifact,
    AgentRunCheckpoint,
    AgentRunInteraction,
    AgentRunMessage,
    AgentRuntimeDeployment,
    AgentRuntimeImage,
    AgentRuntimeInvocation,
    AgentTaskExecution,
    AgentVersion,
    AgentWorkspaceGrant,
    EdgeNode,
)
from .runtime_runner import RuntimeDisplayContext, RuntimeMCPResult, RuntimeMCPStreamResult, RuntimeWorkspaceContext, get_runtime_runner
from .task_execution import assert_execution_lease, enqueue as enqueue_agent_task, request_cancel as request_task_cancel
from .services import (
    AGUI_CUSTOM_COMPUTER_LOG,
    AGUI_CUSTOM_MEMORY_ITEM_DELETED,
    AGUI_CUSTOM_MEMORY_ITEM_UPDATED,
    AGUI_CUSTOM_RUNTIME_STATUS,
    append_display_event,
    export_mcp,
    get_agent,
    get_private_display_run,
    materialize_private_run_result,
    sanitize_display_payload,
    redact_payload,
)
from .tool_catalog import effective_mcp_tools, interaction_tools, runtime_tool_policy, tool_contract_digest
from .workspace_grants import (
    WORKSPACE_CAPABILITIES,
    effective_workspace_capabilities,
    effective_workspace_capabilities_for_run,
    get_workspace_grant,
    is_workspace_setup_tool,
    normalize_workspace_capabilities,
)
from .mobile_access import (
    AgentMobilePermissionRequired,
    AgentMobileRequired,
    AgentMobileUnavailable,
    close_mobile_run,
    effective_declared_mobile_capabilities,
    get_mobile_grant,
    issue_mobile_delegate_token,
    list_mobile_bindings,
    normalize_mobile_capabilities,
    published_mobile_declaration,
    resolve_invocation_mobile_binding,
    runtime_mobile_declaration,
)
from .project_context import (
    ensure_run_project_context_authorized,
    project_context_snapshot,
    project_context_state,
)


_TERMINAL_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|private[_-]?key)"
    r"(\s*(?:=|:)\s*|\s+)([^\s]+)"
)
_TERMINAL_SECRET_FLAG_RE = re.compile(
    r"(?i)(--(?:password|token|secret|api-key|private-key)\s+)([^\s]+)"
)
_TERMINAL_PEM_RE = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)


class AgentRuntimeError(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Agent runtime request failed."
    default_code = "AGENT_RUNTIME_ERROR"


class AgentRuntimeNotFound(AgentRuntimeError):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Agent runtime resource not found."
    default_code = "NOT_FOUND"


class AgentRuntimeNotAvailable(AgentRuntimeError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Agent runtime deployment is not available."
    default_code = "AGENT_RUNTIME_NOT_AVAILABLE"


class AgentRuntimeTemporarilyUnavailable(AgentRuntimeNotAvailable):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


class AgentRuntimeOperationConflict(AgentRuntimeError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Another runtime operation is already in progress."
    default_code = "AGENT_RUNTIME_OPERATION_IN_PROGRESS"


class AgentUsageConflict(AgentRuntimeOperationConflict):
    default_detail = "This model usage event ID was already reported with different values."
    default_code = "AGENT_USAGE_EVENT_CONFLICT"


class AgentPolicyDenied(AgentRuntimeError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "API key policy does not allow this agent."
    default_code = "AGENT_POLICY_DENIED"


class AgentComputerRequired(AgentRuntimeError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This agent requires a caller-owned Computer binding."
    default_code = "COMPUTER_REQUIRED"


class AgentAPIKeyScopeDenied(AgentRuntimeError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "API key scope does not allow agent runtime calls."
    default_code = "API_KEY_SCOPE_DENIED"


class AgentStreamingNotSupported(AgentRuntimeError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Agent runtime streaming is not supported."
    default_code = "AGENT_STREAMING_NOT_SUPPORTED"


class OpenWrtIPv6Required(AgentRuntimeError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Interactive OpenWrt Agents require an IPv6 runtime connection."
    default_code = "OPENWRT_IPV6_REQUIRED"

    def __init__(self, *, runtime: AgentRuntimeDeployment):
        registration = runtime.edge_registration
        capabilities = (
            registration.node.capabilities
            if registration is not None and registration.node_id
            else {}
        )
        super().__init__(
            {
                "code": self.default_code,
                "message": self.default_detail,
                "runtime_kind": runtime.runtime_kind,
                "registration_id": str(registration.id) if registration else "",
                "capabilities": {
                    name: bool((capabilities or {}).get(name))
                    for name in (
                        "runtime_context_v1",
                        "invoke_interactions_v1",
                        "mcp_stream_v1",
                        "mcp_tasks_v1",
                        "durable_recovery_v1",
                    )
                },
            },
            self.default_code,
        )


def runtime_recovery_protocol(*, runtime: AgentRuntimeDeployment, declared: int) -> int:
    if int(declared or 0) < 1:
        return 0
    if runtime.runtime_kind == AgentRuntimeDeployment.RUNTIME_DOCKER:
        return 1
    if runtime.runtime_kind != AgentRuntimeDeployment.RUNTIME_OPENWRT_IPV6:
        return 0
    node = runtime.edge_registration.node if runtime.edge_registration_id and runtime.edge_registration.node_id else None
    return 1 if node and bool((node.capabilities or {}).get("durable_recovery_v1")) else 0


class AgentMemoryRevisionConflict(AgentRuntimeError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Agent Memory was modified by another Run."
    default_code = "MEMORY_REVISION_CONFLICT"

    def __init__(self, *, current_revision: int):
        self.current_revision = int(current_revision)
        super().__init__(self.default_detail, self.default_code)


class AgentMemoryRunClosed(AgentRuntimeError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Agent invocation Run is already closed."
    default_code = "AGENT_RUN_CLOSED"


def list_runtime_images(*, request, agent_id: str):
    agent = get_agent(request=request, agent_id=agent_id)
    return (
        AgentRuntimeImage.objects.filter(tenant=agent.tenant, agent=agent)
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .select_related("version")
        .order_by("-created_at")
    )


@transaction.atomic
def register_runtime_image(*, request, agent_id: str, data: dict[str, Any], uploaded_file=None) -> AgentRuntimeImage:
    agent = get_mutable_runtime_agent(request=request, agent_id=agent_id)
    agent = Agent.objects.select_for_update().get(pk=agent.pk)
    project = resolve_project_from_request(request=request, tenant=agent.tenant)
    version = resolve_version(agent=agent, version_value="")
    artifact_path = ""
    image_ref = data.get("image_ref", "")
    if uploaded_file is not None:
        from apps.common.resource_limits import enforce_capability

        enforce_capability(
            tenant=agent.tenant,
            code="agents.hosted_image_mb",
            requested=Decimal(int(getattr(uploaded_file, "size", 0) or 0)) / Decimal(1024 ** 2),
        )
        artifact_path = save_runtime_image_artifact(agent=agent, uploaded_file=uploaded_file)
        image_ref = get_runtime_runner().load_image(
            artifact_path=str(agent_storage_root() / artifact_path),
            image_ref=image_ref,
        )
    if not image_ref:
        raise exceptions.ValidationError("image_ref or file is required.")
    get_runtime_runner().validate_image_ref(image_ref=image_ref)
    image_digest = get_runtime_runner().resolve_image(image_ref=image_ref)
    image = AgentRuntimeImage.objects.create(
        tenant=agent.tenant,
        project=project,
        agent=agent,
        version=version,
        image_ref=image_ref,
        image_digest=image_digest,
        artifact_path=artifact_path,
        registry_secret_ref=data.get("registry_secret_ref", ""),
        created_by=request.user,
    )
    if agent.current_image_id is None:
        agent.current_image = image
        if not agent.current_version and version is not None:
            agent.current_version = version.version
        agent.save(update_fields=["current_image", "current_version", "updated_at"])
    log_write(
        request=request,
        action="agents.runtime.image.register",
        agent=agent,
        metadata={
            "image_id": str(image.id),
            "image_ref": image.image_ref,
            "version": version.version if version else "",
            "artifact_path": artifact_path,
        },
    )
    return image


def set_current_runtime_image(*, request, agent_id: str, image_id: str) -> AgentRuntimeImage:
    agent = get_mutable_runtime_agent(request=request, agent_id=agent_id)
    return _set_current_runtime_image(request=request, agent=agent, image_id=image_id)


@transaction.atomic
def _set_current_runtime_image(*, request, agent, image_id: str) -> AgentRuntimeImage:
    agent = Agent.objects.select_for_update().get(pk=agent.pk)
    image = (
        AgentRuntimeImage.objects.filter(
            tenant=agent.tenant,
            agent=agent,
            id=image_id,
            status=SoftDeleteModel.STATUS_ACTIVE,
        )
        .select_related("version")
        .first()
    )
    if image is None:
        raise AgentRuntimeNotFound("Agent runtime image not found.")
    agent.current_image = image
    if image.version_id:
        agent.current_version = image.version.version
    agent.save(update_fields=["current_image", "current_version", "updated_at"])
    log_write(
        request=request,
        action="agents.runtime.image.set_current",
        agent=agent,
        metadata={"image_id": str(image.id), "image_ref": image.image_ref},
    )
    return image


def deploy_runtime(
    *,
    request,
    agent_id: str,
    image_id=None,
    env: str = "prod",
    workspace_connection_id=None,
    workspace_root: str = "",
    workspace_access_mode: str | None = None,
) -> AgentRuntimeDeployment:
    runtime = prepare_runtime_deploy(
        request=request,
        agent_id=agent_id,
        image_id=image_id,
        env=env,
        workspace_connection_id=workspace_connection_id,
        workspace_root=workspace_root,
        workspace_access_mode=workspace_access_mode,
    )
    return perform_runtime_deploy(runtime_id=runtime.id, actor=request.user, request=request)


@transaction.atomic
def prepare_runtime_deploy(
    *,
    request,
    agent_id: str,
    image_id=None,
    env: str = "prod",
    workspace_connection_id=None,
    workspace_root: str = "",
    workspace_access_mode: str | None = None,
) -> AgentRuntimeDeployment:
    agent = get_mutable_runtime_agent(request=request, agent_id=agent_id)
    agent = Agent.objects.select_for_update().get(pk=agent.pk)
    image = resolve_image(agent=agent, image_id=image_id)
    from .models import AgentPythonBuild
    from .docker_policy import host_id
    python_build = AgentPythonBuild.objects.filter(image=image).first()
    if python_build and python_build.target_host != host_id():
        raise AgentRuntimeOperationConflict("Python image belongs to a different Docker worker. Route deployment to its assigned host.")
    get_runtime_runner().validate_image_ref(image_ref=image.image_ref)
    from .docker_lifecycle import prepare_state
    lifecycle = prepare_state(agent=agent, env=env, image=image)
    # Runtime-level workspaces are deprecated. A caller-owned Computer is
    # resolved for each tools/call and exposed only through a run-scoped proxy.
    workspace_connection = None
    access_mode = AgentRuntimeDeployment.WORKSPACE_READ_ONLY
    agent_deployment, _ = AgentDeployment.objects.update_or_create(
        agent=agent,
        env=env,
        defaults={
            "version": image.version,
            "status": AgentDeployment.STATUS_DEPLOYING,
            "deployed_by": request.user,
        },
    )
    runtime, _ = AgentRuntimeDeployment.objects.update_or_create(
        tenant=agent.tenant,
        agent=agent,
        env=env,
        defaults={
            "project": image.project,
            "agent_deployment": agent_deployment,
            "runtime_kind": AgentRuntimeDeployment.RUNTIME_DOCKER,
            "image": image,
            "edge_registration": None,
            "status": AgentRuntimeDeployment.STATUS_DEPLOYING,
            "health_status": AgentRuntimeDeployment.HEALTH_UNKNOWN,
            "last_error": "",
            "workspace_connection": None,
            "workspace_root": "",
            "workspace_access_mode": access_mode,
            "workspace_token": "",
            "deployed_by": request.user,
            "docker_lifecycle": lifecycle,
        },
    )
    log_write(
        request=request,
        action="agents.runtime.deploy",
        agent=agent,
        metadata={"runtime_deployment_id": str(runtime.id), "image_id": str(image.id), "env": env},
    )
    return runtime


def enqueue_deploy_runtime(
    *,
    request,
    agent_id: str,
    image_id=None,
    env: str = "prod",
    workspace_connection_id=None,
    workspace_root: str = "",
    workspace_access_mode: str | None = None,
) -> tuple[AgentRuntimeDeployment, Any]:
    from apps.jobs.services import create_job, set_celery_task_id

    from .tasks import deploy_runtime_job

    # First-use IAM provisioning must commit before the operation transaction.
    # Otherwise a concurrent image removal can wait on IAM while this request
    # holds uncommitted roles and attempts to re-enter IAM's process lock.
    get_mutable_runtime_agent(request=request, agent_id=agent_id)
    with transaction.atomic():
        operation_agent = lock_runtime_operation(
            request=request,
            agent_id=agent_id,
            env=env,
        )
        runtime = prepare_runtime_deploy(
            request=request,
            agent_id=str(operation_agent.id),
            image_id=image_id,
            env=env,
            workspace_connection_id=workspace_connection_id,
            workspace_root=workspace_root,
            workspace_access_mode=workspace_access_mode,
        )
        job = create_job(
            request=request,
            job_type="agents.runtime.deploy",
            resource_type="agent_runtime_deployment",
            resource_id=str(runtime.id),
            project=runtime.project,
            input_json={
                "agent_id": str(runtime.agent_id),
                "image_id": str(runtime.image_id),
                "env": runtime.env,
                "workspace_connection_id": str(runtime.workspace_connection_id or ""),
                "workspace_root": runtime.workspace_root,
                "workspace_access_mode": runtime.workspace_access_mode,
                "docker_generation": (runtime.docker_lifecycle or {}).get("generation", ""),
            },
        )
    async_result = dispatch_runtime_job(deploy_runtime_job, job=job, runtime=runtime)
    raise_eager_task_error(async_result)
    task_id = getattr(async_result, "id", "") or ""
    if task_id:
        set_celery_task_id(job_id=job.id, celery_task_id=task_id)
    runtime.refresh_from_db()
    return runtime, job


def perform_runtime_deploy(*, runtime_id, actor=None, request=None, expected_generation=None) -> AgentRuntimeDeployment:
    from .docker_lifecycle import deploy
    return deploy(runtime_id=runtime_id, actor=actor, request=request, expected_generation=expected_generation)


def complete_runtime_deploy(*, runtime, result, display_run, before, actor=None, request=None):
    """Commit the observed container and audit in the caller's short transaction."""
    agent = runtime.agent
    from .python_contract import activate_python_contract
    activate_python_contract(agent=agent, version=runtime.image.version)
    agent_deployment = runtime.agent_deployment
    runtime.container_id = result.container_id
    runtime.internal_mcp_url = result.internal_mcp_url
    runtime.status = AgentRuntimeDeployment.STATUS_ACTIVE
    runtime.health_status = AgentRuntimeDeployment.HEALTH_HEALTHY
    runtime.save(update_fields=["container_id", "internal_mcp_url", "status", "health_status", "updated_at"])
    agent_deployment.status = AgentDeployment.STATUS_ACTIVE
    agent_deployment.endpoint_url = public_mcp_url(request=request, agent=agent)
    agent_deployment.save(update_fields=["status", "endpoint_url", "updated_at"])
    # A suspend/archive during a slow Docker operation must not be undone by
    # its eventual completion (including background reconciliation).
    Agent.objects.filter(id=agent.id, status__in=[Agent.STATUS_DRAFT, Agent.STATUS_ACTIVE]).update(status=Agent.STATUS_ACTIVE)
    AgentLog.objects.create(agent=agent, deployment=agent_deployment, message=f"Runtime deployment {runtime.env} is active.")
    append_display_event(
        run=display_run,
        event_type=AgentDisplayEvent.TYPE_RUNTIME_STATUS,
        payload={
            "name": AGUI_CUSTOM_RUNTIME_STATUS,
            "value": {
                "status": runtime.status,
                "health_status": runtime.health_status,
                "env": runtime.env,
                "image_ref": runtime.image.image_ref,
            },
        },
    )
    append_display_event(
        run=display_run,
        event_type=AgentDisplayEvent.TYPE_COMPUTER_LOG,
        payload={"name": AGUI_CUSTOM_COMPUTER_LOG, "value": {"level": "info", "message": f"Runtime deployment {runtime.env} is active."}},
    )
    write_runtime_audit(
        request=request,
        actor=actor,
        runtime=runtime,
        action="agents.runtime.deploy.succeeded",
        before=before,
    )
    return runtime


def create_runtime_display_context(*, runtime: AgentRuntimeDeployment, request=None) -> tuple[AgentDisplayRun, RuntimeDisplayContext]:
    from .cloud_trust import hosted_cloud_trust
    trust = hosted_cloud_trust()
    public_base_url = request.build_absolute_uri("/") if request else str(getattr(settings, "NEXUS_PUBLIC_BASE_URL", "http://localhost:8000"))
    public_base_url = public_base_url.rstrip("/")
    events_base_url = str(getattr(settings, "NEXUS_AGENT_DISPLAY_EVENTS_BASE_URL", public_base_url)).rstrip("/")
    run = AgentDisplayRun.objects.create(
        tenant=runtime.tenant,
        agent=runtime.agent,
        runtime=runtime,
        status=AgentDisplayRun.STATUS_RUNNING,
        title=f"{runtime.agent.name} runtime deployment",
        write_token=secrets.token_urlsafe(32),
        run_kind=AgentDisplayRun.KIND_DEPLOYMENT,
    )
    return run, RuntimeDisplayContext(
        run_id=str(run.id),
        events_url=f"{events_base_url}/api/v1/internal/ag-ui/runs/{run.id}/events/",
        write_token=run.write_token,
        public_url=f"{public_base_url}/share/agents/{runtime.agent_id}",
        cloud_trust=trust,
    )




@transaction.atomic
def create_invocation_display_context(
    *,
    runtime: AgentRuntimeDeployment,
    tool_name: str,
    request=None,
    execution_profile: dict[str, Any] | None = None,
    reasoning_effort: str = "",
) -> tuple[AgentDisplayRun, RuntimeDisplayContext]:
    public_base_url = request.build_absolute_uri("/") if request else str(getattr(settings, "NEXUS_PUBLIC_BASE_URL", "http://localhost:8000"))
    public_base_url = public_base_url.rstrip("/")
    events_base_url = str(getattr(settings, "NEXUS_AGENT_DISPLAY_EVENTS_BASE_URL", public_base_url)).rstrip("/")
    title = f"{runtime.agent.name}: {tool_name or 'tools/call'}"
    consumer_tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    workspace_capabilities = effective_workspace_capabilities(request=request, agent=runtime.agent)
    mobile_requirement, declared_mobile_capabilities = runtime_mobile_declaration(runtime)
    tool_policy = runtime_tool_policy(agent=runtime.agent, runtime=runtime, tool_name=tool_name)
    tool_mobile_scopes = list(tool_policy.get("mobile_scopes") or [])
    required_mobile_capabilities = (
        [value for value in declared_mobile_capabilities if value in set(tool_mobile_scopes)]
        if tool_mobile_scopes
        else declared_mobile_capabilities
    )
    mobile_capabilities = effective_declared_mobile_capabilities(
        request=request,
        agent=runtime.agent,
        declared_capabilities=declared_mobile_capabilities,
    )
    if tool_mobile_scopes:
        mobile_capabilities = [value for value in mobile_capabilities if value in set(tool_mobile_scopes)]
    if runtime.runtime_kind == AgentRuntimeDeployment.RUNTIME_OPENWRT_RELAY:
        mobile_capabilities = []
    binding = resolve_invocation_computer_binding(
        request=request,
        agent=runtime.agent,
        tenant=consumer_tenant,
        tool_name=tool_name,
        workspace_capabilities=workspace_capabilities,
    )
    mobile_binding = None
    if runtime.runtime_kind != AgentRuntimeDeployment.RUNTIME_OPENWRT_RELAY:
        mobile_binding = resolve_invocation_mobile_binding(
            request=request,
            agent=runtime.agent,
            tenant=consumer_tenant,
            mobile_capabilities=mobile_capabilities,
            mobile_requirement=mobile_requirement,
            required_capabilities=required_mobile_capabilities,
        )
    display_token = secrets.token_urlsafe(32)
    run_write_token = secrets.token_urlsafe(32)
    interaction_token = secrets.token_urlsafe(32)
    workspace_delegate_token = secrets.token_urlsafe(32) if workspace_capabilities else ""
    browser_enabled = bool(binding is not None and "browser.control" in workspace_capabilities)
    browser_delegate_token = secrets.token_urlsafe(32) if browser_enabled else ""
    mobile_delegate_token, mobile_delegate_hash, mobile_delegate_expires_at = issue_mobile_delegate_token(
        capabilities=mobile_capabilities if mobile_binding is not None else [],
    )
    accept_header = str(request.headers.get("Accept") or "").lower() if request else ""
    interaction_mode = "stream" if "text/event-stream" in accept_header else "json"
    context_extension = issue_run_context_extension(
        agent=runtime.agent, tool_name=tool_name, now=timezone.now()
    )
    context_token = secrets.token_urlsafe(32)
    project_snapshot = project_context_snapshot(request=request, agent=runtime.agent)
    from apps.common.resource_limits import enforce_capability, release_capability_reservation, reserve_capability

    Tenant.objects.select_for_update().get(id=runtime.tenant_id)
    run_id = uuid.uuid4()
    reservation_key = f"agent-run:{run_id}"
    reserve_capability(
        tenant=runtime.tenant,
        code="agents.concurrent_runs",
        idempotency_key=reservation_key,
        ttl_seconds=300,
    )
    enforce_capability(tenant=runtime.tenant, code="agents.runs_per_30_days")
    run = AgentDisplayRun.objects.create(
        id=run_id,
        tenant=runtime.agent.tenant,
        agent=runtime.agent,
        runtime=runtime,
        run_kind=AgentDisplayRun.KIND_INVOCATION,
        consumer_tenant=consumer_tenant,
        consumer_project_id=getattr(request, "project_id", None) or None,
        caller_principal_type=subject.principal_type,
        caller_principal_id=subject.principal_id,
        caller_subject_hash=subject.subject_hash,
        computer_binding=binding,
        mobile_binding=mobile_binding,
        status=AgentDisplayRun.STATUS_RUNNING,
        title=title,
        write_token=run_write_token,
        display_token_hash=hash_token(display_token),
        display_token_expires_at=timezone.now() + timedelta(hours=1),
        interaction_token_hash=hash_token(interaction_token),
        interaction_token_expires_at=timezone.now() + timedelta(hours=1),
        context_token_hash=hash_token(context_token),
        context_token_expires_at=timezone.now() + timedelta(hours=1),
        interaction_mode=interaction_mode,
        project_context_snapshot=project_snapshot,
        workspace_capabilities_snapshot=workspace_capabilities,
        workspace_delegate_token_hash=hash_token(workspace_delegate_token) if workspace_delegate_token else "",
        workspace_delegate_token_expires_at=(timezone.now() + timedelta(hours=1)) if workspace_delegate_token else None,
        browser_delegate_token_hash=hash_token(browser_delegate_token) if browser_delegate_token else "",
        browser_delegate_token_expires_at=(timezone.now() + timedelta(hours=1)) if browser_delegate_token else None,
        mobile_capabilities_snapshot=mobile_capabilities if mobile_binding is not None else [],
        mobile_delegate_token_hash=mobile_delegate_hash,
        **context_extension.model_values,
        mobile_delegate_token_expires_at=mobile_delegate_expires_at,
    )
    release_capability_reservation(
        tenant=runtime.tenant,
        code="agents.concurrent_runs",
        idempotency_key=reservation_key,
    )
    if binding is not None:
        base_root = str(binding.connection.workspace_root or "~/.nexus").rstrip("/")
        agent_root = posixpath.join(base_root, "agents", str(runtime.agent_id))
        run.workspace_root = posixpath.join(agent_root, "workspace")
        run.output_root = posixpath.join(agent_root, "runs", str(run.id), "outputs")
        run.save(update_fields=["workspace_root", "output_root", "updated_at"])
        binding.last_used_at = timezone.now()
        binding.save(update_fields=["last_used_at", "updated_at"])
    if mobile_binding is not None:
        mobile_binding.last_used_at = timezone.now()
        mobile_binding.save(update_fields=["last_used_at", "updated_at"])
    internal_root = f"{events_base_url}/api/v1/internal/agent-runs/{run.id}"
    display_url = f"{public_base_url}/agent-runs/{run.id}/display"
    context = RuntimeDisplayContext(
        run_id=str(run.id),
        events_url=f"{events_base_url}/api/v1/internal/ag-ui/runs/{run.id}/events/",
        write_token=run.write_token,
        public_url=display_url,
        display_token=display_token,
        display_url=display_url,
        computer_enabled=binding is not None,
        terminal_url=f"{internal_root}/terminal/",
        workspace_url=f"{internal_root}/workspace/",
        memory_url=f"{internal_root}/memory/",
        workspace_root=run.workspace_root,
        output_root=run.output_root,
        workspace_delegate_url=f"{internal_root}/manage/",
        workspace_delegate_token=workspace_delegate_token,
        workspace_capabilities=tuple(workspace_capabilities),
        browser_enabled=browser_enabled,
        browser_delegate_url=f"{internal_root}/browser/" if browser_enabled else "",
        browser_delegate_token=browser_delegate_token,
        browser_computer_name=binding.connection.name if browser_enabled else "",
        mobile_enabled=mobile_binding is not None,
        mobile_delegate_url=f"{internal_root}/mobile/",
        mobile_delegate_token=mobile_delegate_token,
        mobile_capabilities=tuple(mobile_capabilities if mobile_binding is not None else []),
        interaction_url=f"{internal_root}/interactions/",
        context_url=f"{internal_root}/context/",
        context_token=context_token,
        checkpoint_url=f"{internal_root}/checkpoint/",
        display_asset_url=f"{internal_root}/display-assets/",
        interaction_token=interaction_token,
        interaction_mode=interaction_mode,
        turn_index=1,
        **context_extension.display_values(internal_root),
        usage_url=f"{internal_root}/usage/",
        execution_profile=str((execution_profile or {}).get("id") or ""),
        execution_model=str((execution_profile or {}).get("model") or ""),
        reasoning_effort=str(reasoning_effort or ""),
        execution_context_window=int((execution_profile or {}).get("context_window") or 0),
    )
    append_display_event(
        run=run,
        event_type=AgentDisplayEvent.TYPE_RUN_STARTED,
        payload={
            "threadId": str(runtime.agent_id),
            "runId": str(run.id),
        },
    )
    return run, context


def resolve_invocation_computer_binding(
    *,
    request,
    agent: Agent,
    tenant: Tenant,
    tool_name: str = "",
    workspace_capabilities: list[str] | None = None,
) -> AgentComputerBinding | None:
    if agent.computer_requirement == Agent.COMPUTER_DISABLED:
        return None
    subject = request_subject(request)
    requested_id = str(request.headers.get("X-Nexus-Agent-Computer-Binding") or "").strip()
    queryset = AgentComputerBinding.objects.filter(
        tenant=tenant,
        agent=agent,
        caller_subject_hash=subject.subject_hash,
        status=SoftDeleteModel.STATUS_ACTIVE,
        connection__owner_subject_hash=subject.subject_hash,
        connection__connection_type="runtime",
        connection__status=SoftDeleteModel.STATUS_ACTIVE,
    ).select_related("connection", "connection__runtime_device")
    binding = queryset.filter(id=requested_id).first() if requested_id else queryset.filter(is_default=True).first()
    if requested_id and binding is None:
        raise AgentRuntimeNotFound("Computer binding not found.")
    setup_allowed = is_workspace_setup_tool(tool_name) and bool(workspace_capabilities)
    if binding is None and agent.computer_requirement == Agent.COMPUTER_REQUIRED and not setup_allowed:
        raise AgentComputerRequired()
    if binding is not None:
        from apps.workspaces.computer_runtime import ensure_runtime_connection

        ensure_runtime_connection(binding.connection, operation="workspace.test")
        operations = {
            "browser.control": "browser.open",
            "command.execute": "command.execute",
            "tool.setup": "tool_setup.detect",
        }
        for scope in workspace_capabilities or []:
            ensure_runtime_connection(
                binding.connection,
                operation=operations.get(scope, "workspace.test"),
            )
    return binding


def runtime_headers_with_agui(
    *,
    headers: dict[str, str],
    context: RuntimeDisplayContext | None,
) -> dict[str, str]:
    safe_headers = {
        key: value
        for key, value in headers.items()
        if not key.lower().startswith("x-nexus-agui-")
        and not key.lower().startswith("x-nexus-agent-")
        and not key.lower().startswith("x-nexus-workspace-")
        and not key.lower().startswith("x-nexus-computer-")
        and not key.lower().startswith("x-nexus-interaction-")
        and not key.lower().startswith("x-nexus-checkpoint-")
        and not key.lower().startswith("x-nexus-recovery-")
        and not key.lower().startswith("x-nexus-display-asset-")
        and not key.lower().startswith("x-nexus-browser-")
        and not key.lower().startswith("x-nexus-mobile-")
        and not key.lower().startswith("x-nexus-run-")
        and not key.lower().startswith("x-nexus-context-")
        and not key.lower().startswith("x-nexus-billing-")
        and not key.lower().startswith("x-nexus-usage-")
        and not key.lower().startswith("x-nexus-execution-")
        and not key.lower().startswith("x-nexus-input-")
        and key.lower() != "x-nexus-end-user"
    }
    if context is not None:
        safe_headers.update(
            {
                "X-Nexus-AGUI-Run-Id": context.run_id,
                "X-Nexus-AGUI-Events-Url": context.events_url,
                "X-Nexus-AGUI-Token": context.write_token,
                "X-Nexus-Computer-Enabled": "true" if context.computer_enabled else "false",
                "X-Nexus-Terminal-Url": context.terminal_url,
                "X-Nexus-Workspace-Url": context.workspace_url,
                "X-Nexus-Memory-Url": context.memory_url,
                "X-Nexus-Workspace-Token": context.workspace_delegate_token or context.write_token,
                "X-Nexus-Workspace-Root": context.workspace_root,
                "X-Nexus-Output-Root": context.output_root,
                "X-Nexus-Workspace-Delegate-Url": context.workspace_delegate_url,
                "X-Nexus-Workspace-Delegate-Token": context.workspace_delegate_token,
                "X-Nexus-Workspace-Capabilities": ",".join(context.workspace_capabilities),
                "X-Nexus-Browser-Enabled": "true" if context.browser_enabled else "false",
                "X-Nexus-Browser-Delegate-Url": context.browser_delegate_url,
                "X-Nexus-Browser-Delegate-Token": context.browser_delegate_token,
                "X-Nexus-Browser-Computer-Name": context.browser_computer_name,
                "X-Nexus-Mobile-Enabled": "true" if context.mobile_enabled else "false",
                "X-Nexus-Mobile-Delegate-Url": context.mobile_delegate_url,
                "X-Nexus-Mobile-Delegate-Token": context.mobile_delegate_token,
                "X-Nexus-Mobile-Capabilities": ",".join(context.mobile_capabilities),
                "X-Nexus-Interaction-Url": context.interaction_url,
                "X-Nexus-Interaction-Token": context.interaction_token,
                "X-Nexus-Interaction-Mode": context.interaction_mode,
                "X-Nexus-Context-Url": context.context_url,
                "X-Nexus-Context-Token": context.context_token,
                "X-Nexus-Run-Turn": str(context.turn_index),
                "X-Nexus-Checkpoint-Url": context.checkpoint_url,
                "X-Nexus-Recovery-Url": context.recovery_url,
                "X-Nexus-Recovery-Managed": "true" if context.recovery_managed else "false",
                "X-Nexus-Recovery-Attempt": str(context.recovery_attempt),
                "X-Nexus-Recovery-Replay": "true" if context.recovery_is_replay else "false",
                "X-Nexus-Recovery-Last-Committed": str(context.recovery_last_committed_operation or ""),
                "X-Nexus-Display-Asset-Url": context.display_asset_url,
                "X-Nexus-Billing-Url": context.billing_url,
                "X-Nexus-Billing-Token": context.billing_token,
                "X-Nexus-Billing-Currency": context.billing_currency,
                "X-Nexus-Billing-Max-Cost": context.billing_max_cost,
                "X-Nexus-Usage-Url": context.usage_url,
                "X-Nexus-Execution-Profile": context.execution_profile,
                "X-Nexus-Execution-Model": context.execution_model,
                "X-Nexus-Reasoning-Effort": context.reasoning_effort,
                "X-Nexus-Execution-Context-Window": str(context.execution_context_window or ""),
                "X-Nexus-Input-Files": json.dumps(list(context.input_files), separators=(",", ":"), ensure_ascii=False),
            }
        )
    return safe_headers


def finish_invocation_display_run(
    *,
    run: AgentDisplayRun | None,
    succeeded: bool,
    error_code: str = "",
    error_message: str = "",
) -> None:
    assert_execution_lease()
    if run is None:
        return
    close_mobile_run(run=run)
    run.refresh_from_db(fields=["status"])
    if run.status != AgentDisplayRun.STATUS_RUNNING:
        return
    if succeeded:
        append_display_event(
            run=run,
            event_type=AgentDisplayEvent.TYPE_RUN_COMPLETED,
            payload={
                "threadId": str(run.agent_id),
                "runId": str(run.id),
            },
        )
        snapshot_invocation_outputs(run=run)
        close_invocation_terminal(run=run)
    else:
        append_display_event(
            run=run,
            event_type=AgentDisplayEvent.TYPE_RUN_FAILED,
            payload={
                "message": str(error_message or "Agent runtime invocation failed.")[:300],
                "code": str(error_code or "MCP_REQUEST_FAILED")[:64],
            },
        )
        close_invocation_terminal(run=run)
    AgentDisplayRun.objects.filter(id=run.id).update(
        context_token_hash="",
        context_token_expires_at=None,
    )


def close_invocation_terminal(*, run: AgentDisplayRun) -> None:
    from apps.workspaces.models import WorkspaceTerminalSession

    session = getattr(run, "terminal_session", None)
    if session is None or session.status == WorkspaceTerminalSession.STATUS_FAILED:
        return
    session.status = WorkspaceTerminalSession.STATUS_CLOSED
    session.ended_at = timezone.now()
    session.save(update_fields=["status", "ended_at", "updated_at"])


def snapshot_invocation_outputs(*, run: AgentDisplayRun) -> None:
    """Best-effort immutable copy; invocation success never depends on storage."""
    if run.run_kind != AgentDisplayRun.KIND_INVOCATION or run.computer_binding_id is None:
        return
    from django.core.files.base import ContentFile
    from apps.datasets.storage_backends import get_dataset_storage_backend
    from apps.workspaces.execution import WorkspaceNotFound, read_workspace_file
    from .services import _run_message_turn

    # Events already materialize manifests at ingestion. Replaying older events
    # here would manufacture current-turn artifacts from previous-turn paths.
    for artifact in run.output_artifacts.filter(computer_revision=run.computer_revision,
            turn_index=_run_message_turn(run)).exclude(status=SoftDeleteModel.STATUS_DELETED):
        # The run-scoped output endpoint snapshots new SDK writes from the
        # acknowledged request body. Keep this completion-time read for old
        # SDKs and Agents that publish a manifest for a file written by other
        # means, but never duplicate an already durable object.
        if artifact.snapshot_status == "ready" and artifact.snapshot_object_key:
            continue
        try:
            # Windows OpenSSH can acknowledge an SFTP close just before a new
            # session observes the directory entry. Snapshotting is the only
            # cross-session read on this completion boundary, so tolerate the
            # bounded visibility window without retrying authorization,
            # policy, or storage failures.
            result = None
            for attempt, delay in enumerate((0.0, 0.05, 0.15, 0.3)):
                if delay:
                    time.sleep(delay)
                try:
                    result = read_workspace_file(
                        connection=run.computer_binding.connection,
                        root=run.output_root,
                        path=artifact.workspace_path,
                    )
                    break
                except WorkspaceNotFound:
                    if attempt == 3:
                        raise
            if result is None:  # pragma: no cover - defensive loop guard
                raise WorkspaceNotFound("Workspace output was not visible.")
            content = str(result.get("content") or "").encode("utf-8")
            uploaded = ContentFile(content, name=artifact.original_file_name)
            uploaded.content_type = artifact.content_type or "application/octet-stream"
            backend = get_dataset_storage_backend()
            stored = backend.save(
                tenant=run.consumer_tenant or run.tenant,
                dataset_id=f"agent-outputs/{run.agent_id}/{run.id}",
                file_name=artifact.original_file_name,
                uploaded_file=uploaded,
            )
            artifact.snapshot_storage_backend = stored.backend
            artifact.snapshot_object_key = stored.object_key
            artifact.snapshot_status = "ready"
            artifact.snapshot_error = ""
            artifact.size_bytes = stored.size_bytes
            artifact.sha256 = stored.sha256
            artifact.snapshotted_at = timezone.now()
            artifact.save(
                update_fields=[
                    "snapshot_storage_backend",
                    "snapshot_object_key",
                    "snapshot_status",
                    "snapshot_error",
                    "size_bytes",
                    "sha256",
                    "snapshotted_at",
                    "updated_at",
                ]
            )
        except Exception as exc:
            artifact.snapshot_status = "failed"
            from apps.workspaces.execution import redact_workspace_output

            detail = redact_workspace_output(str(exc)).strip()
            artifact.snapshot_error = (
                f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
            )[:1024]
            artifact.save(update_fields=["snapshot_status", "snapshot_error", "updated_at"])


def snapshot_invocation_output_content(
    *,
    run: AgentDisplayRun,
    path: str,
    content: str | bytes,
    content_type: str = "",
    computer_revision: int | None = None,
    turn_index: int | None = None,
) -> AgentOutputArtifact:
    """Persist caller-downloadable bytes at the authenticated output write boundary."""

    from apps.datasets.storage_backends import get_dataset_storage_backend
    from apps.workspaces.execution import redact_workspace_output

    workspace_path = posixpath.normpath(str(path or "").strip().replace("\\", "/"))
    original_file_name = posixpath.basename(workspace_path)
    if turn_index is None:
        task = getattr(run, "execution_task", None)
        turn_index = max(
            int((getattr(task, "request_json", {}) or {}).get("turn_index") or 1),
            1,
        )
    artifact, _ = AgentOutputArtifact.objects.update_or_create(
        run=run,
        turn_index=turn_index,
        workspace_path=workspace_path,
        computer_revision=run.computer_revision if computer_revision is None else computer_revision,
        defaults={
            "tenant": run.tenant,
            "project": run.agent.project,
            "agent": run.agent,
            "runtime": run.runtime,
            "original_file_name": original_file_name,
            **({"content_type": content_type} if content_type else {}),
        },
    )
    try:
        content_bytes = content if isinstance(content, bytes) else str(content).encode("utf-8")
        uploaded = ContentFile(content_bytes, name=original_file_name)
        uploaded.content_type = content_type or "application/octet-stream"
        stored = get_dataset_storage_backend().save(
            tenant=run.consumer_tenant or run.tenant,
            dataset_id=f"agent-outputs/{run.agent_id}/{run.id}",
            file_name=original_file_name,
            uploaded_file=uploaded,
        )
        artifact.snapshot_storage_backend = stored.backend
        artifact.snapshot_object_key = stored.object_key
        artifact.snapshot_status = "ready"
        artifact.snapshot_error = ""
        artifact.size_bytes = stored.size_bytes
        artifact.sha256 = stored.sha256
        artifact.snapshotted_at = timezone.now()
        artifact.save(
            update_fields=[
                "snapshot_storage_backend",
                "snapshot_object_key",
                "snapshot_status",
                "snapshot_error",
                "size_bytes",
                "sha256",
                "snapshotted_at",
                "updated_at",
            ]
        )
    except Exception as exc:
        detail = redact_workspace_output(str(exc)).strip()
        artifact.snapshot_status = "failed"
        artifact.snapshot_error = (
            f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
        )[:1024]
        artifact.save(update_fields=["snapshot_status", "snapshot_error", "updated_at"])
    return artifact


def create_runtime_workspace_context(*, runtime: AgentRuntimeDeployment, request=None) -> RuntimeWorkspaceContext | None:
    return None


def stop_runtime(*, request, agent_id: str, env: str = "prod") -> AgentRuntimeDeployment:
    agent = get_mutable_runtime_agent(request=request, agent_id=agent_id)
    runtime = get_runtime_deployment(agent=agent, env=env, include_inactive=True)
    return perform_runtime_stop(runtime_id=runtime.id, actor=request.user, request=request)


def enqueue_stop_runtime(*, request, agent_id: str, env: str = "prod") -> tuple[AgentRuntimeDeployment, Any]:
    from apps.jobs.services import create_job, set_celery_task_id

    from .tasks import stop_runtime_job

    with transaction.atomic():
        agent = lock_runtime_operation(request=request, agent_id=agent_id, env=env)
        runtime = get_runtime_deployment(agent=agent, env=env, include_inactive=True)
        if runtime.runtime_kind == AgentRuntimeDeployment.RUNTIME_DOCKER:
            from .docker_lifecycle import request_stop
            runtime = request_stop(runtime.id)
        job = create_job(
            request=request,
            job_type="agents.runtime.stop",
            resource_type="agent_runtime_deployment",
            resource_id=str(runtime.id),
            project=runtime.project,
            input_json={"agent_id": str(agent.id), "env": env,
                        "docker_generation": (runtime.docker_lifecycle or {}).get("generation", "")},
        )
    async_result = dispatch_runtime_job(stop_runtime_job, job=job, runtime=runtime)
    raise_eager_task_error(async_result)
    task_id = getattr(async_result, "id", "") or ""
    if task_id:
        set_celery_task_id(job_id=job.id, celery_task_id=task_id)
    runtime.refresh_from_db()
    return runtime, job


def dispatch_runtime_job(task, *, job, runtime):
    from kombu.exceptions import OperationalError
    from apps.jobs.services import add_job_event
    try:
        return task.delay(str(job.id), str(runtime.id))
    except OperationalError:
        if runtime.runtime_kind != AgentRuntimeDeployment.RUNTIME_DOCKER:
            raise
        # Desired state and Job committed before dispatch. The independent
        # reconciler can finish this operation without Redis/Celery availability.
        add_job_event(job=job, event_type="dispatch_deferred",
                      message="Queue unavailable; the saved operation is awaiting Docker reconciliation.")
        return None


def perform_runtime_stop(*, runtime_id, actor=None, request=None, expected_generation=None) -> AgentRuntimeDeployment:
    if AgentRuntimeDeployment.objects.filter(pk=runtime_id, runtime_kind="docker").exists():
        from .docker_lifecycle import stop
        return stop(runtime_id=runtime_id, actor=actor, request=request, expected_generation=expected_generation)
    return _perform_edge_runtime_stop(runtime_id=runtime_id, actor=actor, request=request)


@transaction.atomic
def _perform_edge_runtime_stop(*, runtime_id, actor=None, request=None):
    runtime = (
        AgentRuntimeDeployment.objects.select_for_update(of=("self",))
        .select_related("tenant", "agent", "image", "project", "agent_deployment", "edge_registration")
        .get(id=runtime_id)
    )
    agent = runtime.agent
    before = runtime_snapshot(runtime)
    get_runtime_runner(runtime).stop(deployment=runtime)
    runtime.status = AgentRuntimeDeployment.STATUS_STOPPED
    runtime.health_status = AgentRuntimeDeployment.HEALTH_UNKNOWN
    runtime.save(update_fields=["status", "health_status", "updated_at"])
    if runtime.runtime_kind in {
        AgentRuntimeDeployment.RUNTIME_OPENWRT_IPV6,
        AgentRuntimeDeployment.RUNTIME_OPENWRT_RELAY,
    } and runtime.edge_registration_id:
        registration = runtime.edge_registration
        registration.agent = None
        registration.health_status = registration.HEALTH_UNKNOWN
        registration.save(update_fields=["agent", "health_status", "updated_at"])
    if runtime.agent_deployment_id:
        AgentDeployment.objects.filter(id=runtime.agent_deployment_id).update(status=AgentDeployment.STATUS_STOPPED)
    AgentLog.objects.create(agent=agent, deployment=runtime.agent_deployment, message=f"Runtime deployment {runtime.env} stopped.")
    write_runtime_audit(
        request=request,
        actor=actor,
        runtime=runtime,
        action="agents.runtime.stop",
        before=before,
    )
    return runtime


def runtime_status(*, request, agent_id: str) -> dict[str, Any]:
    agent = get_agent(request=request, agent_id=agent_id)
    return {
        "images": (
            AgentRuntimeImage.objects.filter(tenant=agent.tenant, agent=agent)
            .exclude(status=SoftDeleteModel.STATUS_DELETED)
            .select_related("version")
            .order_by("-created_at")
        ),
        "deployments": (
            AgentRuntimeDeployment.objects.filter(tenant=agent.tenant, agent=agent)
            .exclude(status=SoftDeleteModel.STATUS_DELETED)
            .select_related("image", "edge_registration", "edge_registration__node")
            .order_by("env")
        ),
    }


def runtime_health_check(*, request, agent_id: str, env: str = "prod") -> AgentRuntimeDeployment:
    agent = get_mutable_runtime_agent(request=request, agent_id=agent_id)
    runtime = get_runtime_deployment(agent=agent, env=env, include_inactive=True)
    return perform_runtime_health_check(runtime_id=runtime.id, actor=request.user, request=request)


def list_runtime_workspace_files(*, runtime_id: str, token: str, path: str = "") -> dict[str, Any]:
    runtime = get_runtime_workspace(runtime_id=runtime_id, token=token)
    from apps.workspaces.execution import list_workspace_files

    return list_workspace_files(connection=runtime.workspace_connection, root=runtime.workspace_root or ".", path=path)


def read_runtime_workspace_file(*, runtime_id: str, token: str, path: str) -> dict[str, Any]:
    runtime = get_runtime_workspace(runtime_id=runtime_id, token=token)
    from apps.workspaces.execution import read_workspace_file

    return read_workspace_file(connection=runtime.workspace_connection, root=runtime.workspace_root or ".", path=path)


def write_runtime_workspace_file(*, runtime_id: str, token: str, path: str, content: str) -> dict[str, Any]:
    runtime = get_runtime_workspace(runtime_id=runtime_id, token=token)
    if runtime.workspace_access_mode != AgentRuntimeDeployment.WORKSPACE_READ_WRITE:
        raise exceptions.PermissionDenied("Workspace attachment is read-only.")
    from apps.workspaces.execution import write_workspace_file

    return write_workspace_file(connection=runtime.workspace_connection, root=runtime.workspace_root or ".", path=path, content=content)


def run_runtime_workspace_command(*, runtime_id: str, token: str, command: str, cwd: str = ".", timeout_seconds: int | None = None) -> dict[str, Any]:
    runtime = get_runtime_workspace(runtime_id=runtime_id, token=token)
    if runtime.workspace_access_mode != AgentRuntimeDeployment.WORKSPACE_READ_WRITE:
        raise exceptions.PermissionDenied("Workspace attachment is read-only.")
    from apps.workspaces.execution import run_workspace_command

    return run_workspace_command(
        connection=runtime.workspace_connection,
        root=runtime.workspace_root or ".",
        cwd=cwd or ".",
        command=command,
        timeout_seconds=timeout_seconds,
    )


def get_runtime_workspace(*, runtime_id: str, token: str) -> AgentRuntimeDeployment:
    runtime = (
        AgentRuntimeDeployment.objects.select_related("workspace_connection")
        .filter(id=runtime_id, workspace_connection__isnull=False)
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .first()
    )
    if runtime is None or runtime.workspace_connection is None:
        raise AgentRuntimeNotFound("Workspace attachment not found.")
    if not token or not secrets.compare_digest(token, runtime.workspace_token):
        raise exceptions.PermissionDenied("Workspace token is invalid.")
    return runtime


def enqueue_runtime_health_check(*, request, agent_id: str, env: str = "prod") -> tuple[AgentRuntimeDeployment, Any]:
    from apps.jobs.services import create_job, set_celery_task_id

    from .tasks import runtime_health_check_job

    with transaction.atomic():
        agent = lock_runtime_operation(request=request, agent_id=agent_id, env=env)
        runtime = get_runtime_deployment(agent=agent, env=env, include_inactive=True)
        job = create_job(
            request=request,
            job_type="agents.runtime.health_check",
            resource_type="agent_runtime_deployment",
            resource_id=str(runtime.id),
            project=runtime.project,
            input_json={"agent_id": str(agent.id), "env": env},
        )
    async_result = runtime_health_check_job.delay(str(job.id), str(runtime.id))
    raise_eager_task_error(async_result)
    task_id = getattr(async_result, "id", "") or ""
    if task_id:
        set_celery_task_id(job_id=job.id, celery_task_id=task_id)
    runtime.refresh_from_db()
    return runtime, job


@transaction.atomic
def perform_runtime_health_check(*, runtime_id, actor=None, request=None) -> AgentRuntimeDeployment:
    runtime = (
        AgentRuntimeDeployment.objects.select_for_update(of=("self",))
        .select_related("tenant", "agent", "image", "project", "agent_deployment", "edge_registration", "edge_registration__node")
        .get(id=runtime_id)
    )
    before_health = runtime.health_status
    before = runtime_snapshot(runtime)
    healthy = get_runtime_runner(runtime).health_check(deployment=runtime)
    runtime.health_status = AgentRuntimeDeployment.HEALTH_HEALTHY if healthy else AgentRuntimeDeployment.HEALTH_UNHEALTHY
    runtime.save(update_fields=["health_status", "updated_at"])
    if runtime.edge_registration_id:
        registration = runtime.edge_registration
        registration.health_status = runtime.health_status
        registration.last_probe_at = timezone.now()
        registration.save(update_fields=["health_status", "last_probe_at", "updated_at"])
    if before_health != runtime.health_status:
        write_runtime_audit(
            request=request,
            actor=actor,
            runtime=runtime,
            action=f"agents.runtime.health.{runtime.health_status}",
            before=before,
        )
    return runtime


def export_runtime_mcp(*, request, agent_id: str) -> dict[str, Any]:
    return export_mcp(request=request, agent_id=agent_id)


@transaction.atomic
def maybe_handle_mcp_task(
    *, request, agent_id: str, method: str, body: bytes, headers: dict[str, str]
) -> RuntimeMCPResult | None:
    if method.upper() != "POST" or not body:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    elicitation_response = maybe_handle_mcp_elicitation_response(
        request=request,
        agent_id=agent_id,
        payload=payload,
        headers=headers,
    )
    if elicitation_response is not None:
        return elicitation_response
    rpc_method = str(payload.get("method") or "")
    if rpc_method in {"tasks/get", "tasks/update", "tasks/cancel"}:
        return handle_mcp_task_request(
            request=request,
            agent_id=agent_id,
            payload=payload,
            headers=headers,
        )
    if rpc_method != "tools/call" or not _mcp_task_capability(payload, headers=headers):
        return None
    tenant = get_tenant_from_request(request)
    agent = get_runtime_use_agent(request=request, tenant=tenant, agent_id=agent_id)
    tool_name = tool_name_from_mcp_payload(body)
    runtime = get_runtime_deployment(agent=agent, env="prod")
    policy = runtime_tool_policy(agent=agent, runtime=runtime, tool_name=tool_name)
    if not isinstance(policy, dict) or not policy.get("task"):
        return None
    enforce_api_key_agent_policy(api_key=getattr(request, "api_key", None), agent=agent)
    ensure_openwrt_interactive_transport(runtime=runtime, policy=policy)
    invocation_preflight(tenant=tenant, agent=agent)
    from .file_transfers import create_mcp_file_context
    display_run, display_context, body = create_mcp_file_context(
        runtime=runtime,
        tool_name=tool_name,
        request=request,
        body=body,
    )
    display_run.interaction_mode = "task"
    display_run.save(update_fields=["interaction_mode", "updated_at"])
    display_context = replace(display_context, interaction_mode="task")
    subject = request_subject(request)
    contract = next(
        (item for item in effective_mcp_tools(agent=agent, runtime=runtime) if item["name"] == tool_name),
        None,
    )
    task = AgentExecutionTask.objects.create(
        run=display_run,
        agent=agent,
        runtime=runtime,
        caller_subject_hash=subject.subject_hash,
        mcp_session_hash=hash_token(_header_value(headers, "Mcp-Session-Id")) if _header_value(headers, "Mcp-Session-Id") else "",
        tool_name=tool_name,
        request_json={
            "jsonrpc_id": payload.get("id"),
            "tool_name": tool_name,
            "has_arguments": bool((payload.get("params") or {}).get("arguments")),
            "agent_version": agent.current_version,
            "tool_contract_digest": tool_contract_digest(contract),
        },
        continuable=bool(policy.get("continuable")),
        recovery_protocol=runtime_recovery_protocol(runtime=runtime, declared=int(policy.get("recovery_protocol") or 0)),
        expires_at=timezone.now() + timedelta(hours=1),
    )
    display_context = replace(
        display_context,
        recovery_managed=task.recovery_protocol >= 1,
        recovery_attempt=0,
    )
    enqueue_agent_task(task=task, request=request, body=bytes(body), headers=headers,
        display_pair=(display_run, display_context))
    response_headers: dict[str, str] = {}
    add_private_display_response_headers(
        response_headers=response_headers,
        run=display_run,
        context=display_context,
    )
    return _task_rpc_response(
        request_id=payload.get("id"),
        result={"resultType": "task", **serialize_execution_task(task)},
        headers=response_headers,
    )


def _elicitation_cache_key(request_id: str) -> str:
    return "agent-mcp-elicitation:" + hash_token(request_id)


@transaction.atomic
def maybe_handle_mcp_elicitation_response(
    *, request, agent_id: str, payload: dict[str, Any], headers: dict[str, str]
) -> RuntimeMCPResult | None:
    request_id = str(payload.get("id") or "")
    if not request_id.startswith("nexus-elicit-") or "method" in payload:
        return None
    binding = cache.get(_elicitation_cache_key(request_id))
    if not isinstance(binding, dict):
        return RuntimeMCPResult(status_code=202, headers={}, body=b"")
    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    session_id = _header_value(headers, "Mcp-Session-Id")
    valid = bool(
        str(binding.get("agent_id") or "") == str(agent_id)
        and str(binding.get("tenant_id") or "") == str(tenant.id)
        and str(binding.get("project_id") or "")
            == str(getattr(request, "project_id", None) or "")
        and str(binding.get("caller_subject_hash") or "") == subject.subject_hash
        and session_id
        and str(binding.get("mcp_session_hash") or "") == hash_token(session_id)
    )
    if not valid:
        return RuntimeMCPResult(status_code=202, headers={}, body=b"")
    interaction = AgentRunInteraction.objects.select_for_update().filter(
        id=str(binding.get("interaction_id") or ""),
        run_id=str(binding.get("run_id") or ""),
        status=AgentRunInteraction.STATUS_PENDING,
    ).select_related("run").first()
    if interaction is None or interaction.expires_at <= timezone.now():
        return RuntimeMCPResult(status_code=202, headers={}, body=b"")
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    action = str(result.get("action") or "accept")
    if action != "accept":
        interaction.status = AgentRunInteraction.STATUS_CANCELLED
        interaction.save(update_fields=["status", "updated_at"])
        cache.delete(_elicitation_cache_key(request_id))
        return RuntimeMCPResult(status_code=202, headers={}, body=b"")
    content = result.get("content") if isinstance(result.get("content"), dict) else {}
    value = str(content.get("input") or content.get("value") or "").strip()
    allowed = {
        str(item.get("value") or "")
        for item in interaction.choices_json or []
        if isinstance(item, dict)
    }
    if not value or len(value) > 4000 or (allowed and value not in allowed):
        return RuntimeMCPResult(status_code=202, headers={}, body=b"")
    interaction.response_json = {"value": value, "text": value}
    interaction.status = AgentRunInteraction.STATUS_ANSWERED
    interaction.answered_at = timezone.now()
    interaction.save(update_fields=["response_json", "status", "answered_at", "updated_at"])
    message_id = "user-" + uuid.uuid4().hex
    append_display_event(
        run=interaction.run,
        event_type="TEXT_MESSAGE_START",
        payload={"messageId": message_id, "role": "user"},
    )
    append_display_event(
        run=interaction.run,
        event_type="TEXT_MESSAGE_CONTENT",
        payload={"messageId": message_id, "delta": value},
    )
    append_display_event(
        run=interaction.run,
        event_type="TEXT_MESSAGE_END",
        payload={"messageId": message_id},
    )
    cache.delete(_elicitation_cache_key(request_id))
    return RuntimeMCPResult(status_code=202, headers={}, body=b"")


def start_public_agent_demo(*, request, agent_id: str, tool_name: str = "", arguments: dict[str, Any] | None = None) -> AgentDisplayRun:
    return invoke_agent_policy("start_public_agent_demo", request=request, agent_id=agent_id, tool_name=tool_name, arguments=arguments)


def _execute_public_demo(*, runtime_id: str, run_id: str, context: RuntimeDisplayContext, body: bytes) -> None:
    return invoke_agent_policy("_execute_public_demo", runtime_id=runtime_id, run_id=run_id, context=context, body=body)

@transaction.atomic
def start_agent_invocation(
    *,
    request,
    agent_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    initial_content: str = "",
    force_task: bool = False,
    prepared_images: list | None = None,
    prepared_files: list | None = None,
    execution_profile: dict[str, Any] | None = None,
    reasoning_effort: str = "",
) -> AgentDisplayRun:
    tenant = get_tenant_from_request(request)
    agent = get_runtime_use_agent(request=request, tenant=tenant, agent_id=agent_id)
    runtime = get_runtime_deployment(agent=agent, env="prod")
    descriptor = next(
        (item for item in effective_mcp_tools(agent=agent, runtime=runtime) if item["name"] == tool_name),
        None,
    )
    if descriptor is None:
        raise AgentRuntimeNotFound("Agent tool not found.")
    policy = {
        field: bool(descriptor.get(field))
        for field in ("task", "continuable", "demo", "chat", "interactive")
    }
    ensure_openwrt_interactive_transport(runtime=runtime, policy=policy)
    api_key = getattr(request, "api_key", None)
    enforce_api_key_agent_policy(api_key=api_key, agent=agent)
    run, context = create_invocation_display_context(
        runtime=runtime,
        tool_name=tool_name,
        request=request,
        execution_profile=execution_profile,
        reasoning_effort=reasoning_effort,
    )
    initial_text = str(initial_content or "").strip()
    from .image_services import bind_attachments
    image_blocks = bind_attachments(run=run, prepared=prepared_images or [])
    from .file_transfers import bind_files, file_arguments
    bound_file_arguments = file_arguments(prepared_files or [])
    file_blocks = bind_files(run=run, rows=prepared_files or [], turn_index=1)
    context = replace(context, input_files=tuple(bound_file_arguments))
    if initial_text or image_blocks or file_blocks:
        AgentRunMessage.objects.create(
            run=run,
            sequence=1,
            turn_index=1,
            role=AgentRunMessage.ROLE_USER,
            content=initial_text,
            content_blocks=[{"type": "markdown", "text": initial_text}, *image_blocks, *file_blocks],
        )
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "display-" + uuid.uuid4().hex,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": dict(arguments or {})},
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
    }
    if policy["task"] or force_task:
        run.interaction_mode = "task"
        run.save(update_fields=["interaction_mode", "updated_at"])
        context = replace(context, interaction_mode="task")
        subject = request_subject(request)
        task = AgentExecutionTask.objects.create(
            run=run,
            agent=agent,
            runtime=runtime,
            caller_subject_hash=subject.subject_hash,
            tool_name=tool_name,
            request_json={
                "tool_name": tool_name,
                "agent_version": agent.current_version,
                "tool_contract_digest": tool_contract_digest(descriptor),
                "turn_index": 1,
                "execution_profile_id": str((execution_profile or {}).get("id") or ""),
                "execution_model": str((execution_profile or {}).get("model") or ""),
                "reasoning_effort": reasoning_effort,
            },
            continuable=bool(policy["continuable"]),
            recovery_protocol=runtime_recovery_protocol(runtime=runtime, declared=int(descriptor.get("recovery_protocol") or 0)),
            expires_at=timezone.now() + timedelta(hours=1),
        )
    else:
        task = AgentExecutionTask.objects.create(
            run=run, agent=agent, runtime=runtime,
            caller_subject_hash=request_subject(request).subject_hash, tool_name=tool_name,
            request_json={
                "tool_name": tool_name,
                "agent_version": agent.current_version,
                "turn_index": 1,
                "execution_profile_id": str((execution_profile or {}).get("id") or ""),
                "execution_model": str((execution_profile or {}).get("model") or ""),
                "reasoning_effort": reasoning_effort,
            },
            expires_at=timezone.now() + timedelta(hours=1),
        )
    context = replace(
        context,
        recovery_managed=task.recovery_protocol >= 1,
        recovery_attempt=0,
    )
    enqueue_agent_task(task=task, request=request, body=body, headers=headers, display_pair=(run, context))
    return run


def get_agent_interactor(*, request, agent_id: str) -> dict[str, Any]:
    """Return the signed-in caller catalog used to render Private Display before a Run exists."""

    tenant = get_tenant_from_request(request)
    agent = get_runtime_use_agent(request=request, tenant=tenant, agent_id=agent_id)
    project_context = project_context_state(request=request, agent=agent)
    runtime = (
        AgentRuntimeDeployment.objects.filter(agent=agent, env="prod")
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .select_related("edge_registration", "edge_registration__node")
        .order_by("-updated_at")
        .first()
    )
    tools = interaction_tools(agent=agent, runtime=runtime)
    chat_tools = [
        item for item in tools
        if bool(item.get("policy", {}).get("chat"))
    ]
    default_tool = chat_tools[0] if len(chat_tools) == 1 else None
    default_tool_available = bool(
        default_tool is not None
        and default_tool.get("availability", {}).get("can_invoke")
    )
    from .context_extension import interactor_presentation_fields
    declared_scopes = normalize_workspace_capabilities(
        agent.workspace_capabilities or []
    )
    grant = get_workspace_grant(request=request, agent=agent)
    granted_set = set(
        normalize_workspace_capabilities(grant.scopes or [])
        if grant is not None and grant.status == SoftDeleteModel.STATUS_ACTIVE
        else []
    )
    granted_scopes = [
        scope for scope in WORKSPACE_CAPABILITIES if scope in granted_set
    ]
    missing_scopes = [
        scope for scope in declared_scopes if scope not in granted_set
    ]
    subject = request_subject(request)
    computer_binding = AgentComputerBinding.objects.filter(
        tenant=tenant,
        agent=agent,
        caller_subject_hash=subject.subject_hash,
        status=SoftDeleteModel.STATUS_ACTIVE,
        connection__owner_subject_hash=subject.subject_hash,
        connection__connection_type="runtime",
        connection__status=SoftDeleteModel.STATUS_ACTIVE,
    ).select_related("connection", "connection__runtime_device").filter(is_default=True).first()
    attached = computer_binding is not None
    computer_code = ""
    computer_message = ""
    computer_ready = True
    if agent.computer_requirement == Agent.COMPUTER_REQUIRED and not attached:
        computer_ready = False
        computer_code = "COMPUTER_REQUIRED"
        computer_message = "Connect a Computer before starting this Agent."
    elif missing_scopes:
        computer_ready = False
        computer_code = "WORKSPACE_PERMISSION_REQUIRED"
        computer_message = (
            "Grant the missing Workspace permissions before starting this Agent."
        )
    browser_requested = "browser.control" in declared_scopes
    computer_platform = ""
    browser_available = None
    browser_name = ""
    if attached:
        device = computer_binding.connection.runtime_device
        facts = device.facts if isinstance(device.facts, dict) else {}
        computer_platform = device.platform
        browser_available = facts.get("browser_available")
        browser_name = str(facts.get("browser_name") or "")
        if device.revoked_at is not None:
            computer_ready = False
            computer_code = "COMPUTER_RUNTIME_REVOKED"
            computer_message = "This Computer Runtime was revoked. Pair it again from the Computer page."
        elif not device.online:
            computer_ready = False
            computer_code = "COMPUTER_RUNTIME_OFFLINE"
            computer_message = "Start Nexus Computer Runtime on the attached Computer. The binding will recover automatically."
    if browser_requested and attached and not computer_code:
        if computer_platform not in {"windows", "linux", "macos"}:
            computer_ready = False
            computer_code = "BROWSER_UNAVAILABLE"
            computer_message = (
                "Attached browser supports Windows, Linux, and macOS Computer Runtime devices."
            )
        elif computer_platform == "linux" and facts.get("user_is_root") is True:
            computer_ready = False
            computer_code = "BROWSER_UNAVAILABLE"
            computer_message = (
                "Linux browser sessions require a non-root Computer Runtime user."
            )
        elif browser_available is False:
            computer_ready = False
            computer_code = "BROWSER_UNAVAILABLE"
            if computer_platform == "linux":
                computer_message = (
                    "Install Google Chrome, Chromium, or Microsoft Edge on the Linux Computer."
                )
            elif computer_platform == "macos":
                computer_message = (
                    "Install Google Chrome, Chromium, or Microsoft Edge on the macOS Computer. Safari is not supported."
                )
            else:
                computer_message = (
                    "Install Google Chrome or Microsoft Edge on the Windows Computer."
                )

    mobile_requirement, declared_mobile_scopes = (
        runtime_mobile_declaration(runtime)
        if runtime is not None
        else published_mobile_declaration(agent)
    )
    mobile_grant = get_mobile_grant(request=request, agent=agent)
    mobile_granted_set = set(
        mobile_grant.scopes or []
        if mobile_grant is not None
        and mobile_grant.status == SoftDeleteModel.STATUS_ACTIVE
        else []
    )
    mobile_granted_scopes = [
        scope for scope in declared_mobile_scopes if scope in mobile_granted_set
    ]
    mobile_missing_scopes = [
        scope for scope in declared_mobile_scopes if scope not in mobile_granted_set
    ]
    mobile_binding = (
        list_mobile_bindings(request=request, agent_id=str(agent.id))
        .filter(is_default=True, status=SoftDeleteModel.STATUS_ACTIVE)
        .first()
    )
    mobile_attached = mobile_binding is not None
    mobile_device_status = (
        mobile_binding.device.lifecycle_status if mobile_binding is not None else ""
    )
    mobile_ready = True
    mobile_code = ""
    mobile_message = ""
    if (
        runtime is not None
        and runtime.runtime_kind == AgentRuntimeDeployment.RUNTIME_OPENWRT_RELAY
        and mobile_requirement == Agent.MOBILE_REQUIRED
    ):
        mobile_ready = False
        mobile_code = "OPENWRT_IPV6_REQUIRED"
        mobile_message = "This Agent requires Direct IPv6 for Caller Mobile access."
    elif mobile_requirement == Agent.MOBILE_REQUIRED and not mobile_attached:
        mobile_ready = False
        mobile_code = "MOBILE_REQUIRED"
        mobile_message = "Attach a Mobile before starting this Agent."
    elif mobile_requirement == Agent.MOBILE_REQUIRED and mobile_missing_scopes:
        mobile_ready = False
        mobile_code = "MOBILE_PERMISSION_REQUIRED"
        mobile_message = (
            "Grant the requested Mobile permissions before starting this Agent."
        )
    elif (
        mobile_requirement == Agent.MOBILE_REQUIRED
        and mobile_device_status != MobileDevice.LIFECYCLE_ONLINE
    ):
        mobile_ready = False
        mobile_code = "MOBILE_UNAVAILABLE"
        mobile_message = "The attached Mobile is offline or unavailable."
    return {
        "agent": {
            "id": str(agent.id),
            "name": agent.name,
            "status": agent.status,
            "visibility": agent.visibility,
            "publication_status": agent.publication_status,
        },
        "runtime": {
            "id": str(runtime.id),
            "status": runtime.effective_status(),
            "kind": runtime.runtime_kind,
            "health_status": runtime.health_status,
        }
        if runtime
        else None,
        **interactor_presentation_fields(agent=agent),
        "tools": tools,
        "default_tool_name": default_tool["name"] if default_tool else "",
        "private_display": {
            "available": default_tool is not None,
            "tool_name": default_tool["name"] if default_tool else "",
            "code": (
                str(default_tool["availability"].get("code") or "")
                if default_tool is not None and not default_tool_available
                else "" if default_tool is not None
                else "PRIVATE_DISPLAY_CHAT_AMBIGUOUS" if len(chat_tools) > 1
                else "PRIVATE_DISPLAY_CHAT_UNSUPPORTED"
            ),
            "message": (
                str(default_tool["availability"].get("message") or "")
                if default_tool is not None and not default_tool_available
                else "" if default_tool is not None
                else "This Agent publishes more than one Chat tool." if len(chat_tools) > 1
                else "This Agent does not publish a Chat tool for Private Display."
            ),
        },
        "computer": {
            "requirement": agent.computer_requirement,
            "declared_scopes": declared_scopes,
            "granted_scopes": granted_scopes,
            "missing_scopes": missing_scopes,
            "attached": attached,
            "online": bool(attached and computer_binding.connection.runtime_device.online),
            "device_name": computer_binding.connection.name if computer_binding else "",
            "platform": computer_platform,
            "browser_available": browser_available,
            "browser_name": browser_name,
            "ready": computer_ready,
            "code": computer_code,
            "message": computer_message,
        },
        "mobile": {
            "requirement": mobile_requirement,
            "declared_scopes": declared_mobile_scopes,
            "granted_scopes": mobile_granted_scopes,
            "missing_scopes": mobile_missing_scopes,
            "attached": mobile_attached,
            "binding_id": str(mobile_binding.id) if mobile_binding is not None else "",
            "device_name": mobile_binding.device.name if mobile_binding is not None else "",
            "device_status": mobile_device_status,
            "ready": mobile_ready,
            "code": mobile_code,
            "message": mobile_message,
        },
        "project_context": project_context,
    }


def _private_run_title(run: AgentDisplayRun) -> str:
    if run.display_title:
        return run.display_title
    tool_name = getattr(getattr(run, "execution_task", None), "tool_name", "") or "Agent run"
    return f"{tool_name} · {run.started_at.astimezone().strftime('%b %d, %H:%M')}"


def serialize_private_run(run: AgentDisplayRun, *, include_messages: bool = True) -> dict[str, Any]:
    from .run_history import attention_state, completion_unread
    task = getattr(run, "execution_task", None)
    if hasattr(run, "_history_preview"):
        preview = run._history_preview or ""
    else:
        latest = run.messages.order_by("-sequence").first()
        preview = latest.content if latest else ""
    value: dict[str, Any] = {
        "id": str(run.id),
        "agent_id": str(run.agent_id),
        "title": _private_run_title(run),
        "title_source": run.display_title_source,
        "tool_name": task.tool_name if task else "",
        "turn_index": max(int((task.request_json or {}).get("turn_index") or 1), 1) if task else 1,
        "status": run.status,
        "preview": str(preview)[:160],
        "attention_state": attention_state(run),
        "completion_unread": completion_unread(run),
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "updated_at": run.updated_at.isoformat(),
        "display_url": f"/agents/{run.agent_id}/private-display?run={run.id}",
    }
    if include_messages:
        value["messages"] = [
            {
                "id": str(message.id),
                "sequence": message.sequence,
                "turn_index": message.turn_index,
                "role": message.role,
                "content": message.content,
                "content_blocks": message.content_blocks or [{"type": "markdown", "text": message.content}],
                "created_at": message.created_at.isoformat(),
            }
            for message in run.messages.order_by("sequence")
        ]
        latest_usage = run.model_usage.filter(primary=True).order_by("-turn_index", "-created_at").first()
        totals = run.model_usage.aggregate(
            input_tokens=Sum("input_tokens"),
            output_tokens=Sum("output_tokens"),
            cached_input_tokens=Sum("cached_input_tokens"),
            reasoning_tokens=Sum("reasoning_tokens"),
        )
        value["usage"] = {
            "reported": latest_usage is not None,
            "current": (
                {
                    "turn_index": latest_usage.turn_index,
                    "profile_id": latest_usage.profile_id,
                    "model": latest_usage.model,
                    "input_tokens": latest_usage.input_tokens,
                    "output_tokens": latest_usage.output_tokens,
                    "cached_input_tokens": latest_usage.cached_input_tokens,
                    "reasoning_tokens": latest_usage.reasoning_tokens,
                    "context_window": latest_usage.context_window,
                    "source": latest_usage.source,
                }
                if latest_usage else None
            ),
            "totals": {key: int(value or 0) for key, value in totals.items()},
        }
        latest_invocation = run.runtime_invocations.order_by("-turn_index", "-created_at").first()
        value["execution"] = {
            "profile_id": latest_invocation.execution_profile_id if latest_invocation else "",
            "model": latest_invocation.execution_model if latest_invocation else "",
            "reasoning_effort": latest_invocation.reasoning_effort if latest_invocation else "",
        }
        snapshot = dict(run.project_context_snapshot or {})
        value["project_context"] = {
            "available": bool(snapshot),
            "project_id": str(snapshot.get("project_id") or ""),
            "project_name": str(snapshot.get("project_name") or ""),
            "instructions_markdown": str(snapshot.get("instructions_markdown") or ""),
            "revision": int(snapshot.get("instructions_revision") or 0),
            "captured_at": snapshot.get("captured_at"),
        }
    return value


def list_private_runs(
    *, request, agent_id: str, query: str = "", limit: int = 30, cursor: str = "",
    attention: str = "all", counts: dict | None = None,
) -> tuple[list[AgentDisplayRun], str]:
    tenant = get_tenant_from_request(request)
    agent = get_runtime_use_agent(request=request, tenant=tenant, agent_id=agent_id)
    subject = request_subject(request)
    size = max(1, min(int(limit or 30), 100))
    queryset = (
        AgentDisplayRun.objects.filter(
            agent=agent,
            consumer_tenant=tenant,
            consumer_project_id=getattr(request, "project_id", None) or None,
            caller_subject_hash=subject.subject_hash,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            caller_hidden_at__isnull=True,
        )
        .select_related("agent", "execution_task")
        .order_by("-created_at", "-id")
    )
    anchor_queryset = queryset
    search = str(query or "").strip()
    if search:
        queryset = queryset.filter(
            Q(display_title__icontains=search) | Q(messages__content__icontains=search)
        ).distinct()
    from .run_history import history_queryset
    queryset = history_queryset(queryset, attention, counts)
    if cursor:
        anchor = anchor_queryset.filter(id=cursor).first()
        if anchor is None:
            raise AgentRuntimeNotFound("Agent Run not found.")
        queryset = queryset.filter(
            Q(created_at__lt=anchor.created_at)
            | Q(created_at=anchor.created_at, id__lt=anchor.id)
        )
    runs = list(queryset[: size + 1])
    next_cursor = str(runs[size - 1].id) if len(runs) > size else ""
    return runs[:size], next_cursor


def _private_run_schema_error(error: JsonSchemaValidationError) -> str:
    location = ".".join(str(part) for part in error.absolute_path)
    field = f' field "{location}"' if location else ""
    if error.validator == "required":
        required = error.validator_value if isinstance(error.validator_value, list) else []
        missing = next(
            (str(name) for name in required if isinstance(error.instance, dict) and name not in error.instance),
            "required value",
        )
        return f'Arguments are missing required field "{missing}".'
    if error.validator == "type":
        return f"Arguments{field} have the wrong type."
    if error.validator == "format":
        return f"Arguments{field} must use the {error.validator_value} format."
    if error.validator == "enum":
        return f"Arguments{field} must use one of the allowed values."
    return f"Arguments do not match the tool input schema{field}."


def resolve_execution_profile(
    *,
    descriptor: dict[str, Any],
    profile_id: str = "",
    reasoning_effort: str = "",
) -> tuple[dict[str, Any] | None, str]:
    """Resolve only publisher-declared execution choices from signed tool policy."""

    profiles = descriptor.get("execution_profiles") or []
    requested_profile = str(profile_id or "").strip()
    requested_effort = str(reasoning_effort or "").strip().lower()
    if not profiles:
        if requested_profile or requested_effort:
            raise exceptions.ValidationError(
                {"execution_profile_id": "This Agent tool uses its fixed default execution profile."}
            )
        return None, ""
    default_profile = next(
        (item for item in profiles if item.get("is_default") is True),
        profiles[0],
    )
    selected = next(
        (item for item in profiles if str(item.get("id") or "") == requested_profile),
        None,
    ) if requested_profile else default_profile
    if selected is None:
        raise exceptions.ValidationError(
            {"execution_profile_id": "The selected execution profile is not available for this tool."}
        )
    efforts = [str(value).lower() for value in selected.get("reasoning_efforts") or []]
    if not efforts:
        if requested_effort:
            raise exceptions.ValidationError(
                {"reasoning_effort": "This execution profile lets the model manage reasoning."}
            )
        return selected, ""
    selected_effort = (
        requested_effort
        or str(selected.get("default_reasoning_effort") or "").lower()
        or efforts[0]
    )
    if selected_effort not in efforts:
        raise exceptions.ValidationError(
            {"reasoning_effort": "The selected reasoning effort is not available for this execution profile."}
        )
    return selected, selected_effort


def _private_run_arguments(
    *,
    descriptor: dict[str, Any],
    content: str,
    arguments: dict[str, Any] | None,
    image_arguments: list | None = None,
    file_arguments: list | None = None,
    audio_arguments: list | None = None,
) -> tuple[dict[str, Any], str]:
    text = str(content or "").strip()
    if len(text) > 8000:
        raise exceptions.ValidationError(
            {"content": "Run input must be at most 8000 characters."}
        )
    if arguments is None:
        if not text and not image_arguments and not file_arguments and not audio_arguments:
            raise exceptions.ValidationError(
                {"content": "Run input must be 1..8000 characters."}
            )
        properties = (descriptor.get("input_schema") or {}).get("properties") or {}
        text_field = "content" if "content" in properties and "message" not in properties else "message"
        candidate = {}
        if text or text_field in ((descriptor.get("input_schema") or {}).get("required") or []):
            # Empty text is included only when the contract requires that field;
            # minLength and other schema constraints remain authoritative.
            candidate[text_field] = text
    else:
        candidate = dict(arguments)
    if image_arguments:
        if "attachments" in candidate:
            raise exceptions.ValidationError("Submit image attachments separately from arguments.")
        candidate["attachments"] = image_arguments
    schema = descriptor.get("input_schema") or {}
    from .file_transfers import tool_file_arguments
    if file_arguments:
        if "files" in candidate:
            raise exceptions.ValidationError("Submit files separately from arguments.")
        candidate["files"] = tool_file_arguments(file_arguments, (schema.get("properties") or {}).get("files", {}))
    if audio_arguments:
        if "audio" in candidate:
            raise exceptions.ValidationError("Submit audio separately from arguments.")
        candidate["audio"] = tool_file_arguments(audio_arguments, (schema.get("properties") or {}).get("audio", {}))
    try:
        Draft202012Validator.check_schema(schema)
        validation_error = next(
            iter(
                Draft202012Validator(
                    schema,
                    format_checker=FormatChecker(),
                ).iter_errors(candidate)
            ),
            None,
        )
    except SchemaError as exc:
        raise AgentRuntimeOperationConflict(
            "This tool has an invalid input schema. Ask the Agent developer to publish a corrected manifest."
        ) from exc
    if validation_error is not None:
        if arguments is None:
            raise exceptions.ValidationError(
                {
                    "arguments": (
                        "This Agent cannot accept this message or attachment. "
                        + _private_run_schema_error(validation_error)
                    )
                }
            )
        raise exceptions.ValidationError(
            {"arguments": _private_run_schema_error(validation_error)}
        )
    return candidate, text


def start_private_run(
    *,
    request,
    agent_id: str,
    content: str,
    tool_name: str = "",
    arguments: dict[str, Any] | None = None,
    attachments=None,
    files=None,
    audio=None,
    execution_profile_id: str = "",
    reasoning_effort: str = "",
) -> AgentDisplayRun:
    tenant = get_tenant_from_request(request)
    agent = get_runtime_use_agent(request=request, tenant=tenant, agent_id=agent_id)
    runtime = get_runtime_deployment(agent=agent, env="prod")
    callable_tools = [
        item
        for item in interaction_tools(agent=agent, runtime=runtime)
        if item["availability"]["can_invoke"]
    ]
    available = [
        item for item in callable_tools
        if bool(item.get("policy", {}).get("chat"))
    ]
    selected = str(tool_name or "").strip()
    if not selected:
        if len(available) != 1:
            raise AgentRuntimeOperationConflict(
                "Private Display requires exactly one available Chat tool."
            )
        selected = str(available[0]["name"])
    descriptor = next(
        (
            item for item in callable_tools
            if item["name"] == selected
            and (
                bool(item.get("policy", {}).get("chat"))
                or bool(item.get("slash_command"))
            )
        ),
        None,
    )
    if descriptor is None:
        raise AgentRuntimeNotFound("Private Display Chat tool not found or unavailable.")
    from .image_services import prepare_attachments, attachment_arguments
    prepared = prepare_attachments(request=request, descriptor=descriptor, attachments=attachments)
    from .file_transfers import prepare_files, prepare_audio, file_arguments
    prepared_files = prepare_files(request=request, descriptor=descriptor, references=files, agent_id=agent.id)
    prepared_audio = prepare_audio(request=request, descriptor=descriptor, references=audio, agent_id=agent.id)
    all_files = [*prepared_files, *prepared_audio]
    if len(all_files) > 8:
        raise exceptions.ValidationError({"files": "Provide at most eight files and recordings in one Run."})
    execution_profile, selected_effort = resolve_execution_profile(
        descriptor=descriptor,
        profile_id=execution_profile_id,
        reasoning_effort=reasoning_effort,
    )
    invocation_arguments, initial_content = _private_run_arguments(
        descriptor=descriptor,
        content=content,
        arguments=arguments,
        image_arguments=attachment_arguments(prepared),
        file_arguments=file_arguments(prepared_files),
        audio_arguments=file_arguments(prepared_audio),
    )
    return start_agent_invocation(
        request=request,
        agent_id=str(agent.id),
        tool_name=selected,
        arguments=invocation_arguments,
        initial_content=initial_content,
        force_task=True,
        prepared_images=prepared,
        prepared_files=all_files,
        execution_profile=execution_profile,
        reasoning_effort=selected_effort,
    )


def _resume_invocation_display_context(
    *,
    run: AgentDisplayRun,
    request,
    tool_name: str,
    turn_index: int,
    workspace_ceiling: tuple[str, ...] | None = None,
    execution_profile: dict[str, Any] | None = None,
    reasoning_effort: str = "",
    input_files: list[dict[str, Any]] | None = None,
) -> RuntimeDisplayContext:
    """Rotate one completed Run's delegates without changing its identity."""

    public_base_url = request.build_absolute_uri("/").rstrip("/")
    events_base_url = str(
        getattr(settings, "NEXUS_AGENT_DISPLAY_EVENTS_BASE_URL", public_base_url)
    ).rstrip("/")
    workspace_capabilities = effective_workspace_capabilities(
        request=request,
        agent=run.agent,
    )
    if workspace_ceiling is not None:
        workspace_capabilities = [scope for scope in workspace_capabilities if scope in workspace_ceiling]
    if run.computer_binding_id is None and run.agent.computer_requirement == Agent.COMPUTER_REQUIRED:
        raise AgentComputerRequired()
    if run.computer_binding_id:
        from apps.workspaces.computer_runtime import ensure_runtime_connection
        ensure_runtime_connection(run.computer_binding.connection, operation="workspace.test")
        for scope in run.workspace_capabilities_snapshot or []:
            ensure_runtime_connection(run.computer_binding.connection,
                operation={"browser.control":"browser.open", "command.execute":"command.execute",
                    "tool.setup":"tool_setup.detect"}.get(scope, "workspace.test"))

    mobile_requirement, _current_mobile_capabilities = runtime_mobile_declaration(run.runtime)
    # A continued task must not gain permissions that were added after the Run
    # was created. Grant reductions are still applied dynamically below.
    declared_mobile_capabilities = normalize_mobile_capabilities(run.mobile_capabilities_snapshot)
    policy = runtime_tool_policy(agent=run.agent, runtime=run.runtime, tool_name=tool_name)
    tool_mobile_scopes = list(policy.get("mobile_scopes") or [])
    required_mobile_capabilities = (
        [value for value in declared_mobile_capabilities if value in set(tool_mobile_scopes)]
        if tool_mobile_scopes
        else declared_mobile_capabilities
    )
    mobile_capabilities = effective_declared_mobile_capabilities(
        request=request,
        agent=run.agent,
        declared_capabilities=declared_mobile_capabilities,
    )
    if tool_mobile_scopes:
        allowed = set(tool_mobile_scopes)
        mobile_capabilities = [item for item in mobile_capabilities if item in allowed]
    mobile_enabled = bool(
        run.mobile_binding_id
        and run.mobile_binding.status == SoftDeleteModel.STATUS_ACTIVE
        and run.mobile_binding.device.status == SoftDeleteModel.STATUS_ACTIVE
        and run.mobile_binding.device.lifecycle_status == MobileDevice.LIFECYCLE_ONLINE
        and mobile_capabilities
    )
    if mobile_requirement == Agent.MOBILE_REQUIRED:
        if run.mobile_binding_id is None:
            raise AgentMobileRequired()
        if not set(required_mobile_capabilities).issubset(set(mobile_capabilities)):
            raise AgentMobilePermissionRequired()
        if not mobile_enabled:
            raise AgentMobileUnavailable()

    write_token = secrets.token_urlsafe(32)
    interaction_token = secrets.token_urlsafe(32)
    workspace_delegate_token = (
        secrets.token_urlsafe(32)
        if run.computer_binding_id and workspace_capabilities
        else ""
    )
    browser_enabled = bool(
        run.computer_binding_id and "browser.control" in workspace_capabilities
    )
    browser_delegate_token = secrets.token_urlsafe(32) if browser_enabled else ""
    mobile_delegate_token, mobile_delegate_hash, mobile_delegate_expires_at = (
        issue_mobile_delegate_token(
            capabilities=mobile_capabilities if mobile_enabled else [],
        )
    )
    now = timezone.now()
    context_extension = issue_run_context_extension(
        agent=run.agent, tool_name=tool_name, now=now
    )
    context_token = secrets.token_urlsafe(32)
    for field_name, field_value in context_extension.model_values.items():
        setattr(run, field_name, field_value)
    run.write_token = write_token
    run.interaction_token_hash = hash_token(interaction_token)
    run.interaction_token_expires_at = now + timedelta(hours=1)
    run.context_token_hash = hash_token(context_token)
    run.context_token_expires_at = now + timedelta(hours=1)
    run.interaction_mode = "task"
    run.workspace_capabilities_snapshot = workspace_capabilities
    run.workspace_delegate_token_hash = (
        hash_token(workspace_delegate_token) if workspace_delegate_token else ""
    )
    run.workspace_delegate_token_expires_at = (
        now + timedelta(hours=1) if workspace_delegate_token else None
    )
    run.browser_delegate_token_hash = (
        hash_token(browser_delegate_token) if browser_delegate_token else ""
    )
    run.browser_delegate_token_expires_at = (
        now + timedelta(hours=1) if browser_delegate_token else None
    )
    run.mobile_delegate_token_hash = mobile_delegate_hash
    run.mobile_delegate_token_expires_at = mobile_delegate_expires_at
    run.status = AgentDisplayRun.STATUS_RUNNING
    run.completed_at = None
    run.save(
        update_fields=[
            "write_token",
            "interaction_token_hash",
            "interaction_token_expires_at",
            "context_token_hash",
            "context_token_expires_at",
            "interaction_mode",
            *context_extension.model_values,
            "workspace_capabilities_snapshot",
            "workspace_delegate_token_hash",
            "workspace_delegate_token_expires_at",
            "browser_delegate_token_hash",
            "browser_delegate_token_expires_at",
            "mobile_delegate_token_hash",
            "mobile_delegate_token_expires_at",
            "status",
            "completed_at",
            "updated_at",
        ]
    )
    internal_root = f"{events_base_url}/api/v1/internal/agent-runs/{run.id}"
    display_url = f"{public_base_url}/agent-runs/{run.id}/display"
    return RuntimeDisplayContext(
        run_id=str(run.id),
        events_url=f"{events_base_url}/api/v1/internal/ag-ui/runs/{run.id}/events/",
        write_token=write_token,
        public_url=display_url,
        display_url=display_url,
        computer_enabled=run.computer_binding_id is not None,
        terminal_url=f"{internal_root}/terminal/",
        workspace_url=f"{internal_root}/workspace/",
        memory_url=f"{internal_root}/memory/",
        workspace_root=run.workspace_root,
        output_root=run.output_root,
        workspace_delegate_url=f"{internal_root}/manage/",
        workspace_delegate_token=workspace_delegate_token,
        workspace_capabilities=tuple(workspace_capabilities),
        browser_enabled=browser_enabled,
        browser_delegate_url=f"{internal_root}/browser/" if browser_enabled else "",
        browser_delegate_token=browser_delegate_token,
        browser_computer_name=(
            run.computer_binding.connection.name
            if browser_enabled and run.computer_binding_id
            else ""
        ),
        mobile_enabled=mobile_enabled,
        mobile_delegate_url=f"{internal_root}/mobile/",
        mobile_delegate_token=mobile_delegate_token,
        mobile_capabilities=tuple(mobile_capabilities if mobile_enabled else []),
        interaction_url=f"{internal_root}/interactions/",
        context_url=f"{internal_root}/context/",
        context_token=context_token,
        checkpoint_url=f"{internal_root}/checkpoint/",
        recovery_url=f"{internal_root}/operations/",
        display_asset_url=f"{internal_root}/display-assets/",
        interaction_token=interaction_token,
        interaction_mode="task",
        turn_index=turn_index,
        **context_extension.display_values(internal_root),
        usage_url=f"{internal_root}/usage/",
        execution_profile=str((execution_profile or {}).get("id") or ""),
        execution_model=str((execution_profile or {}).get("model") or ""),
        reasoning_effort=str(reasoning_effort or ""),
        execution_context_window=int((execution_profile or {}).get("context_window") or 0),
        input_files=tuple(input_files or []),
    )


@transaction.atomic
def resume_private_run(
    *,
    request,
    run_id: str,
    content: str,
    arguments: dict[str, Any] | None = None,
    attachments=None,
    files=None,
    audio=None,
    execution_profile_id: str = "",
    reasoning_effort: str = "",
    _from_follow_up: bool = False,
    _follow_up_manifest=None,
    _follow_up_id=None,
) -> AgentDisplayRun:
    """Invoke the same tool again while preserving the caller's Run context."""

    visible_run = get_private_display_run(request=request, run_id=run_id, require_token=not _from_follow_up)
    # Keep the same envelope -> Run lock order as Worker, cancel and switching.
    from .models import AgentTaskExecution
    AgentTaskExecution.objects.select_for_update(of=("self",)).filter(task__run=visible_run).first()
    run = (
        AgentDisplayRun.objects.select_for_update(of=("self",))
        .select_related(
            "agent",
            "runtime",
            "computer_binding__connection",
            "mobile_binding__device",
        )
        .get(id=visible_run.id)
    )
    if run.status == AgentDisplayRun.STATUS_RUNNING:
        raise AgentRuntimeOperationConflict(
            "This Run is still active. Reply to its pending interaction or wait for completion."
        )
    ensure_run_project_context_authorized(request=request, run=run)
    from .models import AgentTaskExecution
    if AgentTaskExecution.objects.filter(task__run=run, state="running").exists():
        raise AgentRuntimeOperationConflict("The previous turn is finalizing. Retry shortly.")
    task = AgentExecutionTask.objects.select_for_update().filter(run=run).first()
    if task is None:
        raise AgentRuntimeOperationConflict("This Run cannot be resumed.")
    if not task.continuable:
        raise AgentRuntimeOperationConflict(
            "This tool does not support follow-up turns. Start a new Run."
        )
    # A new turn consumes a slot just like a new Run. Serialize admissions with
    # create_invocation_display_run; do not let repeatedly resumed Runs bypass it.
    from apps.common.resource_limits import enforce_capability
    Tenant.objects.select_for_update().get(pk=run.tenant_id)
    enforce_capability(tenant=run.tenant, code="agents.concurrent_runs")
    try:
        runtime = get_runtime_deployment(agent=run.agent, env="prod")
    except AgentRuntimeNotAvailable as exc:
        # A failed Private Run remains continuable. Treat an offline Runtime as a
        # transient service condition so the caller can keep the same Run open
        # and retry after OpenWrt registers a healthy Runtime again.
        raise AgentRuntimeTemporarilyUnavailable(exc.detail) from exc
    previous_version = str((task.request_json or {}).get("agent_version") or "")
    current_version = str(run.agent.current_version or "")
    if previous_version and previous_version != current_version:
        raise AgentRuntimeOperationConflict(
            "The Agent version changed. Start a new Run to use the new version."
        )
    descriptor = next(
        (
            item
            for item in interaction_tools(agent=run.agent, runtime=runtime)
            if item["name"] == task.tool_name and item["availability"]["can_invoke"]
        ),
        None,
    )
    if descriptor is None:
        raise AgentRuntimeNotFound("Agent tool not found or unavailable.")
    if not descriptor.get("policy", {}).get("continuable"):
        raise AgentRuntimeOperationConflict(
            "This tool does not support follow-up turns. Start a new Run."
        )
    from .image_services import prepare_attachments, attachment_arguments, bind_attachments
    if _from_follow_up and _follow_up_manifest:
        from .follow_up_attachments import restore_images
        from .image_services import accepts_image_attachments
        if _follow_up_manifest.get("attachments") and not accepts_image_attachments(descriptor.get("input_schema")):
            raise exceptions.ValidationError("Queued images are no longer supported by this tool.")
        prepared = restore_images(run=run, manifest=_follow_up_manifest)
        files = [item["file_id"] for item in _follow_up_manifest.get("files", [])]
    else:
        prepared = prepare_attachments(request=request, descriptor=descriptor, attachments=attachments, run=run)
    from .file_transfers import prepare_files, prepare_audio, file_arguments, bind_files
    prepared_files = prepare_files(request=request, descriptor=descriptor, references=files, agent_id=run.agent_id, run=run)
    prepared_audio = prepare_audio(request=request, descriptor=descriptor, references=audio, agent_id=run.agent_id, run=run)
    all_files = [*prepared_files, *prepared_audio]
    if len(all_files) > 8:
        raise exceptions.ValidationError({"files": "Provide at most eight files and recordings in one Run."})
    execution_profile, selected_effort = resolve_execution_profile(
        descriptor=descriptor,
        profile_id=execution_profile_id,
        reasoning_effort=reasoning_effort,
    )
    invocation_arguments, text = _private_run_arguments(
        descriptor=descriptor,
        content=content,
        arguments=arguments,
        image_arguments=attachment_arguments(prepared),
        file_arguments=file_arguments(prepared_files),
        audio_arguments=file_arguments(prepared_audio),
    )

    # An OpenWrt reconnect may replace the Registration and Runtime row while the
    # published Agent version and tool contract remain unchanged. Keep the Run as
    # the caller's conversation boundary, but send its next turn to the currently
    # healthy compatible Runtime. This also gives the SDK the same run_id and the
    # authenticated Run Context so ctx.run.messages() contains only this Run's earlier turns.
    if run.runtime_id != runtime.id:
        run.runtime = runtime
        run.save(update_fields=["runtime", "updated_at"])
        task.runtime = runtime
        task.save(update_fields=["runtime", "updated_at"])
    ensure_openwrt_interactive_transport(
        runtime=runtime,
        policy={
            field: bool(descriptor.get("policy", {}).get(field))
            for field in ("task", "continuable", "demo", "chat", "interactive")
        },
    )
    enforce_api_key_agent_policy(
        api_key=getattr(request, "api_key", None),
        agent=run.agent,
    )
    current_request = dict(task.request_json or {})
    turn_index = max(int(current_request.get("turn_index") or 1), 1) + 1
    run.follow_ups.filter(mode="steer", status__in=["pending", "received"]).update(
        status="not_applied", code="TURN_FINISHED", completed_at=timezone.now())
    if not _from_follow_up and task.status != AgentExecutionTask.STATUS_COMPLETED:
        # Manual recovery must not accidentally revive a failed turn's queue
        # before the worker's regular follow-up sweep has observed that failure.
        run.follow_ups.filter(mode="queue", status="pending").update(
            status="blocked", code="PREVIOUS_TURN_FAILED", encrypted_authority="")
    # Each SDK invocation must negotiate Steer again; an older replacement
    # runtime must never inherit the preceding handler's receive capability.
    run.follow_up_mode = "queue"
    run.follow_up_attachment_protocol = 0
    run.save(update_fields=["follow_up_mode", "follow_up_attachment_protocol"])
    context = _resume_invocation_display_context(
        run=run,
        request=request,
        tool_name=task.tool_name,
        turn_index=turn_index,
        workspace_ceiling=tuple(run.workspace_capabilities_snapshot or []) if _from_follow_up else None,
        execution_profile=execution_profile,
        reasoning_effort=selected_effort,
        input_files=file_arguments(all_files),
    )
    next_sequence = (
        run.messages.order_by("-sequence").values_list("sequence", flat=True).first()
        or 0
    ) + 1
    file_blocks = bind_files(run=run, rows=all_files, turn_index=turn_index)
    image_blocks = bind_attachments(run=run, prepared=prepared)
    if text or image_blocks or file_blocks:
        AgentRunMessage.objects.create(
            run=run,
            sequence=next_sequence,
            turn_index=turn_index,
            role=AgentRunMessage.ROLE_USER,
            content=text,
            content_blocks=[{"type": "markdown", "text": text}, *image_blocks, *file_blocks],
            source_message_id=f"follow-up-{_follow_up_id}" if _from_follow_up and _follow_up_id else "",
        )
    task.status = AgentExecutionTask.STATUS_WORKING
    task.result_json = {}
    task.error_code = ""
    task.retry_count = 0
    task.completed_at = None
    task.continuable = bool(descriptor.get("policy", {}).get("continuable"))
    task.recovery_protocol = runtime_recovery_protocol(runtime=runtime, declared=int(descriptor.get("recovery_protocol") or 0))
    task.expires_at = timezone.now() + timedelta(hours=1)
    task.request_json = {
        **current_request,
        "tool_name": task.tool_name,
        "agent_version": run.agent.current_version,
        "tool_contract_digest": tool_contract_digest(descriptor),
        "turn_index": turn_index,
        "execution_profile_id": str((execution_profile or {}).get("id") or ""),
        "execution_model": str((execution_profile or {}).get("model") or ""),
        "reasoning_effort": selected_effort,
    }
    task.save(
        update_fields=[
            "status",
            "result_json",
            "error_code",
            "retry_count",
            "completed_at",
            "continuable",
            "recovery_protocol",
            "expires_at",
            "request_json",
            "updated_at",
        ]
    )
    context = replace(
        context,
        recovery_managed=task.recovery_protocol >= 1,
        recovery_attempt=0,
        recovery_is_replay=False,
        recovery_last_committed_operation=0,
    )
    append_display_event(
        run=run,
        event_type=AgentDisplayEvent.TYPE_RUN_STARTED,
        payload={
            "threadId": str(run.agent_id),
            "runId": str(run.id),
            "turn": turn_index,
            "resumed": True,
        },
    )
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "display-resume-" + uuid.uuid4().hex,
            "method": "tools/call",
            "params": {
                "name": task.tool_name,
                "arguments": invocation_arguments,
            },
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    enqueue_agent_task(task=task, request=request, body=body,
        headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2026-07-28"},
        display_pair=(run, context))
    return run


@transaction.atomic
def cancel_private_run(*, request, run_id: str, include_hidden: bool = False) -> AgentDisplayRun:
    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    run = (
        AgentDisplayRun.objects
        .filter(
            id=run_id,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            consumer_tenant=tenant,
            consumer_project_id=getattr(request, "project_id", None) or None,
            caller_subject_hash=subject.subject_hash,
            **({} if include_hidden else {"caller_hidden_at__isnull": True}),
        )
        .first()
    )
    if run is None:
        raise AgentRuntimeNotFound("Agent Run not found.")
    durable_task = AgentExecutionTask.objects.filter(run=run, execution__isnull=False).first()
    if durable_task is not None:
        request_task_cancel(durable_task)
        run.refresh_from_db()
        return run
    AgentExecutionTask.objects.filter(
        run=run,
        status__in=[AgentExecutionTask.STATUS_WORKING, AgentExecutionTask.STATUS_INPUT_REQUIRED],
    ).update(
        status=AgentExecutionTask.STATUS_CANCELLED,
        completed_at=timezone.now(),
        updated_at=timezone.now(),
    )
    run.interactions.filter(status=AgentRunInteraction.STATUS_PENDING).update(
        status=AgentRunInteraction.STATUS_CANCELLED,
        updated_at=timezone.now(),
    )
    if run.status == AgentDisplayRun.STATUS_RUNNING:
        finish_invocation_display_run(run=run, succeeded=False, error_code="RUN_CANCELLED")
        run.refresh_from_db()
    from .browser_runtime import close_attached_browser_session

    transaction.on_commit(lambda: close_attached_browser_session(run_id=str(run.id)))
    return run


def _execute_private_invocation(**kwargs: Any) -> None:
    close_old_connections()
    request = kwargs["request"]
    agent_id = kwargs["agent_id"]
    session_headers: dict[str, str] | None = None
    try:
        session_headers = initialize_private_runtime_mcp_session(
            request=request,
            agent_id=agent_id,
            headers=kwargs["headers"],
        )
        call_runtime_mcp(method="POST", **{**kwargs, "headers": session_headers})
    except Exception:
        display_pair = kwargs.get("display_pair")
        run = display_pair[0] if isinstance(display_pair, tuple) and display_pair else None
        if isinstance(run, AgentDisplayRun):
            finish_invocation_display_run(
                run=run, succeeded=False, error_code="INVOCATION_FAILED"
            )
    finally:
        if session_headers is not None:
            close_private_runtime_mcp_session(
                request=request,
                agent_id=agent_id,
                headers=session_headers,
            )
        close_old_connections()


def _mcp_tool_result_succeeded(result: RuntimeMCPResult) -> bool:
    """Interpret both JSON and Streamable HTTP SSE tool results.

    MCP reports tool failures inside a successful HTTP response via
    ``result.isError``.  Treat malformed or explicit JSON-RPC error responses
    as failed without copying Agent-authored error content into the Run.
    """

    if result.status_code >= 400 or not result.body:
        return False
    try:
        text = result.body.decode("utf-8")
    except UnicodeDecodeError:
        return False
    documents: list[dict[str, Any]] = []
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            return False
        if isinstance(value, dict):
            documents.append(value)
    else:
        event_type = ""
        for line in text.splitlines():
            if line.startswith("event:"):
                event_type = line[6:].strip().casefold()
                continue
            if not line.strip():
                event_type = ""
                continue
            if not line.startswith("data:"):
                continue
            try:
                value = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                if event_type == "error":
                    return False
                documents.append(value)
    if not documents:
        return False
    for document in documents:
        if isinstance(document.get("error"), dict):
            return False
        if document.get("isError") is True:
            return False
        tool_result = document.get("result")
        if isinstance(tool_result, dict) and tool_result.get("isError") is True:
            return False
    return True


def _mcp_task_capability(payload: dict[str, Any], *, headers: dict[str, str]) -> bool:
    if _header_value(headers, "MCP-Protocol-Version") != "2026-07-28":
        return False
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    client = meta.get("io.modelcontextprotocol/clientCapabilities")
    extensions = client.get("extensions") if isinstance(client, dict) else {}
    return isinstance(extensions, dict) and "io.modelcontextprotocol/tasks" in extensions


def _task_exception_details(exc: Exception) -> tuple[str, str]:
    """Return only API-safe failure details for a caller-owned Display."""

    if not isinstance(exc, exceptions.APIException):
        return "MCP_TASK_FAILED", "Agent runtime invocation failed."
    raw_code = str(getattr(exc, "default_code", "") or "").upper()
    detail = getattr(exc, "detail", "")
    if isinstance(detail, dict) and isinstance(detail.get("code"), str):
        raw_code = detail["code"].upper()
    code = raw_code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", raw_code) else "MCP_TASK_FAILED"
    message = str(detail).strip() if not isinstance(detail, (dict, list)) else ""
    return code, (message[:300] if message else "Agent runtime invocation failed.")


def _task_result_failure_details(payload: dict[str, Any]) -> tuple[str, str]:
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    if not error and isinstance(payload.get("code"), str):
        raw_code = str(payload.get("code") or "").upper()
        code = (
            raw_code
            if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", raw_code)
            else "MCP_TASK_FAILED"
        )
        message = str(payload.get("message") or "").strip()
        return code, (message[:300] if message else "Agent runtime invocation failed.")
    data = error.get("data") if isinstance(error.get("data"), dict) else {}
    gateway_body = data.get("gatewayBody") if isinstance(data.get("gatewayBody"), dict) else {}
    gateway_error = gateway_body.get("error") if isinstance(gateway_body.get("error"), dict) else {}
    # SDK/direct MCP transports may return data.code without a gateway wrapper.
    # Preserve the innermost explicit symbolic code, not JSON-RPC's numeric code.
    raw_code = str(
        gateway_error.get("code")
        or gateway_body.get("code")
        or data.get("code")
        or error.get("code")
        or "MCP_TASK_FAILED"
    ).upper()
    code = raw_code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", raw_code) else "MCP_TASK_FAILED"
    message = str(gateway_error.get("message") or gateway_body.get("message") or error.get("message") or "").strip()
    if not error:
        tool_result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
        meta = tool_result.get("_meta")
        nexus = meta.get("nexus") if isinstance(meta, dict) else None
        failure = nexus.get("failure") if isinstance(nexus, dict) else None
        if tool_result.get("isError") is True and isinstance(failure, dict):
            from .run_failures import describe_failure, safe_code
            symbolic = safe_code(failure.get("code"))
            if symbolic:
                return symbolic, describe_failure(symbolic)["message"]
        content = tool_result.get("content") if isinstance(tool_result, dict) else []
        tool_text = " ".join(
            str(item.get("text") or "")
            for item in content or []
            if isinstance(item, dict) and item.get("type") == "text"
        ).casefold()
        known_failures = (
            ("mobile is being controlled by another run", "MOBILE_BUSY", "Mobile is busy with another Run."),
            ("mobile capability is not authorized", "MOBILE_PERMISSION_REQUIRED", "Mobile permission is required."),
            ("mobile is not enabled for this run", "MOBILE_REQUIRED", "Attach an available Mobile before starting this Run."),
            ("nexus mobile service is unavailable", "MOBILE_UNAVAILABLE", "The attached Mobile is temporarily unavailable."),
            ("mobile action was rejected by the caller", "MOBILE_ACTION_REJECTED", "The Mobile action was declined."),
            ("mobile action was canceled before completion", "MOBILE_ACTION_CANCELLED", "The Mobile action was cancelled."),
            ("mobile action failed on the attached device", "MOBILE_ACTION_FAILED", "The Mobile action failed on the attached device."),
            # Compatibility with SDK <= 0.42.0, which did not preserve the
            # terminal Mobile command status in its exception message.
            ("mobile action was rejected or failed", "MOBILE_ACTION_FAILED", "The Mobile action failed on the attached device."),
        )
        for marker, known_code, known_message in known_failures:
            if marker in tool_text:
                return known_code, known_message
        if tool_result.get("isError") is True:
            return "TOOL_EXECUTION_FAILED", "The tool reported an unsuccessful task result."
    return code, (message[:300] if message else "Agent runtime invocation failed.")


def _wait_for_private_reporter_flush(run: AgentDisplayRun) -> None:
    """Keep a Private Display Run writable briefly while its reporter drains."""

    latest_user_sequence = (
        AgentRunMessage.objects.filter(run=run, role=AgentRunMessage.ROLE_USER)
        .order_by("-sequence")
        .values_list("sequence", flat=True)
        .first()
    )
    if latest_user_sequence is None:
        return
    timeout = min(
        max(float(getattr(settings, "NEXUS_AGENT_CHAT_EVENT_FLUSH_SECONDS", 1.0)), 0.0),
        5.0,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if AgentRunMessage.objects.filter(
            run=run,
            role=AgentRunMessage.ROLE_ASSISTANT,
            sequence__gt=latest_user_sequence,
        ).exists():
            return
        time.sleep(0.05)


def _execute_mcp_task(*, task_id: str, request, agent_id: str, body: bytes, headers: dict[str, str], display_pair) -> str | None:
    close_old_connections()
    started = time.monotonic()
    try:
        while True:
            assert_execution_lease()
            session_headers: dict[str, str] | None = None
            try:
                try:
                    session_headers = initialize_private_runtime_mcp_session(
                        request=request,
                        agent_id=agent_id,
                        headers=headers,
                    )
                    result = call_runtime_mcp(
                        request=request,
                        agent_id=agent_id,
                        method="POST",
                        body=body,
                        headers=session_headers,
                        display_pair=display_pair,
                        execution_task_id=task_id,
                    )
                finally:
                    if session_headers is not None:
                        close_private_runtime_mcp_session(
                            request=request,
                            agent_id=agent_id,
                            headers=session_headers,
                        )
                payload, succeeded, result_json = _decode_mcp_task_response(result)
                failure_code = ""
                failure_message = ""
                with transaction.atomic():
                    assert_execution_lease()
                    AgentDisplayRun.objects.select_for_update().get(pk=display_pair[0].pk)
                    task = AgentExecutionTask.objects.select_for_update().filter(id=task_id).first()
                    if task is not None and task.status == AgentExecutionTask.STATUS_CANCEL_REQUESTED:
                        return "cancel_acknowledged" if result.status_code < 400 else "cancel_unconfirmed"
                    if task is None or task.status in {AgentExecutionTask.STATUS_CANCELLED, AgentExecutionTask.STATUS_CANCEL_REQUESTED}:
                        return
                    if succeeded:
                        task.status = AgentExecutionTask.STATUS_COMPLETED
                        task.result_json = result_json
                        task.error_code = ""
                    else:
                        failure_code, failure_message = _task_result_failure_details(payload)
                        task.status = AgentExecutionTask.STATUS_FAILED
                        from .run_failures import describe_failure
                        # MCP isError results have no JSON-RPC error member. Keep
                        # a safe cause snapshot instead of silently dropping it.
                        task.result_json = {"code": failure_code, "message": describe_failure(failure_code)["message"]}
                        task.error_code = failure_code
                    task.completed_at = timezone.now()
                    task.save(update_fields=["status", "result_json", "error_code", "completed_at", "updated_at"])
                    # Persist the result and settle once in the same transaction.
                    # A worker crash must not charge a result it never recorded.
                    for invocation in AgentRuntimeInvocation.objects.filter(display_run=task.run, status="pending"):
                        finalize_runtime_invocation(request=request, invocation=invocation,
                            succeeded=succeeded, error_code=failure_code, latency_ms=elapsed_ms(started))
                _wait_for_private_reporter_flush(task.run)
                materialize_private_run_result(
                    run=task.run,
                    value=task.result_json,
                    succeeded=succeeded,
                )
                finish_invocation_display_run(
                    run=task.run,
                    succeeded=succeeded,
                    error_code=failure_code,
                    error_message=failure_message,
                )
                return
            except Exception as exc:
                close_old_connections()
                # Platform-managed executions are finalized by the durable
                # worker.  Let the exception cross this legacy checkpoint
                # loop so the worker can fence the attempt, retain the payload
                # and reconcile/replay the same Run and Turn.
                managed_task = (
                    AgentExecutionTask.objects.filter(id=task_id)
                    .values("recovery_protocol")
                    .first()
                )
                if (
                    managed_task
                    and int(managed_task.get("recovery_protocol") or 0) >= 1
                    and AgentTaskExecution.objects.filter(task_id=task_id).exists()
                ):
                    raise
                failure_code, failure_message = _task_exception_details(exc)
                retry_delay: float | None = None
                failed_task: AgentExecutionTask | None = None
                with transaction.atomic():
                    assert_execution_lease()
                    AgentDisplayRun.objects.select_for_update().get(pk=display_pair[0].pk)
                    task = (
                        AgentExecutionTask.objects.select_for_update(of=("self",))
                        .select_related("run", "agent", "runtime")
                        .filter(id=task_id)
                        .first()
                    )
                    if task is None or task.status in {AgentExecutionTask.STATUS_CANCELLED, AgentExecutionTask.STATUS_CANCEL_REQUESTED}:
                        return "cancel_unconfirmed"
                    checkpoint_exists = AgentRunCheckpoint.objects.filter(run=task.run).exists()
                    version_matches = str((task.request_json or {}).get("agent_version") or "") == str(task.agent.current_version or "")
                    can_resume = bool(
                        task.continuable
                        and not hasattr(task, "execution")
                        and checkpoint_exists
                        and version_matches
                        and task.expires_at > timezone.now()
                        and task.retry_count < 3
                    )
                    if can_resume:
                        task.retry_count += 1
                        task.status = AgentExecutionTask.STATUS_WORKING
                        task.error_code = ""
                        task.save(update_fields=["retry_count", "status", "error_code", "updated_at"])
                        retry_delay = min(0.25 * (2 ** (task.retry_count - 1)), 1.0)
                    else:
                        task.status = AgentExecutionTask.STATUS_FAILED
                        task.error_code = (
                            "CHECKPOINT_VERSION_CHANGED"
                            if task.continuable and checkpoint_exists and not version_matches
                            else failure_code
                        )
                        task.result_json = {"message": failure_message}
                        task.completed_at = timezone.now()
                        task.save(update_fields=["status", "error_code", "result_json", "completed_at", "updated_at"])
                        failed_task = task
                if retry_delay is not None:
                    append_display_event(
                        run=task.run,
                        event_type="CUSTOM",
                        payload={
                            "name": "nexus.task.resume",
                            "value": {"status": "resuming", "attempt": task.retry_count},
                        },
                        visibility="private",
                    )
                    time.sleep(retry_delay)
                    continue
                if failed_task is not None:
                    materialize_private_run_result(
                        run=failed_task.run,
                        value=failed_task.result_json,
                        succeeded=False,
                    )
                    finish_invocation_display_run(
                        run=failed_task.run,
                        succeeded=False,
                        error_code=failed_task.error_code,
                        error_message=failure_message,
                    )
                    record_invocation(
                        request=request,
                        tenant=get_tenant_from_request(request),
                        agent=failed_task.agent,
                        runtime=failed_task.runtime,
                        api_key=getattr(request, "api_key", None),
                        tool_name=failed_task.tool_name,
                        status_value=AgentRuntimeInvocation.STATUS_FAILED,
                        error_code=failed_task.error_code,
                        latency_ms=elapsed_ms(started),
                        cost=Decimal("0"),
                        display_run=failed_task.run,
                        turn_index=max(
                            int((failed_task.request_json or {}).get("turn_index") or 1),
                            1,
                        ),
                    )
                return
    finally:
        close_old_connections()


def initialize_private_runtime_mcp_session(
    *, request, agent_id: str, headers: dict[str, str]
) -> dict[str, str]:
    """Create a caller-bound ephemeral MCP session for a Private Run worker."""

    tenant = get_tenant_from_request(request)
    agent = get_runtime_use_agent(request=request, tenant=tenant, agent_id=agent_id)
    runtime = get_runtime_deployment(agent=agent, env="prod")
    runner = get_runtime_runner(runtime)
    transport_headers = dict(headers)
    _replace_header(
        transport_headers,
        "Accept",
        "application/json, text/event-stream",
    )
    initialize_body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "private-run-init-" + uuid.uuid4().hex,
            "method": "initialize",
            "params": {
                "protocolVersion": _header_value(
                    transport_headers, "MCP-Protocol-Version"
                )
                or "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "nexus-private-run", "version": "1"},
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    initialized = runner.call_mcp(
        deployment=runtime,
        method="POST",
        headers=transport_headers,
        body=initialize_body,
    )
    if initialized.status_code >= 400:
        raise AgentRuntimeOperationConflict(
            "Private Run MCP initialization failed."
        )
    try:
        initialize_payload = _decode_mcp_json_response(initialized.body)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        initialize_payload = {}
    negotiated_protocol = str(
        ((initialize_payload.get("result") or {}).get("protocolVersion") or "")
        if isinstance(initialize_payload, dict)
        else ""
    ).strip()
    if negotiated_protocol:
        _replace_header(
            transport_headers,
            "MCP-Protocol-Version",
            negotiated_protocol,
        )
    internal_session_id = _header_value(
        initialized.headers, "Mcp-Session-Id"
    )
    if not internal_session_id:
        return transport_headers
    activated_headers = dict(transport_headers)
    _replace_header(
        activated_headers, "Mcp-Session-Id", internal_session_id
    )
    activated = runner.call_mcp(
        deployment=runtime,
        method="POST",
        headers=activated_headers,
        body=b'{"jsonrpc":"2.0","method":"notifications/initialized"}',
    )
    if activated.status_code >= 400:
        raise AgentRuntimeOperationConflict(
            "Private Run MCP session activation failed."
        )
    _session, external_session_id = _create_external_mcp_session(
        request=request,
        tenant=tenant,
        agent=agent,
        runtime=runtime,
        internal_session_id=internal_session_id,
    )
    result_headers = dict(transport_headers)
    _replace_header(result_headers, "Mcp-Session-Id", external_session_id)
    return result_headers


def close_private_runtime_mcp_session(
    *, request, agent_id: str, headers: dict[str, str]
) -> None:
    if not _header_value(headers, "Mcp-Session-Id"):
        return
    try:
        call_runtime_mcp(
            request=request,
            agent_id=agent_id,
            method="DELETE",
            body=b"",
            headers=headers,
        )
    except Exception:
        pass


def _task_for_request(*, request, agent_id: str, task_id: str) -> AgentExecutionTask | None:
    tenant = get_tenant_from_request(request)
    subject = request_subject(request)
    return AgentExecutionTask.objects.filter(
        id=task_id,
        agent_id=agent_id,
        caller_subject_hash=subject.subject_hash,
        run__consumer_tenant=tenant,
    ).select_related("run", "agent").first()


def serialize_execution_task(task: AgentExecutionTask, *, detailed: bool = False) -> dict[str, Any]:
    ttl_ms = max(int((task.expires_at - task.created_at).total_seconds() * 1000), 0)
    value: dict[str, Any] = {
        "taskId": str(task.id),
        "status": task.status,
        "createdAt": task.created_at.isoformat(),
        "lastUpdatedAt": task.updated_at.isoformat(),
        "ttlMs": ttl_ms,
        "pollIntervalMs": 1000,
    }
    if not detailed:
        return value
    value["resultType"] = "complete"
    if task.status == AgentExecutionTask.STATUS_INPUT_REQUIRED:
        outstanding: dict[str, Any] = {}
        for interaction in task.run.interactions.filter(status=AgentRunInteraction.STATUS_PENDING).order_by("created_at"):
            properties: dict[str, Any] = {"input": {"type": "string"}}
            choices = [item for item in interaction.choices_json or [] if isinstance(item, dict)]
            if choices:
                properties["input"]["enum"] = [str(item.get("value") or "") for item in choices]
                properties["input"]["x-nexus-labels"] = {
                    str(item.get("value") or ""): str(item.get("label") or item.get("value") or "")
                    for item in choices
                }
            outstanding[interaction.key] = {
                "method": "elicitation/create",
                "params": {
                    "mode": "form",
                    "message": interaction.prompt,
                    "requestedSchema": {
                        "type": "object",
                        "properties": properties,
                        "required": ["input"],
                    },
                },
            }
        value["inputRequests"] = outstanding
    elif task.status == AgentExecutionTask.STATUS_COMPLETED:
        value["result"] = dict(task.result_json or {})
    elif task.status == AgentExecutionTask.STATUS_FAILED:
        value["error"] = dict(task.result_json or {}) or {
            "code": -32603,
            "message": "Agent task failed.",
            "data": {"code": task.error_code or "MCP_TASK_FAILED"},
        }
    return value


@transaction.atomic
def handle_mcp_task_request(*, request, agent_id: str, payload: dict[str, Any], headers: dict[str, str]) -> RuntimeMCPResult:
    if not _mcp_task_capability(payload, headers=headers):
        return _task_rpc_error(
            request_id=payload.get("id"),
            code=-32003,
            message="Missing required client capability",
            data={"requiredCapabilities": {"extensions": {"io.modelcontextprotocol/tasks": {}}}},
        )
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    task_id = str(params.get("taskId") or "")
    task = _task_for_request(request=request, agent_id=agent_id, task_id=task_id)
    if task is None:
        return _task_rpc_error(request_id=payload.get("id"), code=-32602, message="Invalid taskId")
    method = str(payload.get("method") or "")
    if method == "tasks/get":
        task.refresh_from_db()
        return _task_rpc_response(request_id=payload.get("id"), result=serialize_execution_task(task, detailed=True))
    if method == "tasks/update":
        responses = params.get("inputResponses") if isinstance(params.get("inputResponses"), dict) else {}
        for key, response in responses.items():
            interaction = AgentRunInteraction.objects.select_for_update().filter(
                run=task.run,
                key=str(key),
                status=AgentRunInteraction.STATUS_PENDING,
            ).first()
            if interaction is None or not isinstance(response, dict):
                continue
            action = str(response.get("action") or "accept")
            if action != "accept":
                interaction.status = AgentRunInteraction.STATUS_CANCELLED
                interaction.save(update_fields=["status", "updated_at"])
                continue
            content = response.get("content") if isinstance(response.get("content"), dict) else {}
            input_value = content.get("input", content.get("value", ""))
            text_value = str(input_value or "").strip()[:4000]
            if not text_value:
                continue
            allowed = {
                str(item.get("value") or "")
                for item in interaction.choices_json or []
                if isinstance(item, dict)
            }
            if allowed and text_value not in allowed:
                continue
            interaction.status = AgentRunInteraction.STATUS_ANSWERED
            interaction.response_json = {"value": text_value, "text": text_value}
            interaction.answered_at = timezone.now()
            interaction.save(update_fields=["status", "response_json", "answered_at", "updated_at"])
            message_id = "user-" + uuid.uuid4().hex
            append_display_event(run=task.run, event_type="TEXT_MESSAGE_START", payload={"messageId": message_id, "role": "user"})
            append_display_event(run=task.run, event_type="TEXT_MESSAGE_CONTENT", payload={"messageId": message_id, "delta": text_value})
            append_display_event(run=task.run, event_type="TEXT_MESSAGE_END", payload={"messageId": message_id})
        remaining = task.run.interactions.filter(status=AgentRunInteraction.STATUS_PENDING).exists()
        task.status = AgentExecutionTask.STATUS_INPUT_REQUIRED if remaining else AgentExecutionTask.STATUS_WORKING
        task.save(update_fields=["status", "updated_at"])
        return _task_rpc_response(request_id=payload.get("id"), result={"resultType": "complete"})
    if method == "tasks/cancel":
        if hasattr(task, "execution"):
            request_task_cancel(task)
            return _task_rpc_response(request_id=payload.get("id"), result={"resultType": "complete", "status": "cancel_requested"})
        if task.status not in {
            AgentExecutionTask.STATUS_COMPLETED,
            AgentExecutionTask.STATUS_FAILED,
            AgentExecutionTask.STATUS_CANCELLED,
        }:
            task.status = AgentExecutionTask.STATUS_CANCELLED
            task.completed_at = timezone.now()
            task.save(update_fields=["status", "completed_at", "updated_at"])
            task.run.interactions.filter(status=AgentRunInteraction.STATUS_PENDING).update(
                status=AgentRunInteraction.STATUS_CANCELLED,
                updated_at=timezone.now(),
            )
        return _task_rpc_response(request_id=payload.get("id"), result={"resultType": "complete"})
    return _task_rpc_error(request_id=payload.get("id"), code=-32601, message="Method not found")


def _task_rpc_response(*, request_id, result: dict[str, Any], headers: dict[str, str] | None = None) -> RuntimeMCPResult:
    body = json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, separators=(",", ":")).encode("utf-8")
    return RuntimeMCPResult(status_code=200, headers={"Content-Type": "application/json", **(headers or {})}, body=body)


def _task_rpc_error(*, request_id, code: int, message: str, data: dict[str, Any] | None = None) -> RuntimeMCPResult:
    error: dict[str, Any] = {"code": code, "message": message}
    if data:
        error["data"] = data
    body = json.dumps({"jsonrpc": "2.0", "id": request_id, "error": error}, separators=(",", ":")).encode("utf-8")
    return RuntimeMCPResult(status_code=200, headers={"Content-Type": "application/json"}, body=body)


def call_runtime_mcp(
    *, request, agent_id: str, method: str, body: bytes,
    headers: dict[str, str], upstream_path: str = "",
    display_pair: tuple[AgentDisplayRun, RuntimeDisplayContext] | None = None,
    execution_task_id: str = "",
) -> RuntimeMCPResult:
    tenant = get_tenant_from_request(request)
    agent = get_runtime_use_agent(request=request, tenant=tenant, agent_id=agent_id)
    api_key = getattr(request, "api_key", None)
    enforce_api_key_agent_policy(api_key=api_key, agent=agent)
    runtime = get_runtime_deployment(agent=agent, env="prod")
    if upstream_path == "/messages/":
        upstream_path = prepare_legacy_sse_message_path(
            request=request,
            tenant=tenant,
            agent=agent,
            runtime=runtime,
        )
    headers, mcp_session, external_session_id = prepare_mcp_session_request(
        request=request,
        tenant=tenant,
        agent=agent,
        runtime=runtime,
        headers=headers,
    )
    tool_name = tool_name_from_mcp_payload(body)
    business_call = is_mcp_tool_call(body)
    policy = runtime_tool_policy(agent=agent, runtime=runtime, tool_name=tool_name)
    if business_call:
        ensure_openwrt_interactive_transport(runtime=runtime, policy=policy)
    started = time.monotonic()

    display_run: AgentDisplayRun | None = None
    display_context: RuntimeDisplayContext | None = None
    if display_pair is not None:
        display_run, display_context = display_pair
    elif business_call:
        from .file_transfers import create_mcp_file_context
        display_run, display_context, body = create_mcp_file_context(
            runtime=runtime,
            tool_name=tool_name,
            request=request,
            body=body,
        )
    pending_invocation = None
    if business_call and display_run is not None and display_context is not None:
        try:
            pending_invocation = begin_runtime_invocation(
                request=request,
                tenant=tenant,
                agent=agent,
                runtime=runtime,
                api_key=api_key,
                tool_name=tool_name,
                display_run=display_run,
                turn_index=display_context.turn_index,
            )
        except InvocationFundingDenied:
            finish_invocation_display_run(
                run=display_run,
                succeeded=False,
                error_code="BALANCE_NOT_ENOUGH",
            )
            raise
    runtime_headers = runtime_headers_with_agui(headers=headers, context=display_context)

    try:
        runner = get_runtime_runner(runtime)
        # Interactive MCP Tasks may wait for Display/MCP input far beyond the
        # ordinary request timeout.  The Task owns cancellation/finalization,
        # while Relay interactive tools have already been rejected above.
        task_timeout = None
        if business_call:
            from apps.common.resource_limits import capability_state

            duration_limit = capability_state(
                tenant=runtime.tenant,
                code="agents.run_minutes_per_run",
            )["limit"]
            if duration_limit is not None:
                task_timeout = max(float(Decimal(duration_limit) * Decimal(60)), 1.0)
            elif execution_task_id:
                task_timeout = 3600
            if execution_task_id:
                deadline = AgentExecutionTask.objects.get(pk=execution_task_id).expires_at
                task_timeout = max(1.0, (deadline - timezone.now()).total_seconds())
        if upstream_path:
            result = runner.call_mcp(
                deployment=runtime, method=method, headers=runtime_headers,
                body=body, path=upstream_path, timeout=task_timeout,
            )
        else:
            result = runner.call_mcp(
                deployment=runtime, method=method, headers=runtime_headers,
                body=body, timeout=task_timeout,
            )
    except Exception:
        # A Task worker owns finalization so it can recover a managed execution
        # tool from a durable checkpoint after a transient container failure.
        # Non-Task calls preserve the original immediate failure semantics.
        if not execution_task_id:
            finish_invocation_display_run(
                run=display_run,
                succeeded=False,
                error_code="MCP_REQUEST_FAILED",
            )
        if pending_invocation is not None and not execution_task_id:
            finalize_runtime_invocation(
                request=request,
                invocation=pending_invocation,
                succeeded=False,
                error_code="MCP_REQUEST_FAILED",
                latency_ms=elapsed_ms(started),
            )
        raise

    task_cancelled = bool(
        execution_task_id
        and AgentExecutionTask.objects.filter(
            id=execution_task_id,
            status__in=[AgentExecutionTask.STATUS_CANCELLED, AgentExecutionTask.STATUS_CANCEL_REQUESTED],
        ).exists()
    )
    assert_execution_lease()
    is_success = _mcp_tool_result_succeeded(result) and not task_cancelled if business_call else result.status_code < 400
    if not execution_task_id:
        finish_invocation_display_run(
            run=display_run,
            succeeded=is_success,
            error_code="" if is_success else ("RUN_CANCELLED" if task_cancelled else "MCP_REQUEST_FAILED"),
        )
    if pending_invocation is not None and not execution_task_id:
        finalize_runtime_invocation(
            request=request,
            invocation=pending_invocation,
            succeeded=is_success,
            error_code="" if is_success else ("RUN_CANCELLED" if task_cancelled else "MCP_REQUEST_FAILED"),
            latency_ms=elapsed_ms(started),
        )
    response_headers = sanitized_runtime_response_headers(result.headers)
    sync_runtime_tool_policy_from_response(agent=agent, request_body=body, response_body=result.body)
    finalize_mcp_session_response(
        request=request,
        tenant=tenant,
        agent=agent,
        runtime=runtime,
        method=method,
        status_code=result.status_code,
        response_headers=response_headers,
        session=mcp_session,
        external_session_id=external_session_id,
        request_body=body,
    )
    add_private_display_response_headers(response_headers=response_headers, run=display_run, context=display_context)
    return RuntimeMCPResult(
        status_code=result.status_code,
        headers=response_headers,
        body=result.body,
    )


def call_runtime_mcp_stream(
    *, request, agent_id: str, method: str, body: bytes,
    headers: dict[str, str], upstream_path: str = "",
) -> RuntimeMCPStreamResult:
    tenant = get_tenant_from_request(request)
    agent = get_runtime_use_agent(request=request, tenant=tenant, agent_id=agent_id)
    api_key = getattr(request, "api_key", None)
    enforce_api_key_agent_policy(api_key=api_key, agent=agent)
    runtime = get_runtime_deployment(agent=agent, env="prod")
    if upstream_path == "/messages/":
        upstream_path = prepare_legacy_sse_message_path(
            request=request,
            tenant=tenant,
            agent=agent,
            runtime=runtime,
        )
    headers, mcp_session, external_session_id = prepare_mcp_session_request(
        request=request,
        tenant=tenant,
        agent=agent,
        runtime=runtime,
        headers=headers,
    )
    tool_name = tool_name_from_mcp_payload(body)
    business_call = is_mcp_tool_call(body)
    policy = runtime_tool_policy(agent=agent, runtime=runtime, tool_name=tool_name)
    if business_call:
        ensure_openwrt_interactive_transport(runtime=runtime, policy=policy)
    started = time.monotonic()

    display_run: AgentDisplayRun | None = None
    display_context: RuntimeDisplayContext | None = None
    if business_call:
        from .file_transfers import create_mcp_file_context
        display_run, display_context, body = create_mcp_file_context(
            runtime=runtime,
            tool_name=tool_name,
            request=request,
            body=body,
        )
    pending_invocation = None
    if business_call and display_run is not None and display_context is not None:
        try:
            pending_invocation = begin_runtime_invocation(
                request=request,
                tenant=tenant,
                agent=agent,
                runtime=runtime,
                api_key=api_key,
                tool_name=tool_name,
                display_run=display_run,
                turn_index=display_context.turn_index,
            )
        except InvocationFundingDenied:
            finish_invocation_display_run(
                run=display_run,
                succeeded=False,
                error_code="BALANCE_NOT_ENOUGH",
            )
            raise
    runtime_headers = runtime_headers_with_agui(headers=headers, context=display_context)

    try:
        runner = get_runtime_runner(runtime)
        if upstream_path:
            result = runner.stream_mcp(deployment=runtime, method=method, headers=runtime_headers, body=body, path=upstream_path)
        else:
            result = runner.stream_mcp(deployment=runtime, method=method, headers=runtime_headers, body=body)
    except Exception as exc:
        code = getattr(exc, "default_code", "")
        finish_invocation_display_run(
            run=display_run,
            succeeded=False,
            error_code=str(code or "MCP_STREAM_FAILED"),
        )
        if pending_invocation is not None:
            finalize_runtime_invocation(
                request=request,
                invocation=pending_invocation,
                succeeded=False,
                error_code=str(code or "MCP_STREAM_FAILED"),
                latency_ms=elapsed_ms(started),
            )
        if code == "AGENT_STREAMING_NOT_SUPPORTED":
            raise AgentStreamingNotSupported() from exc
        raise

    source_chunks = result.chunks
    if upstream_path == "/sse":
        source_chunks = rewrite_legacy_sse_chunks(
            chunks=source_chunks,
            request=request,
            tenant=tenant,
            agent=agent,
            runtime=runtime,
        )

    seen_elicitations: set[str] = set()

    def recording_chunks():
        status_value = AgentRuntimeInvocation.STATUS_SUCCESS if result.status_code < 400 else AgentRuntimeInvocation.STATUS_FAILED
        error_code = "" if status_value == AgentRuntimeInvocation.STATUS_SUCCESS else "MCP_STREAM_FAILED"
        chunks: "queue.Queue[tuple[str, Any]]" = queue.Queue(maxsize=64)
        stopped = threading.Event()

        def enqueue(kind: str, value: Any) -> bool:
            while not stopped.is_set():
                try:
                    chunks.put((kind, value), timeout=0.5)
                    return True
                except queue.Full:
                    continue
            return False

        def consume_upstream() -> None:
            try:
                for upstream_chunk in source_chunks:
                    if not enqueue("chunk", upstream_chunk):
                        return
                enqueue("done", None)
            except BaseException as exc:
                enqueue("error", exc)

        threading.Thread(
            target=consume_upstream,
            name="nexus-mcp-stream-" + str(display_run.id if display_run else uuid.uuid4())[:12],
            daemon=True,
        ).start()
        last_heartbeat = time.monotonic()
        try:
            while True:
                try:
                    kind, value = chunks.get(timeout=0.5)
                except queue.Empty:
                    kind, value = "idle", None
                yield from pending_mcp_elicitation_chunks(
                    request=request,
                    run=display_run,
                    session=mcp_session,
                    seen=seen_elicitations,
                )
                if kind == "chunk":
                    yield value
                elif kind == "error":
                    raise value
                elif kind == "done":
                    break
                if time.monotonic() - last_heartbeat >= 10:
                    yield b": nexus-heartbeat\n\n"
                    last_heartbeat = time.monotonic()
        except GeneratorExit:
            status_value = AgentRuntimeInvocation.STATUS_FAILED
            error_code = "RUN_CANCELLED"
            raise
        except Exception:
            status_value = AgentRuntimeInvocation.STATUS_FAILED
            error_code = "MCP_STREAM_FAILED"
            raise
        finally:
            stopped.set()
            finish_invocation_display_run(
                run=display_run,
                succeeded=status_value == AgentRuntimeInvocation.STATUS_SUCCESS,
                error_code=error_code,
            )
            if pending_invocation is not None:
                finalize_runtime_invocation(
                    request=request,
                    invocation=pending_invocation,
                    succeeded=status_value == AgentRuntimeInvocation.STATUS_SUCCESS,
                    error_code=error_code,
                    latency_ms=elapsed_ms(started),
                )

    response_headers = sanitized_runtime_response_headers(result.headers)
    finalize_mcp_session_response(
        request=request,
        tenant=tenant,
        agent=agent,
        runtime=runtime,
        method=method,
        status_code=result.status_code,
        response_headers=response_headers,
        session=mcp_session,
        external_session_id=external_session_id,
        request_body=body,
    )
    add_private_display_response_headers(response_headers=response_headers, run=display_run, context=display_context)
    return RuntimeMCPStreamResult(status_code=result.status_code, headers=response_headers, chunks=recording_chunks())


def pending_mcp_elicitation_chunks(
    *, request, run: AgentDisplayRun | None,
    session: AgentMCPSession | None, seen: set[str],
):
    capabilities = session.client_capabilities_json if session is not None else {}
    if run is None or not isinstance(capabilities, dict) or not capabilities.get("elicitation"):
        return
    subject = request_subject(request)
    interactions = run.interactions.filter(
        status=AgentRunInteraction.STATUS_PENDING,
        expires_at__gt=timezone.now(),
    ).exclude(id__in=seen).order_by("created_at")
    for interaction in interactions:
        interaction_id = str(interaction.id)
        seen.add(interaction_id)
        request_id = "nexus-elicit-" + uuid.uuid4().hex
        ttl = min(
            max(int((interaction.expires_at - timezone.now()).total_seconds()), 1),
            900,
        )
        cache.set(
            _elicitation_cache_key(request_id),
            {
                "agent_id": str(run.agent_id),
                "tenant_id": str(run.consumer_tenant_id or ""),
                "project_id": str(run.consumer_project_id or ""),
                "caller_subject_hash": subject.subject_hash,
                "mcp_session_hash": session.external_session_hash,
                "run_id": str(run.id),
                "interaction_id": interaction_id,
            },
            timeout=ttl,
        )
        input_schema: dict[str, Any] = {"type": "string"}
        choices = [
            item for item in interaction.choices_json or []
            if isinstance(item, dict) and item.get("value")
        ]
        if choices:
            input_schema["enum"] = [str(item.get("value")) for item in choices]
            input_schema["x-nexus-labels"] = {
                str(item.get("value")): str(item.get("label") or item.get("value"))
                for item in choices
            }
        rpc = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "elicitation/create",
            "params": {
                "mode": "form",
                "message": interaction.prompt,
                "requestedSchema": {
                    "type": "object",
                    "properties": {"input": input_schema},
                    "required": ["input"],
                },
            },
        }
        yield (
            "event: message\n"
            "data: " + json.dumps(rpc, separators=(",", ":"), ensure_ascii=False)
            + "\n\n"
        ).encode("utf-8")


def add_private_display_response_headers(*, response_headers: dict[str, str], run, context) -> None:
    if run is None or context is None:
        return
    response_headers["X-Nexus-Agent-Run-Id"] = str(run.id)
    response_headers["X-Nexus-Agent-Display-Token"] = context.display_token
    response_headers["X-Nexus-Agent-Display-Url"] = context.display_url


def sanitized_runtime_response_headers(headers: dict[str, str]) -> dict[str, str]:
    blocked_prefixes = (
        "x-nexus-agui-",
        "x-nexus-agent-",
        "x-nexus-workspace-",
        "x-nexus-terminal-",
        "x-nexus-interaction-",
        "x-nexus-checkpoint-",
        "x-nexus-display-asset-",
        "x-nexus-mobile-",
    )
    return {
        key: value
        for key, value in headers.items()
        if not key.lower().startswith(blocked_prefixes)
    }


def _header_value(headers: dict[str, str], name: str) -> str:
    expected = name.lower()
    return next((str(value) for key, value in headers.items() if key.lower() == expected), "")


def _replace_header(headers: dict[str, str], name: str, value: str) -> None:
    expected = name.lower()
    for key in list(headers):
        if key.lower() == expected:
            del headers[key]
    if value:
        headers[name] = value


def _mcp_session_queryset(*, request, tenant, agent, runtime):
    subject = request_subject(request)
    return AgentMCPSession.objects.filter(
        tenant=tenant,
        project_id=getattr(request, "project_id", None) or None,
        agent=agent,
        runtime=runtime,
        caller_subject_hash=subject.subject_hash,
        expires_at__gt=timezone.now(),
    )


def _mcp_client_capabilities(body: bytes) -> dict[str, bool]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("method") != "initialize":
        return {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    capabilities = params.get("capabilities") if isinstance(params.get("capabilities"), dict) else {}
    extensions = capabilities.get("extensions") if isinstance(capabilities.get("extensions"), dict) else {}
    return {
        "elicitation": isinstance(capabilities.get("elicitation"), dict),
        "tasks": isinstance(extensions.get("io.modelcontextprotocol/tasks"), dict),
    }


def _create_external_mcp_session(
    *, request, tenant, agent, runtime, internal_session_id: str,
    client_capabilities: dict[str, bool] | None = None,
) -> tuple[AgentMCPSession, str]:
    subject = request_subject(request)
    external_session_id = secrets.token_urlsafe(32)
    session = AgentMCPSession.objects.create(
        tenant=tenant,
        project_id=getattr(request, "project_id", None) or None,
        agent=agent,
        runtime=runtime,
        caller_subject_hash=subject.subject_hash,
        external_session_hash=hash_token(external_session_id),
        internal_session_id=internal_session_id,
        client_capabilities_json=dict(client_capabilities or {}),
        expires_at=timezone.now() + timedelta(
            seconds=int(getattr(settings, "NEXUS_AGENT_MCP_SESSION_TTL_SECONDS", 3600))
        ),
    )
    return session, external_session_id


def prepare_legacy_sse_message_path(*, request, tenant, agent, runtime) -> str:
    query_params = getattr(request, "query_params", None) or request.GET
    external_session_id = str(query_params.get("session_id") or "").strip()
    if not external_session_id:
        raise AgentRuntimeNotFound("MCP session not found.")
    session = _mcp_session_queryset(
        request=request,
        tenant=tenant,
        agent=agent,
        runtime=runtime,
    ).filter(external_session_hash=hash_token(external_session_id)).first()
    if session is None:
        raise AgentRuntimeNotFound("MCP session not found.")
    session.expires_at = timezone.now() + timedelta(
        seconds=int(getattr(settings, "NEXUS_AGENT_MCP_SESSION_TTL_SECONDS", 3600))
    )
    session.save(update_fields=["expires_at", "updated_at"])
    return "/messages/?session_id=" + quote(session.internal_session_id, safe="")


def rewrite_legacy_sse_chunks(*, chunks, request, tenant, agent, runtime):
    """Replace FastMCP's container session endpoint with a caller-bound URL."""

    pending = b""
    for chunk in chunks:
        pending += bytes(chunk)
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            yield rewrite_legacy_sse_line(
                line + b"\n",
                request=request,
                tenant=tenant,
                agent=agent,
                runtime=runtime,
            )
    if pending:
        yield rewrite_legacy_sse_line(
            pending,
            request=request,
            tenant=tenant,
            agent=agent,
            runtime=runtime,
        )


def rewrite_legacy_sse_line(line: bytes, *, request, tenant, agent, runtime) -> bytes:
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        return line
    stripped = text.rstrip("\r\n")
    ending = text[len(stripped):]
    if not stripped.startswith("data:"):
        return line
    endpoint = stripped[5:].strip()
    parsed = urlsplit(endpoint)
    if not parsed.path.rstrip("/").endswith("/messages"):
        return line
    internal_session_id = str((parse_qs(parsed.query).get("session_id") or [""])[0]).strip()
    if not internal_session_id:
        return line
    _session, external_session_id = _create_external_mcp_session(
        request=request,
        tenant=tenant,
        agent=agent,
        runtime=runtime,
        internal_session_id=internal_session_id,
    )
    message_path = f"/api/v1/agents/{agent.id}/messages/?session_id={quote(external_session_id, safe='')}"
    return f"data: {request.build_absolute_uri(message_path)}{ending}".encode("utf-8")


def prepare_mcp_session_request(*, request, tenant, agent, runtime, headers: dict[str, str]):
    safe_headers = dict(headers)
    external_session_id = _header_value(safe_headers, "Mcp-Session-Id")
    if not external_session_id:
        return safe_headers, None, ""
    session = _mcp_session_queryset(
        request=request,
        tenant=tenant,
        agent=agent,
        runtime=runtime,
    ).filter(external_session_hash=hash_token(external_session_id)).first()
    if session is None:
        raise AgentRuntimeNotFound("MCP session not found.")
    _replace_header(safe_headers, "Mcp-Session-Id", session.internal_session_id)
    session.expires_at = timezone.now() + timedelta(
        seconds=int(getattr(settings, "NEXUS_AGENT_MCP_SESSION_TTL_SECONDS", 3600))
    )
    session.save(update_fields=["expires_at", "updated_at"])
    return safe_headers, session, external_session_id


@transaction.atomic
def finalize_mcp_session_response(
    *,
    request,
    tenant,
    agent,
    runtime,
    method: str,
    status_code: int,
    response_headers: dict[str, str],
    session,
    external_session_id: str,
    request_body: bytes = b"",
) -> AgentMCPSession | None:
    client_capabilities = _mcp_client_capabilities(request_body)
    internal_session_id = _header_value(response_headers, "Mcp-Session-Id")
    if method.upper() == "DELETE":
        if session is not None and status_code < 400:
            session.delete()
        _replace_header(response_headers, "Mcp-Session-Id", "")
        return None
    if not internal_session_id:
        return session
    if session is None:
        session, external_session_id = _create_external_mcp_session(
            request=request,
            tenant=tenant,
            agent=agent,
            runtime=runtime,
            internal_session_id=internal_session_id,
            client_capabilities=client_capabilities,
        )
    else:
        update_fields: list[str] = []
        if session.internal_session_id != internal_session_id:
            session.internal_session_id = internal_session_id
            update_fields.append("internal_session_id")
        if client_capabilities and session.client_capabilities_json != client_capabilities:
            session.client_capabilities_json = client_capabilities
            update_fields.append("client_capabilities_json")
        if update_fields:
            session.save(update_fields=[*update_fields, "updated_at"])
    _replace_header(response_headers, "Mcp-Session-Id", external_session_id)
    return session


def get_internal_invocation_run(*, run_id: str, token: str) -> AgentDisplayRun:
    run = (
        AgentDisplayRun.objects.filter(
            id=run_id,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            status__in=[AgentDisplayRun.STATUS_RUNNING, AgentDisplayRun.STATUS_COMPLETED, AgentDisplayRun.STATUS_FAILED],
        )
        .select_related("computer_binding__connection")
        .first()
    )
    if run is None or not token or not secrets.compare_digest(str(run.write_token), str(token)):
        raise AgentRuntimeNotFound("Agent run not found.")
    return run


def get_internal_invocation_run_or_delegate(*, run_id: str, token: str) -> AgentDisplayRun:
    try:
        return get_internal_invocation_run(run_id=run_id, token=token)
    except AgentRuntimeNotFound:
        run = AgentDisplayRun.objects.filter(
            id=run_id,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
        ).select_related("agent", "consumer_tenant", "consumer_project").first()
        if (
            run is None
            or not token
            or not run.workspace_delegate_token_hash
            or run.workspace_delegate_token_expires_at is None
            or run.workspace_delegate_token_expires_at <= timezone.now()
            or not secrets.compare_digest(run.workspace_delegate_token_hash, hash_token(token))
        ):
            raise AgentRuntimeNotFound("Agent run not found.")
        return run


def get_internal_interaction_run(*, run_id: str, token: str) -> AgentDisplayRun:
    run = AgentDisplayRun.objects.filter(
        id=run_id,
        run_kind__in=invoke_agent_policy("interaction_run_kinds"),
    ).select_related("agent").first()
    valid = bool(
        run
        and token
        and run.interaction_token_hash
        and run.interaction_token_expires_at
        and run.interaction_token_expires_at > timezone.now()
        and secrets.compare_digest(run.interaction_token_hash, hash_token(token))
    )
    if not valid:
        raise AgentRuntimeNotFound("Agent run not found.")
    return run


def get_internal_context_run(*, run_id: str, token: str) -> AgentDisplayRun:
    run = AgentDisplayRun.objects.filter(
        id=run_id,
        run_kind=AgentDisplayRun.KIND_INVOCATION,
    ).select_related("agent", "consumer_project").first()
    valid = bool(
        run
        and token
        and run.context_token_hash
        and run.context_token_expires_at
        and run.context_token_expires_at > timezone.now()
        and secrets.compare_digest(run.context_token_hash, hash_token(token))
    )
    if not valid:
        raise AgentRuntimeNotFound("Agent run not found.")
    return run


@transaction.atomic
def report_agent_model_usage(
    *, run_id: str, token: str, data: dict[str, Any], source: str = "sdk",
    tenant_id: str = "", project_id: str = "",
) -> AgentModelUsage:
    run = get_internal_interaction_run(run_id=run_id, token=token)
    if run.run_kind != AgentDisplayRun.KIND_INVOCATION or run.status != AgentDisplayRun.STATUS_RUNNING:
        raise AgentRuntimeNotFound("Active Agent run not found.")
    if tenant_id and str(run.consumer_tenant_id) != str(tenant_id):
        raise AgentRuntimeNotFound("Active Agent run not found.")
    if project_id and str(run.consumer_project_id or "") != str(project_id):
        raise AgentRuntimeNotFound("Active Agent run not found.")
    event_id = str(data.get("event_id") or "").strip()
    model = str(data.get("model") or "").strip()
    if not event_id or len(event_id) > 128 or not model or len(model) > 128:
        raise exceptions.ValidationError("event_id and model are required.")
    try:
        numbers = {
            key: int(data.get(key) or 0)
            for key in (
                "input_tokens", "output_tokens", "cached_input_tokens",
                "reasoning_tokens", "context_window",
            )
        }
    except (TypeError, ValueError):
        raise exceptions.ValidationError("Token usage values must be integers.") from None
    if any(value < 0 for value in numbers.values()) or numbers["context_window"] <= 0:
        raise exceptions.ValidationError("Token usage must be non-negative and context_window must be positive.")
    turn_index = max(int(data.get("turn_index") or 1), 1)
    invocation = run.runtime_invocations.filter(turn_index=turn_index).order_by("-created_at").first()
    if invocation is None:
        raise AgentRuntimeNotFound("Agent invocation not found.")
    reported_profile_id = str(data.get("profile_id") or "").strip()
    expected_profile_id = str(invocation.execution_profile_id or "").strip()
    if reported_profile_id and expected_profile_id and reported_profile_id != expected_profile_id:
        raise exceptions.ValidationError(
            {"profile_id": "Reported usage does not belong to the execution profile selected for this Run turn."}
        )
    normalized = {
        "event_id": event_id,
        "turn_index": turn_index,
        "profile_id": (reported_profile_id or expected_profile_id)[:64],
        "model": model,
        **numbers,
        "primary": bool(data.get("primary", True)),
        "source": "gateway" if source == "gateway" else "sdk",
    }
    payload_hash = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    existing = AgentModelUsage.objects.select_for_update().filter(run=run, event_id=event_id).first()
    if existing is not None:
        if existing.payload_hash != payload_hash:
            raise AgentUsageConflict()
        return existing
    return AgentModelUsage.objects.create(
        run=run,
        invocation=invocation,
        payload_hash=payload_hash,
        **normalized,
    )


def serialize_run_interaction(interaction: AgentRunInteraction) -> dict[str, Any]:
    response = interaction.response_json if interaction.status == AgentRunInteraction.STATUS_ANSWERED else {}
    return {
        "id": str(interaction.id),
        "key": interaction.key,
        "kind": interaction.kind,
        "prompt": interaction.prompt,
        "choices": list(interaction.choices_json or []),
        "response": dict(response or {}),
        "status": interaction.status,
        "expires_at": interaction.expires_at.isoformat(),
        "answered_at": interaction.answered_at.isoformat() if interaction.answered_at else None,
    }


def _expire_interaction(interaction: AgentRunInteraction) -> AgentRunInteraction:
    if (
        interaction.status == AgentRunInteraction.STATUS_PENDING
        and interaction.expires_at <= timezone.now()
    ):
        interaction.status = AgentRunInteraction.STATUS_EXPIRED
        interaction.save(update_fields=["status", "updated_at"])
    return interaction


@transaction.atomic
def create_run_interaction(*, run_id: str, token: str, data: dict[str, Any]) -> dict[str, Any]:
    run = get_internal_interaction_run(run_id=run_id, token=token)
    if run.status != AgentDisplayRun.STATUS_RUNNING:
        raise AgentRuntimeNotFound("Agent run not found.")
    invoke_agent_policy("validate_run_interaction", run=run)
    key = str(data.get("key") or "").strip()
    prompt = str(data.get("prompt") or "").strip()
    kind = str(data.get("kind") or AgentRunInteraction.KIND_TEXT).strip().lower()
    if not key or len(key) > 128 or not prompt or len(prompt) > 4000:
        raise exceptions.ValidationError("Interaction key and prompt are required.")
    if kind not in {choice[0] for choice in AgentRunInteraction.KIND_CHOICES}:
        raise exceptions.ValidationError("Unsupported interaction kind.")
    choices: list[dict[str, str]] = []
    for raw in list(data.get("choices") or [])[:20]:
        if not isinstance(raw, dict):
            continue
        value = str(raw.get("value") or "").strip()[:128]
        label = str(raw.get("label") or value).strip()[:120]
        if value and label:
            choices.append({"value": value, "label": label})
    task_deadline_at = AgentExecutionTask.objects.filter(run=run).values_list("expires_at", flat=True).first()
    remaining = max(1, int((task_deadline_at - timezone.now()).total_seconds())) if task_deadline_at else 900
    timeout_seconds = min(max(int(data.get("timeout_seconds") or 300), 1), remaining)
    interaction = AgentRunInteraction.objects.select_for_update().filter(run=run, key=key).first()
    if interaction is None:
        interaction = AgentRunInteraction.objects.create(
            run=run,
            key=key,
            kind=kind,
            prompt=prompt,
            choices_json=choices,
            expires_at=timezone.now() + timedelta(seconds=timeout_seconds),
        )
        append_display_event(
            run=run,
            event_type="CUSTOM",
            payload={
                "name": "nexus.chat.input_required",
                "value": {
                    "interaction_id": str(interaction.id),
                    "key": key,
                    "kind": kind,
                    "choices": choices,
                    "expires_at": interaction.expires_at.isoformat(),
                },
            },
            visibility=str(data.get("visibility") or "public"),
        )
    interaction = _expire_interaction(interaction)
    try:
        task = run.execution_task
    except AgentExecutionTask.DoesNotExist:
        task = None
    if task is not None and interaction.status == AgentRunInteraction.STATUS_PENDING:
        task.status = AgentExecutionTask.STATUS_INPUT_REQUIRED
        task.save(update_fields=["status", "updated_at"])
    return serialize_run_interaction(interaction)


def get_run_interaction(*, run_id: str, interaction_id: str, token: str) -> dict[str, Any]:
    run = get_internal_interaction_run(run_id=run_id, token=token)
    interaction = run.interactions.filter(id=interaction_id).first()
    if interaction is None:
        raise AgentRuntimeNotFound("Interaction not found.")
    return serialize_run_interaction(_expire_interaction(interaction))


def get_run_context(*, run_id: str, token: str, limit: int = 200) -> dict[str, Any]:
    """Return one read-only, Run-scoped context snapshot to the executing SDK."""

    run = get_internal_context_run(run_id=run_id, token=token)
    requested = min(max(int(limit or 200), 1), 200)
    messages = list(run.messages.order_by("-sequence")[:requested])
    messages.reverse()
    task = getattr(run, "execution_task", None)
    turn_index = max(int((task.request_json or {}).get("turn_index") or 1), 1) if task else 1
    snapshot = dict(run.project_context_snapshot or {})
    return {
        "run": {
            "id": str(run.id),
            "turn_index": turn_index,
            "tool_name": task.tool_name if task else "",
            "status": run.status,
        },
        "project": {
            "id": str(snapshot.get("project_id") or ""),
            "name": str(snapshot.get("project_name") or ""),
            "instructions": str(snapshot.get("instructions_markdown") or ""),
            "revision": int(snapshot.get("instructions_revision") or 0),
            "captured_at": snapshot.get("captured_at"),
        },
        "messages": [
            {
                "id": str(message.id),
                "sequence": message.sequence,
                "turn_index": message.turn_index,
                "role": message.role,
                "content": message.content,
                "content_blocks": list(message.content_blocks or []),
                "created_at": message.created_at.isoformat(),
            }
            for message in messages
        ],
        "resources": {
            "computer_attached": bool(run.computer_binding_id),
            "mobile_attached": bool(run.mobile_binding_id),
            "input_files": [
                {
                    "id": str(item.id), "name": item.name, "content_type": item.content_type,
                    "size_bytes": item.size_bytes, "source_kind": item.source_kind,
                }
                for item in run.file_transfers.filter(direction="input", state="ready").order_by("created_at")[:8]
            ],
        },
    }


def get_run_checkpoint(*, run_id: str, token: str) -> dict[str, Any]:
    run = get_internal_interaction_run(run_id=run_id, token=token)
    try:
        checkpoint = run.checkpoint
    except AgentRunCheckpoint.DoesNotExist:
        return {}
    return {
        "stage": checkpoint.stage,
        "data": dict(checkpoint.data_json or {}),
        "revision": checkpoint.revision,
        "updated_at": checkpoint.updated_at.isoformat(),
    }


@transaction.atomic
def save_run_checkpoint(*, run_id: str, token: str, data: dict[str, Any]) -> dict[str, Any]:
    run = get_internal_interaction_run(run_id=run_id, token=token)
    if run.status != AgentDisplayRun.STATUS_RUNNING:
        raise AgentRuntimeNotFound("Agent run not found.")
    stage = str(data.get("stage") or "").strip()
    checkpoint_data = data.get("data") or {}
    if not stage or len(stage) > 128 or not isinstance(checkpoint_data, dict):
        raise exceptions.ValidationError("A valid checkpoint stage and JSON data are required.")
    if len(json.dumps(checkpoint_data, ensure_ascii=False).encode("utf-8")) > 65536:
        raise exceptions.ValidationError("Checkpoint data cannot exceed 64 KiB.")
    checkpoint = AgentRunCheckpoint.objects.select_for_update().filter(run=run).first()
    expected = data.get("expected_revision")
    if checkpoint is None:
        if expected not in {None, 0, 1}:
            raise AgentRuntimeOperationConflict("Checkpoint revision conflict.")
        checkpoint = AgentRunCheckpoint.objects.create(run=run, stage=stage, data_json=checkpoint_data)
    else:
        if expected is not None and int(expected) != checkpoint.revision:
            raise AgentRuntimeOperationConflict("Checkpoint revision conflict.")
        checkpoint.stage = stage
        checkpoint.data_json = checkpoint_data
        checkpoint.revision += 1
        checkpoint.save(update_fields=["stage", "data_json", "revision", "updated_at"])
    return {
        "stage": checkpoint.stage,
        "data": dict(checkpoint.data_json or {}),
        "revision": checkpoint.revision,
        "updated_at": checkpoint.updated_at.isoformat(),
    }


def create_run_display_asset(*, run_id: str, token: str, data: dict[str, Any]) -> dict[str, Any]:
    # Do not keep the Run lookup transaction open while writing the image to
    # object storage.  AG-UI events arrive concurrently and SQLite otherwise
    # deadlocks when both requests try to upgrade a read transaction to a
    # write; PostgreSQL also benefits from the shorter lock lifetime.
    run = get_internal_interaction_run(run_id=run_id, token=token)
    if run.status != AgentDisplayRun.STATUS_RUNNING:
        raise AgentRuntimeNotFound("Agent run not found.")
    content_type = str(data.get("content_type") or "").lower()
    if content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise exceptions.ValidationError("Display Asset must be PNG, JPEG or WebP.")
    encoded_content = data.get("content_base64")
    if not isinstance(encoded_content, str) or len(encoded_content) > ((2 * 1024 * 1024 + 2) // 3) * 4:
        raise exceptions.ValidationError("Display Asset exceeds the encoded byte limit.")
    try:
        content = base64.b64decode(encoded_content, validate=True)
    except (ValueError, TypeError):
        raise exceptions.ValidationError("Display Asset content is invalid.") from None
    if not content or len(content) > 2 * 1024 * 1024:
        raise exceptions.ValidationError("Display Asset must be between 1 byte and 2 MiB.")
    from apps.gateway.image_media import validate_image
    _, dimensions = validate_image(content, content_type)
    digest = hashlib.sha256(content).hexdigest()
    supplied_digest = str(data.get("sha256") or "")
    if supplied_digest and not secrets.compare_digest(digest, supplied_digest):
        raise exceptions.ValidationError("Display Asset checksum does not match.")
    extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[content_type]
    asset = AgentDisplayAsset(
        run=run,
        content_type=content_type,
        size_bytes=len(content),
        sha256=digest,
        width=dimensions[0],
        height=dimensions[1],
    )
    asset.file.save("frame" + extension, ContentFile(content), save=True)
    # Browser frames share this endpoint with output.image(). Only an explicit
    # nexus.image.created event promotes a display asset to a reusable output.
    asset_url = invoke_agent_policy("run_display_asset_url", run=run, asset=asset)
    return {
        "id": str(asset.id),
        "url": asset_url,
        "content_type": asset.content_type,
        "size_bytes": asset.size_bytes,
        "width": asset.width,
        "height": asset.height,
    }


def get_workspace_delegate_run(*, run_id: str, token: str, scope: str) -> AgentDisplayRun:
    run = (
        AgentDisplayRun.objects.filter(
            id=run_id,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            status=AgentDisplayRun.STATUS_RUNNING,
        )
        .select_related("agent", "consumer_tenant", "consumer_project", "computer_binding__connection")
        .first()
    )
    token_matches = bool(
        run
        and token
        and run.workspace_delegate_token_hash
        and secrets.compare_digest(run.workspace_delegate_token_hash, hash_token(token))
    )
    if (
        not token_matches
        or run.workspace_delegate_token_expires_at is None
        or run.workspace_delegate_token_expires_at <= timezone.now()
        or scope not in effective_workspace_capabilities_for_run(run)
    ):
        raise AgentRuntimeNotFound("Agent run not found.")
    return run


def _delegate_connection(*, run: AgentDisplayRun, connection_id: str):
    from apps.workspaces.models import WorkspaceConnection

    connection = WorkspaceConnection.objects.filter(
        id=connection_id,
        tenant=run.consumer_tenant,
        owner_subject_hash=run.caller_subject_hash,
    ).exclude(status=SoftDeleteModel.STATUS_DELETED).first()
    if connection is None:
        raise AgentRuntimeNotFound("Workspace connection not found.")
    if connection.connection_type != "runtime":
        raise AgentRuntimeNotFound("Workspace connection not found.")
    if run.consumer_project_id and connection.project_id not in {None, run.consumer_project_id}:
        raise AgentRuntimeNotFound("Workspace connection not found.")
    return connection


_DELEGATE_SENSITIVE_METADATA_KEYS = {
    "api_key", "apikey", "authorization", "cookie", "credential", "password",
    "private_key", "secret", "token",
}


def _clean_delegate_metadata(value):
    if isinstance(value, dict):
        return {
            str(key): _clean_delegate_metadata(item)
            for key, item in value.items()
            if str(key).lower() not in _DELEGATE_SENSITIVE_METADATA_KEYS
        }
    if isinstance(value, list):
        return [_clean_delegate_metadata(item) for item in value[:100]]
    if isinstance(value, str):
        return value[:4096]
    return value


def serialize_delegate_connection(connection) -> dict[str, Any]:
    runtime = None
    if connection.connection_type == "runtime":
        try:
            device = connection.runtime_device
        except Exception:
            device = None
        if device is not None:
            runtime = {
                "device_id": str(device.id),
                "online": device.online,
                "platform": device.platform,
                "capabilities": device.capabilities,
                "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
            }
    return {
        "id": str(connection.id),
        "name": connection.name,
        "connection_type": connection.connection_type,
        "workspace_root": connection.workspace_root,
        "runtime": runtime,
        "status": connection.status,
        "last_test_status": connection.last_test_status,
        "last_test_error": connection.last_test_error,
        "last_test_at": connection.last_test_at.isoformat() if connection.last_test_at else None,
        "metadata": _clean_delegate_metadata(connection.metadata or {}),
        "created_at": connection.created_at.isoformat(),
        "updated_at": connection.updated_at.isoformat(),
    }


def list_delegate_connections(*, run_id: str, token: str) -> dict[str, Any]:
    from apps.workspaces.models import WorkspaceConnection

    run = get_workspace_delegate_run(run_id=run_id, token=token, scope="connection.list")
    queryset = WorkspaceConnection.objects.filter(
        tenant=run.consumer_tenant,
        owner_subject_hash=run.caller_subject_hash,
    ).filter(connection_type=WorkspaceConnection.TYPE_RUNTIME).exclude(status=SoftDeleteModel.STATUS_DELETED).select_related("runtime_device")
    if run.consumer_project_id:
        queryset = queryset.filter(Q(project__isnull=True) | Q(project_id=run.consumer_project_id))
    return {"items": [serialize_delegate_connection(item) for item in queryset.order_by("-created_at")]}


@transaction.atomic
def create_delegate_connection(*, run_id: str, token: str, data: dict[str, Any]) -> dict[str, Any]:
    from apps.workspaces.computer_runtime import LegacySSHDisabled

    get_workspace_delegate_run(run_id=run_id, token=token, scope="connection.create")
    raise LegacySSHDisabled("Pair a Computer Runtime from the caller's Computer page.")


def get_delegate_connection(*, run_id: str, token: str, connection_id: str) -> dict[str, Any]:
    run = get_workspace_delegate_run(run_id=run_id, token=token, scope="connection.list")
    return serialize_delegate_connection(_delegate_connection(run=run, connection_id=connection_id))


@transaction.atomic
def update_delegate_connection(*, run_id: str, token: str, connection_id: str, data: dict[str, Any]) -> dict[str, Any]:
    run = get_workspace_delegate_run(run_id=run_id, token=token, scope="connection.update")
    connection = _delegate_connection(run=run, connection_id=connection_id)
    if connection.connection_type != "runtime":
        from apps.workspaces.computer_runtime import LegacySSHDisabled

        raise LegacySSHDisabled()
    changed: list[str] = []
    for field in ["name", "workspace_root"]:
        if field in data:
            setattr(connection, field, data[field])
            changed.append(field)
    if "metadata" in data:
        connection.metadata = _clean_delegate_metadata(data["metadata"])
        changed.append("metadata")
    if changed:
        connection.save(update_fields=changed + ["updated_at"])
    return serialize_delegate_connection(connection)


@transaction.atomic
def delete_delegate_connection(*, run_id: str, token: str, connection_id: str) -> None:
    from apps.workspaces.models import WorkspaceTerminalSession

    run = get_workspace_delegate_run(run_id=run_id, token=token, scope="connection.delete")
    connection = _delegate_connection(run=run, connection_id=connection_id)
    active_binding = AgentComputerBinding.objects.filter(
        connection=connection,
        status=SoftDeleteModel.STATUS_ACTIVE,
    ).exists()
    active_terminal = WorkspaceTerminalSession.objects.filter(
        connection=connection,
        status__in=[WorkspaceTerminalSession.STATUS_CREATED, WorkspaceTerminalSession.STATUS_ACTIVE],
    ).exists()
    if active_binding or active_terminal:
        raise AgentRuntimeOperationConflict("Disconnect active Agent bindings and terminals before deleting the Computer.")
    connection.delete()


def _test_delegate_connection(connection) -> dict[str, Any]:
    from apps.workspaces.models import WorkspaceConnection
    from apps.workspaces.runner_dispatch import workspace_runner_for, workspace_runner_name_for

    result = workspace_runner_for(connection).test(connection=connection)
    checked_at = timezone.now()
    connection.last_test_status = WorkspaceConnection.TEST_SUCCEEDED if result.ok else WorkspaceConnection.TEST_FAILED
    connection.last_test_error = result.error[:1024]
    connection.last_test_at = checked_at
    history = list((connection.metadata or {}).get("health_history") or [])
    history.insert(0, {
        "checked_at": checked_at.isoformat(),
        "status": connection.last_test_status,
        "error": result.error[:1024],
        "facts": result.facts,
    })
    connection.metadata = {
        **_clean_delegate_metadata(connection.metadata or {}),
        "last_facts": _clean_delegate_metadata(result.facts),
        "last_runner": workspace_runner_name_for(connection),
        "health_history": history[:10],
    }
    connection.save(update_fields=["last_test_status", "last_test_error", "last_test_at", "metadata", "updated_at"])
    return {
        "status": connection.last_test_status,
        "facts": _clean_delegate_metadata(result.facts),
        "checks": _clean_delegate_metadata(result.checks),
        "error": result.error[:1024],
    }


def test_delegate_connection(*, run_id: str, token: str, connection_id: str) -> dict[str, Any]:
    run = get_workspace_delegate_run(run_id=run_id, token=token, scope="connection.test")
    return _test_delegate_connection(_delegate_connection(run=run, connection_id=connection_id))


def validate_delegate_connection(*, run_id: str, token: str, data: dict[str, Any]) -> dict[str, Any]:
    from apps.workspaces.computer_runtime import LegacySSHDisabled

    get_workspace_delegate_run(run_id=run_id, token=token, scope="connection.test")
    raise LegacySSHDisabled()


def list_delegate_bindings(*, run_id: str, token: str) -> dict[str, Any]:
    run = get_workspace_delegate_run(run_id=run_id, token=token, scope="connection.bind")
    items = AgentComputerBinding.objects.filter(
        tenant=run.consumer_tenant,
        agent=run.agent,
        caller_subject_hash=run.caller_subject_hash,
    ).exclude(status=SoftDeleteModel.STATUS_DELETED).select_related("connection")
    return {"items": [{
        "id": str(item.id),
        "connection_id": str(item.connection_id),
        "connection_name": item.connection.name,
        "is_default": item.is_default,
        "status": item.status,
    } for item in items]}


@transaction.atomic
def bind_delegate_connection(
    *, run_id: str, token: str, connection_id: str, make_default: bool = True
) -> dict[str, Any]:
    run = get_workspace_delegate_run(run_id=run_id, token=token, scope="connection.bind")
    run = (
        AgentDisplayRun.objects.select_for_update(of=("self",))
        .select_related("agent", "consumer_tenant", "consumer_project")
        .get(id=run.id)
    )
    if hasattr(run, "terminal_session"):
        raise AgentRuntimeOperationConflict("The Run Computer cannot change after its Terminal starts.")
    if run.computer_revision and (not run.computer_binding_id or str(run.computer_binding.connection_id) != str(connection_id)):
        raise AgentRuntimeOperationConflict("Change this Run's Computer from Private Display between turns.")
    connection = _delegate_connection(run=run, connection_id=connection_id)
    from apps.workspaces.computer_runtime import ensure_runtime_connection

    ensure_runtime_connection(connection, operation="workspace.test")
    if make_default:
        AgentComputerBinding.objects.filter(
            tenant=run.consumer_tenant,
            agent=run.agent,
            caller_subject_hash=run.caller_subject_hash,
            is_default=True,
            status=SoftDeleteModel.STATUS_ACTIVE,
        ).update(is_default=False)
    binding, _ = AgentComputerBinding.objects.update_or_create(
        tenant=run.consumer_tenant,
        agent=run.agent,
        connection=connection,
        caller_subject_hash=run.caller_subject_hash,
        defaults={
            "project": run.consumer_project,
            "caller_principal_type": run.caller_principal_type,
            "is_default": make_default,
            "status": SoftDeleteModel.STATUS_ACTIVE,
            "deleted_at": None,
        },
    )
    base_root = str(connection.workspace_root or "~/.nexus").rstrip("/")
    agent_root = posixpath.join(base_root, "agents", str(run.agent_id))
    run.computer_binding = binding
    run.workspace_root = posixpath.join(agent_root, "workspace")
    run.output_root = posixpath.join(agent_root, "runs", str(run.id), "outputs")
    if run.computer_revision:
        run.output_root = posixpath.join(agent_root, "runs", str(run.id), "computers", str(run.computer_revision), "outputs")
    run.save(update_fields=["computer_binding", "workspace_root", "output_root", "updated_at"])
    internal_root = f"/api/v1/internal/agent-runs/{run.id}"
    return {
        "id": str(binding.id),
        "connection_id": str(connection.id),
        "is_default": binding.is_default,
        "computer_enabled": True,
        "workspace_root": run.workspace_root,
        "output_root": run.output_root,
        "workspace_path": f"{internal_root}/workspace/",
        "terminal_path": f"{internal_root}/terminal/",
    }


def invocation_workspace_root(*, run: AgentDisplayRun, root_kind: str) -> str:
    if root_kind == "output":
        return run.output_root
    return run.workspace_root


def invocation_workspace_relative_path(*, run: AgentDisplayRun, path: str, root_kind: str) -> str:
    from apps.workspaces.execution import resolve_workspace_relative_path

    if root_kind == "output":
        return resolve_workspace_relative_path(path=path)
    return resolve_workspace_relative_path(base=run.workspace_cwd or ".", path=path)


def invocation_runtime_command_context(*, run: AgentDisplayRun, idempotency_key: str = "") -> dict[str, str]:
    return {
        "display_run_id": str(run.id),
        "caller_subject_hash": run.caller_subject_hash,
        "idempotency_key": idempotency_key,
    }


def list_invocation_workspace_files(*, run_id: str, token: str, path: str = "", root_kind: str = "workspace") -> dict[str, Any]:
    from apps.workspaces.execution import ensure_workspace_directory, list_workspace_files

    run = get_workspace_delegate_run(run_id=run_id, token=token, scope="files.list")
    if run.computer_binding is None:
        raise AgentRuntimeError("Computer is not enabled for this run.")
    ensure_workspace_directory(
        connection=run.computer_binding.connection,
        path=invocation_workspace_root(run=run, root_kind=root_kind),
        runtime_context=invocation_runtime_command_context(run=run),
    )
    return list_workspace_files(
        connection=run.computer_binding.connection,
        root=invocation_workspace_root(run=run, root_kind=root_kind),
        path=invocation_workspace_relative_path(run=run, path=path or ".", root_kind=root_kind),
        runtime_context=invocation_runtime_command_context(run=run),
    )


def read_invocation_workspace_file(*, run_id: str, token: str, path: str, root_kind: str = "workspace") -> dict[str, Any]:
    from apps.workspaces.execution import read_workspace_file

    run = get_workspace_delegate_run(run_id=run_id, token=token, scope="files.read")
    if run.computer_binding is None:
        raise AgentRuntimeError("Computer is not enabled for this run.")
    return read_workspace_file(
        connection=run.computer_binding.connection,
        root=invocation_workspace_root(run=run, root_kind=root_kind),
        path=invocation_workspace_relative_path(run=run, path=path, root_kind=root_kind),
        runtime_context=invocation_runtime_command_context(run=run),
    )


def write_invocation_workspace_file(*, run_id: str, token: str, path: str, content: str, root_kind: str = "workspace", idempotency_key: str = "") -> dict[str, Any]:
    from apps.workspaces.execution import write_workspace_file

    run = get_workspace_delegate_run(run_id=run_id, token=token, scope="files.write")
    require_open_invocation_run(run)
    if run.computer_binding is None:
        raise AgentRuntimeError("Computer is not enabled for this run.")
    result = write_workspace_file(
        connection=run.computer_binding.connection,
        root=invocation_workspace_root(run=run, root_kind=root_kind),
        path=invocation_workspace_relative_path(run=run, path=path, root_kind=root_kind),
        content=content,
        runtime_context=invocation_runtime_command_context(run=run, idempotency_key=idempotency_key),
    )
    if root_kind == "output":
        # This request has already authenticated the caller-bound delegate and
        # carries the exact bytes accepted by the Workspace. Snapshot here
        # instead of depending solely on a later cross-SFTP visibility check.
        snapshot_invocation_output_content(run=run, path=path, content=content)
    return result


def run_invocation_terminal_command(
    *,
    run_id: str,
    token: str,
    command: str,
    cwd: str = ".",
    timeout_seconds: int | None = None,
    display: bool = True,
    idempotency_key: str = "",
) -> dict[str, Any]:
    from apps.workspaces.models import WorkspaceTerminalSession
    from apps.workspaces.execution import ensure_workspace_directory, run_workspace_command

    if not isinstance(display, bool):
        raise exceptions.ValidationError({"display": "Must be a boolean."})
    # Snapshot the working directory under a short row lock, then release the
    # lock before the remote command runs. A concurrent folder switch can
    # therefore complete immediately, while this already-dispatched command
    # keeps the directory that was current at dispatch time.
    with transaction.atomic():
        delegated = get_workspace_delegate_run(
            run_id=run_id,
            token=token,
            scope="command.execute",
        )
        require_open_invocation_run(delegated)
        run = (
            AgentDisplayRun.objects.select_for_update(of=("self",))
            .select_related("computer_binding__connection")
            .get(id=delegated.id)
        )
        require_open_invocation_run(run)
        if run.computer_binding is None:
            raise AgentRuntimeError("Computer is not enabled for this run.")
        connection = run.computer_binding.connection
        workspace_root = run.workspace_root
        resolved_cwd = invocation_workspace_relative_path(
            run=run,
            path=cwd or ".",
            root_kind="workspace",
        )
        session = None
        if display:
            session, _ = WorkspaceTerminalSession.objects.get_or_create(
                display_run=run,
                defaults={
                    "tenant": run.consumer_tenant,
                    "project": run.consumer_project,
                    "connection": connection,
                    "computer_binding": run.computer_binding,
                    "session_kind": WorkspaceTerminalSession.KIND_AGENT_RUN,
                    "caller_subject_hash": run.caller_subject_hash,
                    "authorized_root": workspace_root,
                    "output_root": run.output_root,
                    "viewer_mode": "read_only",
                    "status": WorkspaceTerminalSession.STATUS_ACTIVE,
                    "started_at": timezone.now(),
                },
            )
    command_id = (hash_token(idempotency_key) if idempotency_key else uuid.uuid4().hex)[:64]
    started = time.monotonic()
    try:
        ensure_workspace_directory(
            connection=connection,
            path=workspace_root,
            runtime_context=invocation_runtime_command_context(run=run),
        )
        result = run_workspace_command(
            connection=connection,
            root=workspace_root,
            cwd=resolved_cwd,
            command=command,
            timeout_seconds=timeout_seconds,
            runtime_context=invocation_runtime_command_context(
                run=run,
                idempotency_key=idempotency_key or f"agent-run:{run.id}:terminal:{command_id}",
            ),
        )
        terminal_result = dict(result or {})
        if session is not None:
            with transaction.atomic():
                locked_session = WorkspaceTerminalSession.objects.select_for_update().get(
                    id=session.id
                )
                _append_terminal_transcript(
                    session=locked_session,
                    command_id=command_id,
                    command=command,
                    result=terminal_result,
                )
                locked_session.status = WorkspaceTerminalSession.STATUS_ACTIVE
                locked_session.last_error = ""
                locked_session.save(
                    update_fields=["status", "last_error", "updated_at"]
                )
        return {
            **terminal_result,
            "command_id": command_id,
            "duration_ms": max(int((time.monotonic() - started) * 1000), 0),
            "displayed": display,
        }
    except Exception as exc:
        safe_error = redact_terminal_text(str(exc))
        if session is not None:
            with transaction.atomic():
                locked_session = WorkspaceTerminalSession.objects.select_for_update().get(
                    id=session.id
                )
                locked_session.status = WorkspaceTerminalSession.STATUS_FAILED
                locked_session.last_error = safe_error[:1024]
                locked_session.save(
                    update_fields=["status", "last_error", "updated_at"]
                )
                _append_terminal_transcript(
                    session=locked_session,
                    command_id=command_id,
                    command=command,
                    result={"stderr": safe_error, "exit_code": -1},
                )
        raise


def _append_terminal_transcript(*, session, command_id: str, command: str, result: dict[str, Any]) -> None:
    from apps.workspaces.models import WorkspaceTerminalTranscript
    from .services import sanitize_display_payload

    existing_bytes = sum(len(value.encode("utf-8")) for value in session.transcript.values_list("data", flat=True))
    max_bytes = 2 * 1024 * 1024
    rows = [
        (WorkspaceTerminalTranscript.KIND_COMMAND, command, None),
        (WorkspaceTerminalTranscript.KIND_STDOUT, str(result.get("stdout") or ""), None),
        (WorkspaceTerminalTranscript.KIND_STDERR, str(result.get("stderr") or ""), None),
        (WorkspaceTerminalTranscript.KIND_EXIT, "", int(result.get("exit_code") or 0)),
    ]
    next_seq = session.transcript.order_by("-seq").values_list("seq", flat=True).first() or 0
    for kind, raw_data, exit_code in rows:
        if not raw_data and kind not in {WorkspaceTerminalTranscript.KIND_EXIT}:
            continue
        safe_data = redact_terminal_text(raw_data)
        safe_data = str(sanitize_display_payload({"data": safe_data}).get("data") or "")
        if existing_bytes + len(safe_data.encode("utf-8")) > max_bytes:
            safe_data = "[terminal transcript truncated]"
            kind = WorkspaceTerminalTranscript.KIND_SYSTEM
        next_seq += 1
        WorkspaceTerminalTranscript.objects.create(
            session=session,
            seq=next_seq,
            kind=kind,
            command_id=command_id,
            data=safe_data,
            exit_code=exit_code,
        )
        existing_bytes += len(safe_data.encode("utf-8"))
        if kind == WorkspaceTerminalTranscript.KIND_SYSTEM:
            break


def redact_terminal_text(value: Any) -> str:
    from .services import redact_payload

    text = str(redact_payload(str(value))[0])
    text = _TERMINAL_PEM_RE.sub("[REDACTED PRIVATE KEY]", text)
    text = _TERMINAL_SECRET_ASSIGNMENT_RE.sub(r"\1\2[REDACTED]", text)
    return _TERMINAL_SECRET_FLAG_RE.sub(r"\1[REDACTED]", text)


def invocation_terminal_status(*, run_id: str, token: str) -> dict[str, Any]:
    run = get_workspace_delegate_run(run_id=run_id, token=token, scope="command.execute")
    session = getattr(run, "terminal_session", None)
    return {
        "enabled": bool(run.computer_binding_id),
        "status": session.status if session else "not_started",
        "viewer_mode": "read_only",
    }


def require_open_invocation_run(run: AgentDisplayRun) -> None:
    if run.status != AgentDisplayRun.STATUS_RUNNING:
        raise AgentRuntimeError("Agent invocation Run is already closed.")


def serialize_invocation_memory_item(item: AgentMemoryItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "kind": item.memory_type,
        "text": item.content_text,
        "content": item.content_json,
        "scope": item.scope,
        "confidence": str(item.confidence),
        "sensitivity": item.sensitivity_level,
        "consent": item.consent_status,
        "license": item.license_status,
        "revision": item.revision,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def invocation_memory_owner_filter(run: AgentDisplayRun) -> Q:
    consumer_tenant_id = run.consumer_tenant_id or run.tenant_id
    consumer_project_id = run.consumer_project_id or run.agent.project_id
    return Q(
        scope=AgentMemoryItem.SCOPE_CALLER,
        caller_subject_hash=run.caller_subject_hash,
        tenant_id=consumer_tenant_id,
        project_id=consumer_project_id,
    )


def recall_invocation_memory(*, run_id: str, token: str, limit: int = 50) -> dict[str, Any]:
    run = get_internal_invocation_run_or_delegate(run_id=run_id, token=token)
    limit = min(max(int(limit or 50), 1), 200)
    items = (
        AgentMemoryItem.objects.filter(
            agent=run.agent,
            consent_status=AgentMemoryItem.CONSENT_APPROVED,
        )
        .filter(
            Q(scope=AgentMemoryItem.SCOPE_AGENT_GLOBAL)
            | invocation_memory_owner_filter(run)
        )
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .order_by("-created_at")[:limit]
    )
    return {"items": [serialize_invocation_memory_item(item) for item in items]}


def expected_memory_revision(data: dict[str, Any]) -> int:
    value = data.get("expected_revision")
    if isinstance(value, bool):
        raise exceptions.ValidationError({"expected_revision": ["A positive integer is required."]})
    try:
        revision = int(value)
    except (TypeError, ValueError):
        raise exceptions.ValidationError({"expected_revision": ["A positive integer is required."]})
    if revision < 1:
        raise exceptions.ValidationError({"expected_revision": ["A positive integer is required."]})
    return revision


def lock_invocation_memory_run(*, run_id: str, token: str) -> AgentDisplayRun:
    validated = get_internal_invocation_run_or_delegate(run_id=run_id, token=token)
    run = (
        AgentDisplayRun.objects.select_for_update(of=("self",))
        .select_related("agent", "consumer_tenant", "consumer_project")
        .get(id=validated.id)
    )
    if run.status != AgentDisplayRun.STATUS_RUNNING:
        raise AgentMemoryRunClosed()
    return run


def get_mutable_invocation_memory(*, run: AgentDisplayRun, memory_id: str) -> AgentMemoryItem:
    item = (
        AgentMemoryItem.objects.select_for_update()
        .filter(id=memory_id, agent=run.agent)
        .filter(invocation_memory_owner_filter(run))
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .first()
    )
    if item is None:
        raise AgentRuntimeNotFound("Agent Memory not found.")
    return item


def validate_memory_revision(*, item: AgentMemoryItem, expected_revision: int) -> None:
    if item.revision != expected_revision:
        raise AgentMemoryRevisionConflict(current_revision=item.revision)


def append_memory_audit_event(
    *,
    run: AgentDisplayRun,
    item: AgentMemoryItem,
    name: str,
    changed_fields: list[str],
) -> None:
    event = append_display_event(
        run=run,
        event_type="CUSTOM",
        payload={
            "name": name,
            "value": {
                "memory_id": str(item.id),
                "revision": item.revision,
                "changed_fields": changed_fields,
                "run_id": str(run.id),
            },
        },
        visibility="private",
    )
    item.source_event_ids = [*list(item.source_event_ids or []), str(event.id)]
    item.save(update_fields=["source_event_ids", "updated_at"])


@transaction.atomic
def update_invocation_memory(
    *,
    run_id: str,
    memory_id: str,
    token: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    allowed_fields = {
        "expected_revision",
        "memory_type",
        "content_text",
        "content_json",
        "confidence",
        "sensitivity_level",
        "consent_status",
        "license_status",
    }
    unknown_fields = sorted(set(data) - allowed_fields)
    if unknown_fields:
        raise exceptions.ValidationError({"fields": ["Unsupported Memory fields were provided."]})
    update_fields = set(data) - {"expected_revision"}
    if not update_fields:
        raise exceptions.ValidationError({"fields": ["At least one Memory field is required."]})

    run = lock_invocation_memory_run(run_id=run_id, token=token)
    item = get_mutable_invocation_memory(run=run, memory_id=memory_id)
    validate_memory_revision(item=item, expected_revision=expected_memory_revision(data))

    values: dict[str, Any] = {}
    if "memory_type" in update_fields:
        memory_type = str(data.get("memory_type") or "")
        if memory_type not in {choice[0] for choice in AgentMemoryItem.TYPE_CHOICES}:
            raise exceptions.ValidationError({"memory_type": ["Unsupported Memory kind."]})
        values["memory_type"] = memory_type
    if "content_text" in update_fields:
        safe = sanitize_display_payload({"content_text": str(data.get("content_text") or "")})
        values["content_text"] = str(safe.get("content_text") or "").strip()
    if "content_json" in update_fields:
        content_json = data.get("content_json")
        if not isinstance(content_json, dict):
            raise exceptions.ValidationError({"content_json": ["A JSON object is required."]})
        safe = sanitize_display_payload({"content_json": content_json})
        values["content_json"] = safe.get("content_json") if isinstance(safe.get("content_json"), dict) else {}
    if "confidence" in update_fields:
        try:
            confidence = Decimal(str(data.get("confidence")))
        except Exception:
            raise exceptions.ValidationError({"confidence": ["A number between 0 and 1 is required."]})
        if confidence < Decimal("0") or confidence > Decimal("1"):
            raise exceptions.ValidationError({"confidence": ["A number between 0 and 1 is required."]})
        values["confidence"] = confidence
    for field, choices in (
        ("sensitivity_level", AgentMemoryItem.SENSITIVITY_CHOICES),
        ("consent_status", AgentMemoryItem.CONSENT_CHOICES),
        ("license_status", AgentMemoryItem.LICENSE_CHOICES),
    ):
        if field in update_fields:
            value = str(data.get(field) or "")
            if value not in {choice[0] for choice in choices}:
                raise exceptions.ValidationError({field: ["Unsupported Memory value."]})
            values[field] = value

    content_text = values.get("content_text", item.content_text)
    content_json = values.get("content_json", item.content_json)
    if not str(content_text or "").strip() and not content_json:
        raise exceptions.ValidationError("Memory requires text or data.")

    changed_model_fields = [field for field, value in values.items() if getattr(item, field) != value]
    if not changed_model_fields:
        return serialize_invocation_memory_item(item)
    for field in changed_model_fields:
        setattr(item, field, values[field])
    item.revision += 1
    item.save(update_fields=[*changed_model_fields, "revision", "updated_at"])

    public_field_names = {
        "memory_type": "kind",
        "content_text": "text",
        "content_json": "data",
        "confidence": "confidence",
        "sensitivity_level": "sensitivity",
        "consent_status": "consent",
        "license_status": "license",
    }
    append_memory_audit_event(
        run=run,
        item=item,
        name=AGUI_CUSTOM_MEMORY_ITEM_UPDATED,
        changed_fields=sorted(public_field_names[field] for field in changed_model_fields),
    )
    return serialize_invocation_memory_item(item)


@transaction.atomic
def delete_invocation_memory(
    *,
    run_id: str,
    memory_id: str,
    token: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    unknown_fields = sorted(set(data) - {"expected_revision"})
    if unknown_fields:
        raise exceptions.ValidationError({"fields": ["Unsupported Memory fields were provided."]})
    run = lock_invocation_memory_run(run_id=run_id, token=token)
    item = get_mutable_invocation_memory(run=run, memory_id=memory_id)
    validate_memory_revision(item=item, expected_revision=expected_memory_revision(data))
    item.status = SoftDeleteModel.STATUS_DELETED
    item.deleted_at = timezone.now()
    item.revision += 1
    item.save(update_fields=["status", "deleted_at", "revision", "updated_at"])
    append_memory_audit_event(
        run=run,
        item=item,
        name=AGUI_CUSTOM_MEMORY_ITEM_DELETED,
        changed_fields=["status"],
    )
    return {"id": str(item.id), "deleted": True, "revision": item.revision}




def runtime_actor_user(*, request, api_key):
    return invoke_runtime_policy("runtime_actor_user", request=request, api_key=api_key)


def resolve_image(*, agent: Agent, image_id=None) -> AgentRuntimeImage:
    queryset = AgentRuntimeImage.objects.filter(tenant=agent.tenant, agent=agent, status=SoftDeleteModel.STATUS_ACTIVE)
    if image_id:
        image = queryset.filter(id=image_id).first()
    elif agent.current_image_id:
        image = queryset.filter(id=agent.current_image_id).first()
    else:
        image = queryset.order_by("-created_at").first()
    if image is None:
        raise AgentRuntimeNotFound("Agent runtime image not found.")
    return image


def resolve_runtime_workspace_connection(*, agent: Agent, workspace_connection_id=None):
    if not workspace_connection_id:
        return None
    from apps.workspaces.models import WorkspaceConnection

    connection = (
        WorkspaceConnection.objects.filter(
            tenant=agent.tenant,
            id=workspace_connection_id,
            status=SoftDeleteModel.STATUS_ACTIVE,
        )
        .select_related("project")
        .first()
    )
    if connection is None:
        raise exceptions.NotFound("Workspace connection not found.")
    if connection.project_id and agent.project_id and connection.project_id != agent.project_id:
        raise exceptions.PermissionDenied("Workspace connection does not belong to this agent project.")
    return connection


def normalize_workspace_root(value: str) -> str:
    root = str(value or ".").strip().replace("\\", "/")
    return root or "."


def resolve_version(*, agent: Agent, version_value: str) -> AgentVersion | None:
    if version_value:
        version = agent.versions.filter(version=version_value).first()
        if version is None:
            raise AgentRuntimeNotFound("Agent version not found.")
        return version
    if agent.current_version:
        return agent.versions.filter(version=agent.current_version).first()
    return agent.versions.order_by("-created_at").first()


def get_runtime_deployment(*, agent: Agent, env: str = "prod", include_inactive: bool = False) -> AgentRuntimeDeployment:
    statuses = [AgentRuntimeDeployment.STATUS_ACTIVE]
    if include_inactive:
        statuses.extend(
            [
                AgentRuntimeDeployment.STATUS_DEPLOYING,
                AgentRuntimeDeployment.STATUS_STOPPED,
                AgentRuntimeDeployment.STATUS_FAILED,
            ]
        )
    runtime = (
        AgentRuntimeDeployment.objects.filter(tenant=agent.tenant, agent=agent, env=env, status__in=statuses)
        .select_related(
            "agent",
            "image",
            "image__version",
            "project",
            "agent_deployment",
            "agent_deployment__version",
            "edge_registration",
            "edge_registration__node",
        )
        .first()
    )
    if runtime is None:
        raise AgentRuntimeNotAvailable()
    if not include_inactive and runtime.runtime_kind == "docker" and (runtime.docker_lifecycle or {}).get("desired") == "stopped":
        raise AgentRuntimeNotAvailable("Agent Docker runtime is stopping or stopped.")
    if (
        runtime.runtime_kind in {
            AgentRuntimeDeployment.RUNTIME_OPENWRT_IPV6,
            AgentRuntimeDeployment.RUNTIME_OPENWRT_RELAY,
        }
        and (
            runtime.edge_registration is None
            or runtime.edge_registration.status != SoftDeleteModel.STATUS_ACTIVE
            or runtime.edge_registration.lease_expires_at <= timezone.now()
        )
    ):
        raise AgentRuntimeNotAvailable("OpenWrt Agent registration is offline or expired.")
    if (
        runtime.runtime_kind in {
            AgentRuntimeDeployment.RUNTIME_OPENWRT_IPV6,
            AgentRuntimeDeployment.RUNTIME_OPENWRT_RELAY,
        }
        and runtime.edge_registration is not None
        and runtime.edge_registration.node.presence_supported
        and runtime.edge_registration.node.effective_connection_status()
        not in {EdgeNode.CONNECTION_ONLINE, EdgeNode.CONNECTION_DEGRADED}
    ):
        raise AgentRuntimeNotAvailable("OpenWrt Router presence is offline or expired.")
    return runtime


def lock_runtime_operation(*, request, agent_id: str, env: str) -> Agent:
    agent = get_mutable_runtime_agent(request=request, agent_id=agent_id)
    agent = Agent.objects.select_for_update().get(id=agent.id)
    in_progress = Job.objects.filter(
        tenant=agent.tenant,
        job_type__in=[
            "agents.runtime.deploy",
            "agents.runtime.stop",
            "agents.runtime.health_check",
        ],
        status__in=[Job.STATUS_QUEUED, Job.STATUS_RUNNING],
        input_json__agent_id=str(agent.id),
        input_json__env=env,
    ).order_by("-created_at").first()
    if in_progress is not None:
        raise AgentRuntimeOperationConflict(
            {
                "message": "Another runtime operation is already in progress.",
                "job_id": str(in_progress.id),
                "job_type": in_progress.job_type,
                "status": in_progress.status,
            }
        )
    return agent


def get_mutable_runtime_agent(*, request, agent_id: str) -> Agent:
    agent = get_agent(request=request, agent_id=agent_id)
    if not has_nexus_permission(request.user, agent.tenant, "admin", resource_type="agent", resource_id=str(agent.id)):
        raise exceptions.PermissionDenied("Agent admin permission is required.")
    return agent


def get_runtime_use_agent(*, request, tenant: Tenant, agent_id: str) -> Agent:
    return invoke_runtime_policy("get_runtime_use_agent", request=request, tenant=tenant, agent_id=agent_id)


def enforce_api_key_agent_policy(*, api_key, agent: Agent) -> None:
    return invoke_runtime_policy("enforce_api_key_agent_policy", api_key=api_key, agent=agent)


def resolve_project_from_request(*, request, tenant: Tenant) -> Project | None:
    project_id = getattr(request, "project_id", "")
    if not project_id:
        return None
    project = Project.objects.filter(tenant=tenant, id=project_id, status=SoftDeleteModel.STATUS_ACTIVE).first()
    if project is None:
        raise exceptions.NotFound("Project not found.")
    return project




def mark_runtime_failed(*, runtime: AgentRuntimeDeployment, agent_deployment: AgentDeployment, agent: Agent, error: str) -> None:
    runtime.status = AgentRuntimeDeployment.STATUS_FAILED
    runtime.health_status = AgentRuntimeDeployment.HEALTH_UNHEALTHY
    runtime.last_error = error[:1024]
    runtime.save(update_fields=["status", "health_status", "last_error", "updated_at"])
    agent_deployment.status = AgentDeployment.STATUS_FAILED
    agent_deployment.save(update_fields=["status", "updated_at"])
    AgentLog.objects.create(agent=agent, deployment=agent_deployment, level=AgentLog.LEVEL_ERROR, message=runtime.last_error)


def ensure_openwrt_interactive_transport(
    *,
    runtime: AgentRuntimeDeployment,
    policy: dict[str, Any],
) -> None:
    mobile_requirement, _ = runtime_mobile_declaration(runtime)
    mobile_required = mobile_requirement == Agent.MOBILE_REQUIRED
    if not any(policy.get(name) for name in ("task", "chat", "interactive")) and not mobile_required:
        return
    if runtime.runtime_kind != AgentRuntimeDeployment.RUNTIME_OPENWRT_IPV6:
        if runtime.runtime_kind == AgentRuntimeDeployment.RUNTIME_OPENWRT_RELAY:
            raise OpenWrtIPv6Required(runtime=runtime)
        return
    registration = runtime.edge_registration
    capabilities = (
        registration.node.capabilities
        if registration is not None and registration.node_id
        else {}
    ) or {}
    required = {"runtime_context_v1", "invoke_interactions_v1", "mcp_stream_v1"}
    if policy.get("task"):
        required.add("mcp_tasks_v1")
    if mobile_required or policy.get("mobile_scopes"):
        required.add("caller_mobile_v1")
    if any(not capabilities.get(name) for name in required):
        raise OpenWrtIPv6Required(runtime=runtime)


def tool_name_from_mcp_payload(body: bytes) -> str:
    if not body:
        return ""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    if payload.get("method") != "tools/call":
        return str(payload.get("method", ""))[:128]
    params = payload.get("params") or {}
    return str(params.get("name", ""))[:128]


def refresh_runtime_tool_policy(*, runtime: AgentRuntimeDeployment) -> bool:
    """Discover a Docker runtime's Nexus tool policy before its first consumer call."""

    if runtime.runtime_kind != AgentRuntimeDeployment.RUNTIME_DOCKER:
        return True
    runner = get_runtime_runner(runtime)
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json; charset=utf-8",
        "MCP-Protocol-Version": "2025-06-18",
    }
    initialize_body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "nexus-runtime-catalog-init-" + uuid.uuid4().hex,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "nexus-runtime-catalog", "version": "1"},
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    initialized = runner.call_mcp(
        deployment=runtime,
        method="POST",
        headers=headers,
        body=initialize_body,
    )
    if initialized.status_code >= 400:
        return False
    session_id = _header_value(initialized.headers, "Mcp-Session-Id")
    session_headers = dict(headers)
    if session_id:
        session_headers["Mcp-Session-Id"] = session_id
    try:
        activated = runner.call_mcp(
            deployment=runtime,
            method="POST",
            headers=session_headers,
            body=b'{"jsonrpc":"2.0","method":"notifications/initialized"}',
        )
        if activated.status_code >= 400:
            return False
        request_body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "nexus-runtime-catalog-" + uuid.uuid4().hex,
                "method": "tools/list",
                "params": {},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        response = runner.call_mcp(
            deployment=runtime,
            method="POST",
            headers=session_headers,
            body=request_body,
        )
        if response.status_code >= 400:
            return False
        return sync_runtime_tool_policy_from_response(
            agent=runtime.agent,
            request_body=request_body,
            response_body=response.body,
        )
    finally:
        if session_id:
            try:
                runner.call_mcp(
                    deployment=runtime,
                    method="DELETE",
                    headers=session_headers,
                    body=b"",
                )
            except Exception:
                pass


def _decode_mcp_json_response(response_body: bytes) -> dict[str, Any]:
    text = response_body.decode("utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            value = line[5:].strip()
            if not value or value == "[DONE]":
                continue
            try:
                candidate = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                payload = candidate
        if payload is None:
            raise
    if not isinstance(payload, dict):
        raise ValueError("MCP response must be a JSON object")
    return payload


def _decode_mcp_task_response(
    result: RuntimeMCPResult,
) -> tuple[dict[str, Any], bool, dict[str, Any]]:
    """Normalize JSON-RPC and direct Streamable HTTP task results.

    The OpenWrt gateway may unwrap a successful MCP ``tools/call`` envelope
    and return the tool result as the SSE data document itself.  A successful
    direct result is still a completed task; requiring a top-level
    ``result`` member incorrectly turns it into ``MCP_TASK_FAILED``.
    """

    try:
        payload = _decode_mcp_json_response(result.body)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {}, False, {}
    if not _mcp_tool_result_succeeded(result):
        return payload, False, {}
    value = payload.get("result") if "result" in payload else payload
    result_json = value if isinstance(value, dict) else {"value": value}
    return payload, True, result_json


def sync_runtime_tool_policy_from_response(*, agent: Agent, request_body: bytes, response_body: bytes) -> bool:
    try:
        request_payload = json.loads(request_body.decode("utf-8"))
        response_payload = _decode_mcp_json_response(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, AttributeError):
        return False
    if not isinstance(request_payload, dict) or request_payload.get("method") != "tools/list":
        return False
    tools = ((response_payload.get("result") or {}).get("tools") or []) if isinstance(response_payload, dict) else []
    if not isinstance(tools, list):
        return False
    policy: dict[str, dict[str, Any]] = {}
    for item in tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        metadata = item.get("_meta") if isinstance(item.get("_meta"), dict) else item.get("meta")
        nexus = metadata.get("nexus") if isinstance(metadata, dict) and isinstance(metadata.get("nexus"), dict) else {}
        if name and nexus:
            policy[name] = {
                "task": bool(nexus.get("task")),
                "continuable": bool(nexus.get("continuable")),
                "recovery_protocol": int(nexus.get("recovery_protocol") or 0),
                "demo": bool(nexus.get("demo")),
                "chat": bool(nexus.get("chat")),
                "interactive": bool(nexus.get("interactive")),
                "mobile_scopes": [str(value) for value in nexus.get("mobile_scopes", [])]
                if isinstance(nexus.get("mobile_scopes"), list)
                else [],
            }
    version = resolve_version(agent=agent, version_value=agent.current_version)
    if version is not None and version.tool_runtime_policy != policy:
        version.tool_runtime_policy = policy
        version.save(update_fields=["tool_runtime_policy", "updated_at"])
    return version is not None


def is_mcp_tool_call(body: bytes) -> bool:
    if not body:
        return False
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("method") == "tools/call"


def public_mcp_url(*, request, agent: Agent) -> str:
    from .mcp_config import public_agent_mcp_url

    return public_agent_mcp_url(request=request, agent=agent)


def elapsed_ms(started: float) -> int:
    return max(int((time.monotonic() - started) * 1000), 1)


def log_write(*, request, action: str, agent: Agent, metadata: dict[str, Any] | None = None) -> None:
    log_audit(
        request=request,
        action=action,
        actor=request.user,
        resource_type="agent",
        resource_id=agent.pk,
        metadata=metadata or {},
    )


def write_runtime_audit(
    *,
    request,
    actor,
    runtime: AgentRuntimeDeployment,
    action: str,
    before: dict[str, Any] | None = None,
) -> None:
    write_audit_log(
        request=request,
        actor=actor,
        tenant=runtime.tenant,
        action=action,
        resource_type="agent_runtime_deployment",
        resource_id=runtime.pk,
        before=before,
        after=runtime_snapshot(runtime),
        metadata={"agent_id": str(runtime.agent_id), "env": runtime.env},
    )


def runtime_snapshot(runtime: AgentRuntimeDeployment) -> dict[str, Any]:
    return {
        "tenant_id": str(runtime.tenant_id),
        "project_id": str(runtime.project_id or ""),
        "agent_id": str(runtime.agent_id),
        "runtime_kind": runtime.runtime_kind,
        "image_id": str(runtime.image_id or ""),
        "edge_registration_id": str(runtime.edge_registration_id or ""),
        "env": runtime.env,
        "status": runtime.status,
        "container_id": runtime.container_id,
        "internal_mcp_url": runtime.internal_mcp_url,
        "health_status": runtime.health_status,
        "last_error": runtime.last_error,
        "workspace_connection_id": str(runtime.workspace_connection_id or ""),
        "workspace_root": runtime.workspace_root,
        "workspace_access_mode": runtime.workspace_access_mode,
    }


def raise_eager_task_error(async_result) -> None:
    failed = getattr(async_result, "failed", None)
    if callable(failed) and failed():
        result = getattr(async_result, "result", None)
        if isinstance(result, Exception):
            raise result
        raise AgentRuntimeError(str(result or "Agent runtime task failed."))


def save_runtime_image_artifact(*, agent: Agent, uploaded_file) -> str:
    filename = safe_artifact_filename(getattr(uploaded_file, "name", "agent-image.tar"))
    relative_path = Path(str(agent.tenant_id)) / str(agent.id) / "runtime-images" / uuid.uuid4().hex / filename
    target = agent_storage_root() / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as destination:
        for chunk in uploaded_file.chunks():
            destination.write(chunk)
    return relative_path.as_posix()


def safe_artifact_filename(name: str) -> str:
    value = Path(name).name.strip().replace("\\", "_").replace("/", "_")
    return value or "agent-image.tar"


def agent_storage_root() -> Path:
    return Path(settings.NEXUS_AGENT_STORAGE_ROOT).resolve()
