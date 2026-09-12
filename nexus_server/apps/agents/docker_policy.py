"""Server-owned Docker policy. None of these settings come from an Agent image."""
import os
import re
import subprocess
from decimal import Decimal, InvalidOperation

from django.conf import settings
from rest_framework import exceptions
from .admission_process import verifier_exit_code


def resource_limits(deployment):
    config = getattr(deployment.agent, "resource_config", None)
    cpu = str(getattr(config, "cpu", "") or getattr(settings, "NEXUS_AGENT_RUNTIME_CPU_LIMIT", "1")).strip().lower()
    memory = str(getattr(config, "memory", "") or getattr(settings, "NEXUS_AGENT_RUNTIME_MEMORY_LIMIT", "512m")).strip().lower()
    try:
        cores = Decimal(cpu[:-1]) / 1000 if cpu.endswith("m") else Decimal(cpu)
        if not cores.is_finite() or cores < Decimal("0.01"):
            raise ValueError()
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(k|m|g|ki|kib|mi|mib|gi|gib|kb|mb|gb)?", memory)
        if not match:
            raise ValueError()
        factors = {"k": 1024, "ki": 1024, "kib": 1024, "kb": 1024,
                   "m": 1024**2, "mi": 1024**2, "mib": 1024**2, "mb": 1024**2,
                   "g": 1024**3, "gi": 1024**3, "gib": 1024**3, "gb": 1000 * 1024**2}
        # Match the product's MB convention, including its legacy GB conversion.
        memory_bytes = int(Decimal(match[1]) * factors[match[2] or "mb"])
        if memory_bytes < 6 * 1024**2:
            raise ValueError()
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise exceptions.ValidationError("Invalid Agent resources: CPU must be at least 0.01 cores and memory at least 6 MiB.") from exc
    return {"cpu": format(cores.normalize(), "f"), "memory_bytes": memory_bytes}


def host_id():
    return str(getattr(settings, "NEXUS_AGENT_RUNTIME_HOST_ID", "local")).strip()


def assert_host(deployment=None, *, starting=True):
    """Do not accidentally manage another daemon's identically named runtime."""
    state = getattr(deployment, "docker_lifecycle", {}) or {}
    if state.get("host_id") and state["host_id"] != host_id():
        raise exceptions.APIException("AGENT_DOCKER_HOST_MISMATCH: route this operation to the assigned Docker worker.")
    remote = os.environ.get("DOCKER_HOST", "")
    # Remote daemons need a host-aware transport, not localhost URLs exposed by this runner.
    if remote and not remote.startswith(("unix://", "npipe://")):
        raise exceptions.APIException("AGENT_DOCKER_TOPOLOGY_UNSUPPORTED: this runner requires a local Docker daemon.")
    if getattr(settings, "NEXUS_PRODUCTION", False):
        if not host_id() or host_id() == "local":
            raise exceptions.APIException("AGENT_DOCKER_HOST_REQUIRED: configure a stable dedicated worker identity.")
        if starting and not getattr(settings, "NEXUS_AGENT_RUNTIME_EGRESS_POLICY_READY", False):
            raise exceptions.APIException("AGENT_EGRESS_POLICY_REQUIRED: provision and verify worker egress filtering before hosting untrusted Agents.")
        if starting and not getattr(settings, "NEXUS_AGENT_IMAGE_ADMISSION_COMMAND", []):
            raise exceptions.APIException("AGENT_IMAGE_ADMISSION_REQUIRED: configure a digest-based image admission verifier.")


def verify_admission(digest):
    command = getattr(settings, "NEXUS_AGENT_IMAGE_ADMISSION_COMMAND", [])
    if not command:
        if getattr(settings, "NEXUS_PRODUCTION", False):
            raise exceptions.ValidationError("AGENT_IMAGE_ADMISSION_REQUIRED")
        return
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise exceptions.ValidationError("AGENT_IMAGE_IDENTITY_INVALID")
    if (not isinstance(command, list) or not 1 <= len(command) <= 32
            or not all(isinstance(x, str) and 0 < len(x) <= 2048
                       and not any(char in x for char in ("\x00", "\n", "\r")) for x in command)):
        raise exceptions.ValidationError("Image admission command must be an operator-configured argument list.")
    try:
        code = verifier_exit_code([*command, digest], timeout=180)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise exceptions.ValidationError("AGENT_IMAGE_ADMISSION_UNAVAILABLE") from None
    if code:
        # Scanner output may contain registry credentials or proprietary metadata.
        raise exceptions.ValidationError("AGENT_IMAGE_ADMISSION_REJECTED: the image did not pass the configured verification policy.")


def container_name(deployment):
    generation = (getattr(deployment, "docker_lifecycle", {}) or {}).get("generation", "initial")
    key = str(deployment.id).replace("-", "")
    if not re.fullmatch(r"[a-zA-Z0-9]+", key) or not re.fullmatch(r"[a-zA-Z0-9]+", generation):
        raise exceptions.ValidationError("Invalid Docker runtime identity.")
    return f"nexus-agent-{key}-{generation}"
