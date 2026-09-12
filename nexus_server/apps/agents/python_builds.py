"""Python upload control plane. Never imports or executes uploaded source."""
from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction, connection
from django.utils import timezone
from rest_framework import exceptions

from apps.common.crypto import encrypt_secret, decrypt_secret
from .models import Agent, AgentPythonBuild, AgentRuntimeImage, AgentVersion
from .runtime_services import get_mutable_runtime_agent

MAX_SOURCE = 1024 * 1024
MAX_REQUIREMENTS = 32 * 1024
STAGES = ("queued", "dependencies", "image", "verify", "ready")


def inspect_source(source: str, entrypoint: str = "") -> dict:
    try:
        tree = ast.parse(source, filename="agent.py", feature_version=(3, 12))
    except (SyntaxError, ValueError, RecursionError) as exc:
        line = getattr(exc, "lineno", None)
        raise exceptions.ValidationError({"file": f"Python syntax error{f' at line {line}' if line else ''}. Check this file with Python 3.12."}) from None
    constructors = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in {"fastmcp", "nexus_agent.fastmcp", "nexus_agent", "nexus_agent.agent"}:
            for item in node.names:
                if item.name in {"FastMCP", "NexusMCPServer", "NexusAgent"}:
                    constructors[item.asname or item.name] = item.name
        elif isinstance(node, ast.Import):
            for item in node.names:
                if item.name == "fastmcp":
                    constructors[f"{item.asname or 'fastmcp'}.FastMCP"] = "FastMCP"
                elif item.name == "nexus_agent.fastmcp":
                    constructors[f"{item.asname or 'nexus_agent.fastmcp'}.NexusMCPServer"] = "NexusMCPServer"
                elif item.name in {"nexus_agent", "nexus_agent.agent"}:
                    constructors[f"{item.asname or item.name}.NexusAgent"] = "NexusAgent"
    candidates = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call):
            call_name = ast.unparse(node.value.func)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and call_name in constructors:
                    candidates[target.id] = constructors[call_name]
    if entrypoint:
        if entrypoint not in candidates:
            raise exceptions.ValidationError({"entrypoint": "Choose a top-level NexusAgent, NexusMCPServer or FastMCP instance declared in this file."})
    elif len(candidates) == 1:
        entrypoint = next(iter(candidates))
    elif candidates:
        raise exceptions.ValidationError({"entrypoint": "Multiple servers found. Enter one instance name: " + ", ".join(sorted(candidates)[:8])})
    else:
        raise exceptions.ValidationError({"file": "No Agent found. Declare a top-level NexusAgent, NexusMCPServer or FastMCP instance; ordinary scripts and factories are not supported."})
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", entrypoint):
        raise exceptions.ValidationError({"entrypoint": "Use an ASCII instance name up to 64 characters."})
    return {"entrypoint": entrypoint, "framework": candidates[entrypoint]}


def validate_requirements(value: str) -> str:
    """Public wheel packages only: never accept pip options, URLs, paths or VCS."""
    lines = []
    for index, raw in enumerate(value.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9_,.-]+\])?(?:\s*(?:==|>=|<=|~=|!=|>|<)\s*[A-Za-z0-9.*+!-]+(?:\s*,\s*(?:==|>=|<=|~=|!=|>|<)\s*[A-Za-z0-9.*+!-]+)*)?", line):
            raise exceptions.ValidationError({"requirements": f"Line {index}: use a public package name and optional version constraint, not a URL, path or pip option."})
        package = re.split(r"[\[<>=!~\s]", line)[0].lower().replace("_", "-").replace(".", "-")
        if package in {"nexus-agent-sdk", "fastmcp", "pip", "setuptools", "wheel"}:
            raise exceptions.ValidationError({"requirements": f"Line {index}: this package is managed by the Nexus Python profile."})
        lines.append(line)
    if len(lines) > 100:
        raise exceptions.ValidationError({"requirements": "At most 100 direct dependencies are supported."})
    return "\n".join(lines)


