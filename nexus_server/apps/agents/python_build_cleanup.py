"""Host-owned failed-build cleanup. Never executes in an API request.

Deletion erases private payloads immediately and leaves a durable tombstone.
The assigned worker retries cleanup without removing shared or deployed images.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from .models import AgentPythonBuild, AgentRuntimeImage
from .python_builder import BuildFailure, docker


@contextmanager
def build_execution_lock(build_id):
    if connection.vendor != "postgresql":
        raise RuntimeError("Python builds require PostgreSQL.")
    key = int.from_bytes(hashlib.sha256(f"python-build:{build_id}".encode()).digest()[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        acquired = cursor.fetchone()[0]
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


def cleanup_build_artifacts(build):
    """Exact generated targets only; no recursive removal, prune or force rmi."""
    identity = build.pk.hex
    name = f"nexus-python-{identity}"
    tag = f"nexus-python/{build.agent_id.hex}:{identity}"
    # Unlike best-effort build-finally cleanup, Docker unavailability must leave
    # this tombstone pending for the next worker pass.
    containers = docker(["ps", "-aq", "--filter", f"name=^/{name}$"], timeout=15).strip()
    if containers:
        info = json.loads(docker(["container", "inspect", name], timeout=15))[0]
        if info.get("Config", {}).get("Labels", {}).get("nexus.managed") != "python-build":
            raise BuildFailure("BUILD_CLEANUP_CONFLICT", "Build cleanup requires operator review.")
        docker(["rm", "-f", name], timeout=15)
    images = docker(["image", "ls", "-q", "--no-trunc", "--filter", f"reference={tag}"], timeout=15).strip()
    if images:
        image = json.loads(docker(["image", "inspect", tag], timeout=15))[0]
        if image.get("Config", {}).get("Labels", {}).get("nexus.managed") != "python-build":
            raise BuildFailure("BUILD_CLEANUP_CONFLICT", "Build cleanup requires operator review.")
        if AgentRuntimeImage.objects.filter(Q(image_ref=tag) | Q(image_digest=image["Id"])).exists():
            raise BuildFailure("BUILD_CLEANUP_REFERENCED", "Build artifacts are still referenced.")
        # Docker refuses an image still used by a container. Never force removal.
        docker(["image", "rm", "--no-prune", tag], timeout=15)

    root = Path(settings.NEXUS_AGENT_STORAGE_ROOT).resolve()
    relative = Path(str(build.agent.tenant_id)) / str(build.agent_id) / "runtime-images" / identity / "agent-image.tar"
    expected = root / relative
    target = expected.resolve()
    if not target.is_relative_to(root) or target != expected:
        raise BuildFailure("BUILD_CLEANUP_PATH_INVALID", "Build cleanup requires operator review.")
    if AgentRuntimeImage.objects.filter(artifact_path=relative.as_posix()).exists():
        raise BuildFailure("BUILD_CLEANUP_REFERENCED", "Build artifacts are still referenced.")
    for path in (target, target.with_suffix(".partial")):
        if path.is_symlink():
            raise BuildFailure("BUILD_CLEANUP_PATH_INVALID", "Build cleanup requires operator review.")
        path.unlink(missing_ok=True)
    if target.parent.is_dir() and not any(target.parent.iterdir()):
        target.parent.rmdir()


def cleanup_deleted_builds(limit=20):
    ids = list(AgentPythonBuild.objects.filter(
        status="deleted", target_host=settings.NEXUS_AGENT_RUNTIME_HOST_ID,
    ).order_by("updated_at").values_list("pk", flat=True)[:limit])
    cleaned = 0
    for build_id in ids:
        with build_execution_lock(build_id) as acquired:
            if not acquired:
                continue
            with transaction.atomic():
                build = AgentPythonBuild.objects.select_for_update(of=("self",)).select_related("agent").filter(
                    pk=build_id, status="deleted", image__isnull=True,
                    target_host=settings.NEXUS_AGENT_RUNTIME_HOST_ID,
                ).first()
                if build is None:
                    continue
                try:
                    cleanup_build_artifacts(build)
                except (BuildFailure, OSError, ValueError, KeyError, IndexError):
                    # Never persist Docker output, paths, source or credentials.
                    AgentPythonBuild.objects.filter(pk=build_id).update(stage="cleanup_pending", updated_at=timezone.now())
                    continue
                build.delete()
                cleaned += 1
    return cleaned
