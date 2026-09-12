"""Fixed-recipe Docker builder. Uploaded code runs only in a bounded, offline container.

The operator supplies a vetted Python 3.12/Nexus/FastMCP image by digest. No
user Dockerfiles, shell fragments, host mounts, build secrets or pip options.
"""
from __future__ import annotations

import io
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import time

from django.conf import settings


class BuildFailure(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def ensure_builder_dependencies():
    base = str(getattr(settings, "NEXUS_AGENT_PYTHON_BASE_IMAGE", "") or "")
    if not re.fullmatch(r"(?:[a-zA-Z0-9./:_-]+@)?sha256:[a-f0-9]{64}", base):
        raise BuildFailure("PYTHON_PROFILE_REQUIRED", "The Python builder requires an immutable runtime profile.")
    docker(["version", "--format", "{{.Server.Version}}"], timeout=15)
    try:
        info = json.loads(docker(["image", "inspect", base], timeout=30))
    except (ValueError, TypeError, KeyError) as exc:
        raise BuildFailure("PYTHON_PROFILE_UNAVAILABLE", "The Python runtime profile is not available on the build host.") from exc
    if not isinstance(info, list) or not info:
        raise BuildFailure("PYTHON_PROFILE_UNAVAILABLE", "The Python runtime profile is not available on the build host.")


def docker(args, *, timeout=60, input_bytes=None, max_bytes=1024 * 1024):
    """Bound disk/output and wall time; never return Docker exception/log text."""
    command = [getattr(settings, "NEXUS_AGENT_PYTHON_DOCKER", "docker"), *args]
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            # Input is already bounded by the upload protocol. A private file
            # delivers it without blocking on a full pipe before timeout polling,
            # and is closed/removed on every exit. Never place it in argv or logs.
            if input_bytes is not None:
                source.write(input_bytes)
                source.seek(0)
            deadline = time.monotonic() + timeout
            process = subprocess.Popen(command, stdin=source if input_bytes is not None else subprocess.DEVNULL,
                stdout=output, stderr=errors)
            try:
                while process.poll() is None:
                    if time.monotonic() > deadline:
                        raise BuildFailure("BUILD_TIMEOUT", "This build exceeded its time limit. Check dependency size and initialization work.")
                    if os.fstat(output.fileno()).st_size > max_bytes or os.fstat(errors.fileno()).st_size > 1024 * 1024:
                        raise BuildFailure("BUILD_OUTPUT_LIMIT", "Build output exceeded the safe size limit.")
                    time.sleep(0.1)
                if process.returncode:
                    raise BuildFailure("BUILD_STAGE_FAILED", "This build stage failed. Check public wheel dependencies and the Python entrypoint; no active deployment was changed.")
                output.seek(0)
                result = output.read(max_bytes + 1)
                if len(result) > max_bytes:
                    raise BuildFailure("BUILD_OUTPUT_LIMIT", "Build output exceeded the safe size limit.")
                return result
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
        except OSError:
            raise BuildFailure("BUILDER_UNAVAILABLE", "The Python build worker cannot reach Docker.") from None


# Executed inside the trusted base image, BEFORE user source/dependencies are present.
DEPENDENCIES = r'''
import io, pathlib, subprocess, sys, tarfile
requirements = sys.stdin.read(32769)
root = pathlib.Path('/tmp/bundle'); root.mkdir()
pathlib.Path('/tmp/requirements.txt').write_text(requirements)
frozen = subprocess.check_output([sys.executable, '-m', 'pip', 'list', '--format=freeze', '--disable-pip-version-check']).decode()
pathlib.Path('/tmp/constraints.txt').write_text(frozen)
if requirements.strip():
    subprocess.run([sys.executable, '-m', 'pip', '--isolated', 'install', '--disable-pip-version-check',
        '--no-cache-dir', '--only-binary=:all:', '--index-url', 'https://pypi.org/simple',
        '--timeout', '15', '--retries', '1', '--target', str(root),
        '-c', '/tmp/constraints.txt', '-r', '/tmp/requirements.txt'],
        stdout=sys.stderr, stderr=sys.stderr, check=True)
with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as archive:
    archive.add(root, arcname='dependencies')
'''


def sandbox(name, base, command, *, network="none", tmpfs="256m"):
    return ["run", "--rm", "-i", "--name", name, "--label", "nexus.managed=python-build",
        "--read-only", "--user", "65532:65532", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "128", "--memory", "1g", "--memory-swap", "1g", "--cpus", "1",
        "--log-driver", "none", "--network", network, "--tmpfs", f"/tmp:rw,nosuid,nodev,size={tmpfs},mode=1777",
        "--entrypoint", "python", base, *command]


def remove_own_container(name):
    # Exact generated name + ownership label; never remove arbitrary containers.
    try:
        rows = json.loads(docker(["container", "inspect", name]))
        if rows[0].get("Config", {}).get("Labels", {}).get("nexus.managed") == "python-build":
            docker(["rm", "-f", name], timeout=15)
    except (BuildFailure, ValueError, KeyError, IndexError):
        pass


def save_image_artifact(build, image_id):
    """Persist a verified immutable image so another Docker host can recover it."""

    storage_root = Path(settings.NEXUS_AGENT_STORAGE_ROOT).resolve()
    tenant_id = str(getattr(getattr(build, "agent", None), "tenant_id", "unassigned"))
    relative = (
        Path(tenant_id)
        / str(build.agent_id)
        / "runtime-images"
        / str(build.pk).replace("-", "")
        / "agent-image.tar"
    )
    target = (storage_root / relative).resolve()
    try:
        target.relative_to(storage_root)
    except ValueError as exc:  # pragma: no cover - identifiers are server generated
        raise BuildFailure("IMAGE_ARTIFACT_PATH_INVALID", "The built image could not be stored safely.") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    partial.unlink(missing_ok=True)
    executable = getattr(settings, "NEXUS_AGENT_PYTHON_DOCKER", "docker")
    max_bytes = int(
        getattr(settings, "NEXUS_AGENT_PYTHON_IMAGE_ARTIFACT_MAX_BYTES", 2 * 1024 * 1024 * 1024)
    )
    process = None
    try:
        process = subprocess.Popen(
            [executable, "image", "save", "--output", str(partial), image_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 300
        while process.poll() is None:
            if time.monotonic() > deadline:
                raise BuildFailure("IMAGE_ARTIFACT_TIMEOUT", "The built image took too long to save.")
            if partial.exists() and partial.stat().st_size > max_bytes:
                raise BuildFailure("IMAGE_ARTIFACT_LIMIT", "The built image exceeds the hosted image limit.")
            time.sleep(0.1)
        if process.returncode or not partial.is_file() or partial.stat().st_size <= 0:
            raise BuildFailure("IMAGE_ARTIFACT_FAILED", "The built image could not be saved for recovery.")
        if partial.stat().st_size > max_bytes:
            raise BuildFailure("IMAGE_ARTIFACT_LIMIT", "The built image exceeds the hosted image limit.")
        os.replace(partial, target)
        return relative.as_posix()
    except OSError as exc:
        raise BuildFailure("IMAGE_ARTIFACT_FAILED", "The built image could not be saved for recovery.") from exc
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        partial.unlink(missing_ok=True)


def build_image(build, stage):
    base = build.base_image
    if not re.fullmatch(r"(?:[a-zA-Z0-9./:_-]+@)?sha256:[a-f0-9]{64}", base):
        raise BuildFailure("PYTHON_PROFILE_REQUIRED", "The operator must configure an immutable Python runtime profile image.")
    if getattr(settings, "NEXUS_PRODUCTION", False) and not getattr(settings, "NEXUS_AGENT_PYTHON_ISOLATION_READY", False):
        raise BuildFailure("BUILD_ISOLATION_REQUIRED", "The operator must verify dedicated build-worker isolation and egress/storage quotas.")
    identity = str(build.pk).replace("-", "")
    if not re.fullmatch(r"[a-f0-9]{32}", identity):
        raise BuildFailure("BUILD_ID_INVALID", "Invalid build identity.")
    name = f"nexus-python-{identity}"
    tag = f"nexus-python/{str(build.agent_id).replace('-', '')}:{identity}"
    # BuildKit treats bare config IDs in FROM as registry names. A unique
    # worker-owned alias pins the already-resolved local image for COPY-only builds.
    base_alias = f"nexus-python-base:{identity}"
    # A private bounded context; its only source comes from this revision.
    with tempfile.TemporaryDirectory(prefix="nexus-python-") as temporary:
        root = Path(temporary)
        try:
            if build.framework == "NexusAgent":
                try:
                    docker(sandbox(name, base, ["-I", "-c", "from nexus_agent import NexusAgent; assert hasattr(NexusAgent, 'as_mcp_server')"]), timeout=30)
                except BuildFailure:
                    raise BuildFailure("PYTHON_PROFILE_UPGRADE_REQUIRED", "Rebuild the trusted Python profile with Nexus SDK 0.46.0 or newer to upload NexusAgent source.") from None
            docker(["tag", base, base_alias])
            stage("dependencies")
            dependency_tar = docker(sandbox(name, base, ["-I", "-c", DEPENDENCIES],
                network=getattr(settings, "NEXUS_AGENT_PYTHON_DEPENDENCY_NETWORK", "bridge"), tmpfs="768m"),
                input_bytes=build.requirements.encode(), timeout=300, max_bytes=512 * 1024 * 1024)
            with tarfile.open(fileobj=io.BytesIO(dependency_tar)) as archive:
                total = 0
                for member in archive:
                    total += member.size
                    parts = Path(member.name).parts
                    if (total > 512 * 1024 * 1024 or not parts or parts[0] != "dependencies"
                        or ".." in parts or "\\" in member.name or ":" in member.name
                        or not (member.isfile() or member.isdir())):
                        raise BuildFailure("DEPENDENCY_ARCHIVE_INVALID", "A dependency contains unsupported files.")
                    target = (root / member.name).resolve()
                    if not target.is_relative_to(root.resolve()):
                        raise BuildFailure("DEPENDENCY_ARCHIVE_INVALID", "A dependency contains unsupported paths.")
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.extractfile(member) as source, target.open("wb") as dest:
                            import shutil
                            shutil.copyfileobj(source, dest, 1024 * 1024)
            del dependency_tar
            (root / "dependencies").mkdir(exist_ok=True)
            (root / "agent.py").write_text(build.source, encoding="utf-8")
            (root / "nexus_boot.py").write_bytes((Path(__file__).parent / "python_profile" / "nexus_boot.py").read_bytes())
            # No RUN: user code and dependencies cannot execute during image assembly.
            recipe = f'''FROM {base_alias}
COPY --chown=65532:65532 dependencies/ /opt/agent-dependencies/
COPY --chown=65532:65532 agent.py nexus_boot.py /opt/nexus-python/
ENV PYTHONPATH=/opt/agent-dependencies PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/nexus-python
USER 65532:65532
EXPOSE 8000
ENTRYPOINT ["python", "/opt/nexus-python/nexus_boot.py"]
CMD ["serve", "{build.entrypoint}"]
'''
            (root / "Dockerfile").write_text(recipe, encoding="utf-8")
            stage("image")
            source_digest = hashlib.sha256(build.source.encode("utf-8")).hexdigest()
            docker(["build", "--network=none",
                "--label", "nexus.managed=python-build",
                "--label", "nexus.python.host=" + str(getattr(settings, 'NEXUS_AGENT_RUNTIME_HOST_ID', '')),
                "--label", "nexus.python.build=" + identity,
                "--label", "nexus.python.source=" + source_digest,
                "-t", tag, str(root)], timeout=180)
            info = json.loads(docker(["image", "inspect", tag]))[0]
            digest = info.get("Id", "")
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
                raise BuildFailure("IMAGE_ID_INVALID", "Docker did not return an immutable image identity.")
            stage("verify")
            report = json.loads(docker(sandbox(name, digest, ["/opt/nexus-python/nexus_boot.py", "verify", build.entrypoint]), timeout=60))
            if not report.get("ok"):
                code = report.get("code")
                messages = {"NO_TOOLS": "No tools were found. Add an exported @agent.capability or @server.tool function.",
                    "IMPORT_FAILED": "Python import failed. Check dependencies and avoid startup work that requires secrets or network access.",
                    "STARTUP_FAILED": "The MCP server did not start. Check initialization and entrypoint; validation runs offline without secrets."}
                raise BuildFailure(code if code in messages else "VERIFY_FAILED", messages.get(code, "MCP initialization or tools/list validation failed."))
            dependencies = report.get("dependencies", [])
            if not isinstance(dependencies, list) or len(dependencies) > 2000 or any(not isinstance(p, str) or not re.fullmatch(r"[A-Za-z0-9._-]+==[A-Za-z0-9.+!_-]+", p) for p in dependencies):
                raise BuildFailure("LOCK_INVALID", "Dependency lock metadata was invalid.")
            raw_tools = report.get("tools", [])
            if not isinstance(raw_tools, list) or not raw_tools or len(raw_tools) > 256:
                raise BuildFailure("TOOL_CATALOG_INVALID", "The Agent must expose between 1 and 256 MCP tools.")
            from .python_contract import verified_python_catalog
            tools, policies, agent_contract = verified_python_catalog(raw_tools, native=build.framework == "NexusAgent")
            if len(tools) != len(raw_tools) or len(json.dumps(tools).encode()) > 256 * 1024:
                raise BuildFailure("TOOL_CATALOG_INVALID", "The MCP tool catalog is invalid or too large.")
            from .docker_policy import verify_admission
            verify_admission(digest)
            stage("artifact")
            artifact_path = save_image_artifact(build, digest)
            return {
                "image_ref": tag,
                "digest": digest,
                "artifact_path": artifact_path,
                "tool_count": len(tools),
                "dependencies": dependencies,
                "tools": tools,
                "policies": policies,
                "agent_contract": agent_contract,
            }
        finally:
            remove_own_container(name)
            try:
                docker(["image", "rm", base_alias], timeout=15)
            except BuildFailure:
                pass