def read_upload(upload, *, field, maximum):
    if not upload or upload.size > maximum:
        raise exceptions.ValidationError({field: f"Choose a UTF-8 file no larger than {maximum // 1024} KiB."})
    try:
        raw = upload.read(maximum + 1)
        if len(raw) > maximum:
            raise ValueError()
        return raw.decode("utf-8-sig")
    except (ValueError, UnicodeError):
        raise exceptions.ValidationError({field: "Choose a UTF-8 text file within the size limit."}) from None


def build_configuration():
    enabled = bool(getattr(settings, "NEXUS_AGENT_PYTHON_BUILDS_ENABLED", False))
    return {"enabled": enabled, "profile": "Python 3.12 · Nexus SDK / FastMCP", "max_source_bytes": MAX_SOURCE}


def enqueue_build(*, request, agent_id):
    agent = get_mutable_runtime_agent(request=request, agent_id=agent_id)
    if not build_configuration()["enabled"]:
        raise exceptions.ValidationError({"file": "Python builds are not enabled on this installation. Ask the platform operator to configure the Python build worker."})
    upload = request.FILES.get("file")
    if not upload or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,124}\.py", upload.name):
        raise exceptions.ValidationError({"file": "Choose a single .py file with a simple filename."})
    source = read_upload(upload, field="file", maximum=MAX_SOURCE)
    declaration = inspect_source(source, str(request.data.get("entrypoint", "")))
    dependencies = request.FILES.get("requirements")
    requirements = validate_requirements(read_upload(dependencies, field="requirements", maximum=MAX_REQUIREMENTS)) if dependencies else ""
    try:
        secrets = json.loads(request.data.get("secrets", "{}"))
        if not isinstance(secrets, dict) or len(secrets) > 20:
            raise ValueError()
        for key, value in secrets.items():
            if (not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key)
                or key.startswith(("NEXUS_", "PYTHON", "LD_", "DYLD_", "PIP_", "UV_"))
                or key in {"PATH", "HOME", "BASH_ENV", "ENV", "SHELLOPTS"}
                or not isinstance(value, str) or not value or len(value) > 8192 or "\x00" in value):
                raise ValueError()
    except (TypeError, ValueError):
        raise exceptions.ValidationError({"secrets": "Use up to 20 uppercase environment names and nonempty values. Runtime and Python control variables are reserved."}) from None
    with transaction.atomic():
        # Serialize uploads across API instances; one pending revision per Agent.
        from apps.tenancy.models import Tenant
        from apps.jobs.services import request_user_or_none
        Tenant.objects.select_for_update().get(pk=agent.tenant_id)
        Agent.objects.select_for_update().get(pk=agent.pk)
        if AgentPythonBuild.objects.filter(agent=agent, status__in=["queued", "running"]).exists():
            raise exceptions.ValidationError({"file": "A build is already pending for this Agent. Wait for it to finish."})
        if AgentPythonBuild.objects.filter(agent__tenant=agent.tenant, status__in=["queued", "running"]).count() >= 10:
            raise exceptions.Throttled(detail="The Organization build queue is full. Try again later.")
        # Bounded durable history; operators can retain/export revisions before cleanup.
        if AgentPythonBuild.objects.filter(agent=agent).exclude(status="deleted").count() >= 100:
            raise exceptions.ValidationError({"file": "This Agent has reached its 100 source revision limit. Delete failed builds to free space, or contact the operator to archive old revisions."})
        build = AgentPythonBuild.objects.create(agent=agent, created_by=request_user_or_none(request.user),
            filename=upload.name, source=source, requirements=requirements,
            source_sha256=hashlib.sha256(source.encode()).hexdigest(),
            base_image=settings.NEXUS_AGENT_PYTHON_BASE_IMAGE, target_host=settings.NEXUS_AGENT_RUNTIME_HOST_ID,
            encrypted_secrets=encrypt_secret(json.dumps(secrets)) if secrets else "", **declaration)
        from .runtime_services import log_write
        log_write(request=request, action="agents.runtime.python.upload", agent=agent,
            metadata={"build_id": str(build.id), "source_sha256": build.source_sha256})
        return build


