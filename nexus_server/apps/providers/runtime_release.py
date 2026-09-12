"""Admission of operator-approved provider images; never pull/build implicitly.

Receipts are trusted deployment policy, not signatures. Mount their directory
read-only from the release pipeline, never from a tenant or provider workspace.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

from django.conf import settings
from rest_framework.exceptions import APIException

RECIPE = "nexus-provider-release-v1"
GATES = {
    "codex_proxy": {"dependencies", "typescript", "auth-recovery"},
    "cliproxyapi": {"provider-regression", "credential-race"},
}


def _matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def approved_release(runtime_type: str) -> dict | None:
    directory = str(getattr(settings, "NEXUS_PROVIDER_RUNTIME_RELEASE_DIR", "")).strip()
    required = bool(getattr(settings, "NEXUS_PRODUCTION", False) or getattr(
        settings, "NEXUS_PROVIDER_RUNTIME_REQUIRE_VERIFIED_RELEASE", False
    ))
    if not directory and not required:
        return None  # Explicitly development-only compatibility.
    if not directory:
        raise APIException("Provider release approval is required. Configure a verified release directory.")
    if runtime_type not in GATES:
        raise APIException("Unsupported provider release type.")
    try:
        path = Path(directory) / f"{runtime_type}.json"
        if path.stat().st_size > 32768:
            raise ValueError
        receipt = json.loads(path.read_text(encoding="utf-8"))
        labels = receipt["labels"]
        valid = (
            receipt["schema"] == 1 and receipt["provider"] == runtime_type
            and receipt["status"] == "verified" and receipt["recipe"] == RECIPE
            and _matches(r"sha256:[a-f0-9]{64}", receipt["image_id"])
            and receipt["image_reference"] == receipt["image_id"]
            and _matches(r"[a-f0-9]{64}", receipt["patchset_sha256"])
            and _matches(r"[a-f0-9]{40}", receipt["upstream_commit"])
            and _matches(r"[a-f0-9]{40}", receipt["patched_tree"])
            and set(receipt["gates"]) == GATES[runtime_type]
            and labels["io.nexilume.provider"] == runtime_type
            and labels["io.nexilume.recipe"] == RECIPE
            and labels["io.nexilume.patchset"] == receipt["patchset_sha256"]
            and labels["io.nexilume.upstream"] == receipt["upstream_commit"]
            and _matches(r"[a-f0-9]{40}", labels["org.opencontainers.image.revision"])
            and _matches(r"\d+\.\d+\.\d+-nexus\.\d+", labels["io.nexilume.release"])
        )
        if not valid:
            raise ValueError
        return receipt
    except (OSError, ValueError, KeyError, TypeError):
        raise APIException("Provider release approval is missing or invalid; install a verified release receipt.") from None


def _inspect(arguments):
    try:
        result = subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise ValueError
        data = json.loads(result.stdout)
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise ValueError
        return data[0]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise APIException("Approved provider image/container is unavailable on this controller; load the verified image first.") from None


def verify_image(receipt: dict) -> None:
    image = _inspect(["image", "inspect", receipt["image_id"]])
    labels = (image.get("Config") or {}).get("Labels") or {}
    if image.get("Id") != receipt["image_id"] or any(
        labels.get(key) != value for key, value in receipt["labels"].items()
    ):
        raise APIException("Provider image does not match the approved Nexus release. No runtime was changed.")


def verify_existing_container(receipt: dict, container_id: str) -> None:
    container = _inspect(["container", "inspect", container_id])
    if container.get("Image") != receipt["image_id"]:
        raise APIException("Provider runtime uses a different release. Explicitly roll out the approved image; automatic recovery will not upgrade it.")