class PythonBuildDeleteConflict(exceptions.APIException):
    status_code = 409
    default_code = "PYTHON_BUILD_DELETE_CONFLICT"
    default_detail = "Only failed builds without a registered image can be deleted. Queued, running and successful builds are protected."


def delete_failed_builds(*, request, agent_id, build_ids):
    agent = get_mutable_runtime_agent(request=request, agent_id=agent_id)
    ids = set(build_ids)
    with transaction.atomic():
        Agent.objects.select_for_update().get(pk=agent.pk)
        builds = list(AgentPythonBuild.objects.select_for_update().filter(agent=agent, pk__in=ids).exclude(status="deleted").order_by("pk"))
        if len(builds) != len(ids):
            raise exceptions.NotFound("Build not found.")
        if any(build.status != "failed" or build.image_id is not None for build in builds):
            raise PythonBuildDeleteConflict()
        # Keep only a cleanup tombstone for the assigned worker. Remove private
        # payloads now, even if that host is offline. No Docker access in the API.
        AgentPythonBuild.objects.filter(pk__in=ids).update(
            status="deleted", stage="cleanup_pending", source="", requirements="", encrypted_secrets="",
            filename="", source_sha256="", entrypoint="", framework="", base_image="",
            error_code="", error_message="", diagnostics=[], dependency_lock=[], tool_count=0,
            created_by=None, worker_id="", lease_expires_at=None, updated_at=timezone.now(),
        )
        from apps.notifications.models import InboxItem, UserNotification
        InboxItem.objects.filter(tenant=agent.tenant, source_type="build", source_id__in=[str(pk) for pk in ids]).delete()
        UserNotification.objects.filter(build_id__in=ids).delete()
        from .runtime_services import log_write
        log_write(request=request, action="agents.runtime.python.delete_failed", agent=agent,
                  metadata={"build_ids": sorted(str(pk) for pk in ids), "count": len(ids)})
    return {"deleted_ids": sorted(str(pk) for pk in ids), "cleanup_pending": True}


def build_data(build):
    # Explicit allowlist: never serialize source, raw logs or secret values.
    return {"id": str(build.id), "filename": build.filename, "source_sha256": build.source_sha256,
        "entrypoint": build.entrypoint, "framework": build.framework, "status": build.status, "stage": build.stage,
        "error_code": build.error_code, "error_message": build.error_message,
        "diagnostics": build.diagnostics, "dependency_lock": build.dependency_lock,
        "tool_count": build.tool_count, "image_id": str(build.image_id) if build.image_id else None,
        "image_removed": bool(build.image_id and build.image.status == "deleted"),
        "created_at": build.created_at, "completed_at": build.completed_at}


def runtime_secrets(image):
    build = AgentPythonBuild.objects.filter(image=image, status="succeeded").only("encrypted_secrets").first()
    return json.loads(decrypt_secret(build.encrypted_secrets)) if build and build.encrypted_secrets else {}


def claim_build(worker_id):
    with transaction.atomic():
        if connection.vendor != "postgresql":
            raise RuntimeError("Python builds require PostgreSQL.")
        # A short, global scheduling lock enforces the same bound across workers.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(72841092)")
        now = timezone.now()
        # Save each expired lease inside the claim transaction so failure notices
        # are durable even when the worker cannot report its own failure.
        for expired in AgentPythonBuild.objects.select_for_update().filter(status="running", lease_expires_at__lt=now):
            expired.status = "failed"
            expired.error_code = "BUILD_WORKER_LOST"
            expired.error_message = "The build worker stopped before completion. Upload again to retry."
            expired.completed_at = now
            expired.save(update_fields=["status", "error_code", "error_message", "completed_at", "updated_at"])
        if AgentPythonBuild.objects.filter(status="running").count() >= 2:
            return None
        build = AgentPythonBuild.objects.select_for_update(skip_locked=True).filter(status="queued", target_host=settings.NEXUS_AGENT_RUNTIME_HOST_ID).order_by("created_at").first()
        if build:
            build.status = "running"
            build.started_at = now
            build.worker_id = worker_id
            build.lease_expires_at = now + timedelta(seconds=1200)
            build.save(update_fields=["status", "started_at", "worker_id", "lease_expires_at", "updated_at"])
        return build


def execute_build(build):
    from .python_build_cleanup import build_execution_lock
    # Cleanup must not race an old, still-live worker after its lease expires.
    with build_execution_lock(build.pk) as acquired:
        if acquired:
            _execute_build(build)


def _execute_build(build):
    from .python_builder import build_image, BuildFailure

    if not AgentPythonBuild.objects.filter(pk=build.pk, status="running", worker_id=build.worker_id).exists():
        return

    def stage(name):
        if not AgentPythonBuild.objects.filter(pk=build.pk, status="running", worker_id=build.worker_id).update(stage=name, updated_at=timezone.now()):
            raise BuildFailure("BUILD_NO_LONGER_ACTIVE", "This build is no longer active.")
    try:
        result = build_image(build, stage)
        with transaction.atomic():
            agent = Agent.objects.select_for_update().get(pk=build.agent_id)
            current = AgentPythonBuild.objects.select_for_update().filter(pk=build.pk, status="running", worker_id=build.worker_id).first()
            if current is None:
                return
            if agent.status == "deleted":
                raise BuildFailure("AGENT_UNAVAILABLE", "This Agent is no longer available.")
            # Internal source version, not Marketplace publication. Binding is
            # activated only by the existing explicit image/deploy operation.
            contract = result.get("agent_contract")
            declaration_fields = {}
            if contract:
                from .python_contract import normalize_agent_contract
                contract = normalize_agent_contract(contract)
                declaration_fields = {"workspace_capabilities": contract["computer"]["workspace_capabilities"],
                    "mobile_requirement": contract["mobile"]["requirement"],
                    "mobile_capabilities": contract["mobile"]["mobile_capabilities"]}
            version = AgentVersion.objects.create(agent=agent, version="py-" + build.id.hex[:28],
                commit_id=build.source_sha256, created_by=build.created_by,
                artifact_metadata={"source_type": "python_upload", "source_sha256": build.source_sha256, "tools": result.get("tools", []), "agent_contract": contract},
                tool_runtime_policy=result.get("policies", {}), **declaration_fields)
            image = AgentRuntimeImage.objects.create(
                agent=agent,
                tenant=agent.tenant,
                project=agent.project,
                image_ref=result["image_ref"],
                image_digest=result["digest"],
                artifact_path=str(result.get("artifact_path") or ""),
                version=version,
                created_by=build.created_by,
            )
            current.image = image
            current.status = "succeeded"
            current.stage = "ready"
            current.tool_count = result["tool_count"]
            current.dependency_lock = result["dependencies"]
            current.diagnostics = ["Source checked without execution.", "Dependencies installed in isolation.", "MCP initialized and tools listed without calling them.", "Candidate image ready; existing deployment unchanged."]
            current.completed_at = timezone.now()
            current.save()
    except Exception as exc:
        safe = exc if isinstance(exc, BuildFailure) else BuildFailure("BUILD_FAILED", "The build could not finish. Retry or contact support with the build ID.")
        with transaction.atomic():
            current = AgentPythonBuild.objects.select_for_update().filter(pk=build.pk, status="running").first()
            if current:
                current.status, current.error_code, current.error_message = "failed", safe.code, safe.message
                current.completed_at = timezone.now()
                current.save(update_fields=["status", "error_code", "error_message", "completed_at", "updated_at"])
