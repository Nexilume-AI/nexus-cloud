from __future__ import annotations

import json
import mimetypes
import io
import tempfile
from pathlib import PurePosixPath
from typing import Any

from rest_framework import exceptions
from django.conf import settings

from apps.agents.models import AgentDisplayRun, AgentMemoryItem, AgentOutputArtifact
from .agent_asset_policy import get_mutable_agent, check_export_source
from apps.common.models import SoftDeleteModel

from .models import DatasetFile
from .services import create_dataset_file_from_upload, get_mutable_dataset, log_write
from .storage_backends import get_dataset_storage_backend


SOURCE_AGENT_TRACE = "agent_trace"
SOURCE_AGENT_MEMORY = "agent_memory"
SOURCE_AGENT_ARTIFACT = "agent_artifact"


def read_workspace_file(*, connection, root, path, runtime_context=None):
    # Legacy deployment captures use the real Workspace service. Invocation
    # snapshots must not load its Provider/Tool Setup integration graph.
    from apps.workspaces.services import read_workspace_file as implementation
    if runtime_context is not None:
        return implementation(connection=connection, root=root, path=path, runtime_context=runtime_context)
    return implementation(connection=connection, root=root, path=path)


def export_agent_trace_to_dataset(
    *,
    request,
    dataset_id: str,
    agent_id: str,
    run_id: str,
) -> DatasetFile:
    dataset, agent = resolve_export_context(request=request, dataset_id=dataset_id, agent_id=agent_id)
    run = (
        AgentDisplayRun.objects.filter(tenant=dataset.tenant, agent=agent, id=run_id)
        .select_related("runtime")
        .first()
    )
    if run is None:
        raise exceptions.NotFound("Agent display run not found.")
    check_export_source(request=request, agent=agent, run=run)
    if run.status != AgentDisplayRun.STATUS_COMPLETED:
        raise exceptions.ValidationError("Agent display run must be completed before trace export.")
    if run.redaction_status != AgentDisplayRun.REDACTION_PASSED:
        raise exceptions.ValidationError("Agent display run must pass system redaction before export.")
    events = run.events.order_by("seq")
    if not events.exists():
        raise exceptions.ValidationError("Agent display run has no events to export.")
    if events.filter(redacted_payload_json__isnull=True).exists():
        raise exceptions.ValidationError("Agent display run must be redacted before trace export.")
    rows = (
        {
            "event_id": str(event.id),
            "agent_id": str(event.agent_id),
            "run_id": str(event.run_id),
            "seq": event.seq,
            "event_type": event.event_type,
            "visibility": event.visibility,
            "payload": event.redacted_payload_json,
            "created_at": event.created_at.isoformat(),
        }
        for event in events.iterator(chunk_size=20)
    )
    dataset_file = create_jsonl_file(
        dataset=dataset,
        file_name=f"agent-runs/{agent.id}/{run.id}/trace.jsonl",
        rows=rows,
        uploaded_by=request.user,
        metadata={
            "source_type": SOURCE_AGENT_TRACE,
            "agent_id": str(agent.id),
            "run_id": str(run.id),
            "runtime_id": str(run.runtime_id or ""),
            "event_count": events.count(),
            "export_format": "jsonl",
            "redaction_status": run.redaction_status,
            "redaction_metadata": run.redaction_metadata,
            "sensitivity_level": "internal",
        },
    )
    log_write(
        request=request,
        action="datasets.agent_trace.export",
        dataset=dataset,
        metadata={"agent_id": str(agent.id), "run_id": str(run.id), "file_id": str(dataset_file.id)},
    )
    return dataset_file


def export_agent_memory_to_dataset(
    *,
    request,
    dataset_id: str,
    agent_id: str,
    memory_item_ids: list[str] | None = None,
) -> DatasetFile:
    dataset, agent = resolve_export_context(request=request, dataset_id=dataset_id, agent_id=agent_id)
    if not memory_item_ids:
        raise exceptions.ValidationError("Select at least one Agent memory item to export.")
    items = agent.memory_items.exclude(status=SoftDeleteModel.STATUS_DELETED).order_by("created_at")
    ids = [str(value) for value in memory_item_ids]
    items = items.filter(id__in=ids)
    check_export_source(request=request, agent=agent, memory_items=items)
    # Keep only bounded metadata in memory, never the selected memory contents.
    memory_items = list(items.values("id", "consent_status", "license_status", "sensitivity_level"))
    if not memory_items:
        raise exceptions.ValidationError("No Agent memory items matched the export request.")
    rows = (
        {
            "memory_item_id": str(item.id),
            "agent_id": str(item.agent_id),
            "memory_type": item.memory_type,
            "content_text": item.content_text,
            "content_json": item.content_json,
            "source_run_id": str(item.source_run_id or ""),
            "source_event_ids": item.source_event_ids,
            "confidence": str(item.confidence),
            "sensitivity_level": item.sensitivity_level,
            "consent_status": item.consent_status,
            "license_status": item.license_status,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        }
        for item in items.iterator(chunk_size=20)
    )
    dataset_file = create_jsonl_file(
        dataset=dataset,
        file_name=f"agent-memory/{agent.id}/memory-items.jsonl",
        rows=rows,
        uploaded_by=request.user,
        metadata={
            "source_type": SOURCE_AGENT_MEMORY,
            "agent_id": str(agent.id),
            "memory_item_ids": [str(item["id"]) for item in memory_items],
            "memory_count": len(memory_items),
            "export_format": "jsonl",
            "consent_status": aggregate_status([item["consent_status"] for item in memory_items]),
            "license_status": aggregate_status([item["license_status"] for item in memory_items]),
            "sensitivity_level": aggregate_sensitivity([item["sensitivity_level"] for item in memory_items]),
        },
    )
    log_write(
        request=request,
        action="datasets.agent_memory.export",
        dataset=dataset,
        metadata={"agent_id": str(agent.id), "file_id": str(dataset_file.id), "memory_count": len(memory_items)},
    )
    return dataset_file


def capture_agent_artifact_to_dataset(
    *,
    request,
    dataset_id: str,
    agent_id: str,
    artifact_id: str,
) -> DatasetFile:
    dataset, agent = resolve_export_context(request=request, dataset_id=dataset_id, agent_id=agent_id)
    artifact = (
        AgentOutputArtifact.objects.filter(tenant=dataset.tenant, agent=agent, id=artifact_id)
        .select_related("run", "runtime", "runtime__workspace_connection")
        .first()
    )
    if artifact is None:
        raise exceptions.NotFound("Agent output artifact not found.")
    check_export_source(request=request, agent=agent, artifact=artifact)
    run = artifact.run
    if run.status != AgentDisplayRun.STATUS_COMPLETED:
        raise exceptions.ValidationError("Agent display run must be completed before output capture.")
    if artifact.scan_status != AgentOutputArtifact.SCAN_PASSED:
        raise exceptions.ValidationError("Agent output artifact must pass system scan before capture.")
    if artifact.policy_status != AgentOutputArtifact.POLICY_APPROVED:
        raise exceptions.ValidationError("Agent output artifact policy must be approved before capture.")
    if artifact.license_status not in {AgentOutputArtifact.LICENSE_INTERNAL, AgentOutputArtifact.LICENSE_APPROVED}:
        raise exceptions.ValidationError("Agent output artifact license must be approved or internal before capture.")
    if not artifact.sha256:
        raise exceptions.ValidationError("Agent output artifact must have a scanned hash before capture.")

    runtime = artifact.runtime or run.runtime
    if run.run_kind == AgentDisplayRun.KIND_INVOCATION:
        if artifact.snapshot_status != "ready" or not artifact.snapshot_object_key:
            raise exceptions.ValidationError("Agent output snapshot is unavailable. Capture requires an immutable snapshot.")
        stream = get_dataset_storage_backend(artifact.snapshot_storage_backend).open(object_key=artifact.snapshot_object_key)
        size = artifact.size_bytes
    else:
        # Historical deployment runs retain their original, size-limited text path.
        if runtime is None or runtime.workspace_connection is None:
            raise exceptions.ValidationError("Agent run is not attached to a runtime workspace.")
        result = read_workspace_file(connection=runtime.workspace_connection,
            root=runtime.workspace_root or ".", path=artifact.workspace_path)
        content = str(result.get("content") or "").encode("utf-8")
        stream, size = io.BytesIO(content), len(content)
    output_name = artifact.original_file_name or PurePosixPath(str(artifact.workspace_path).replace("\\", "/")).name or "artifact.txt"
    content_type = artifact.content_type or mimetypes.guess_type(output_name)[0] or "text/plain"
    with stream:
        dataset_file = create_dataset_file_from_upload(
            dataset=dataset,
            uploaded_file=StreamUpload(stream, name=output_name, size=size, content_type=content_type),
            uploaded_by=request.user,
            expected_sha256=artifact.sha256,
            metadata={
                "source_type": SOURCE_AGENT_ARTIFACT,
                "agent_id": str(agent.id),
                "run_id": str(run.id),
                "runtime_id": str(runtime.id) if runtime else "",
                "artifact_id": str(artifact.id),
                "workspace_path": artifact.workspace_path,
                "original_file_name": artifact.original_file_name,
                "license_status": artifact.license_status,
                "scan_status": artifact.scan_status,
                "policy_status": artifact.policy_status,
                "scan_metadata": artifact.scan_metadata,
                "sensitivity_level": "internal",
            },
        )
    log_write(
        request=request,
        action="datasets.agent_artifact.capture",
        dataset=dataset,
        metadata={
            "agent_id": str(agent.id),
            "run_id": str(run.id),
            "artifact_id": str(artifact.id),
            "workspace_path": artifact.workspace_path,
            "file_id": str(dataset_file.id),
        },
    )
    return dataset_file


def resolve_export_context(*, request, dataset_id: str, agent_id: str):
    dataset = get_mutable_dataset(request=request, dataset_id=dataset_id)
    agent = get_mutable_agent(request=request, agent_id=agent_id)
    if agent.tenant_id != dataset.tenant_id:
        raise exceptions.PermissionDenied("Agent and dataset must belong to the same tenant.")
    return dataset, agent


class StreamUpload:
    def __init__(self, stream, *, name, size, content_type):
        self.stream, self.name, self.size, self.content_type = stream, name, size, content_type

    def chunks(self, chunk_size=256 * 1024):
        from .import_jobs import progress
        processed = 0
        progress("copying", 0, self.size, force=True)
        while True:
            chunk = self.stream.read(chunk_size)
            if not chunk:
                break
            processed += len(chunk)
            progress("copying", processed, self.size)
            yield chunk


def create_jsonl_file(*, rows, dataset, file_name, uploaded_by, metadata):
    from .import_jobs import progress
    import shutil
    # Disk spool makes the exact size available for quota reservation before storing.
    # RAM remains bounded by a single event, independently of the run's event count.
    max_bytes = int(getattr(settings, "NEXUS_DATASET_EXPORT_SPOOL_MAX_BYTES", 10 * 1024 ** 3))
    with tempfile.TemporaryFile(mode="w+b") as spool:
        next_space_check = 0
        progress("preparing", force=True)
        for row in rows:
            if spool.tell() >= next_space_check:
                if shutil.disk_usage(tempfile.gettempdir()).free < settings.NEXUS_DATASET_SPOOL_MIN_FREE_BYTES:
                    raise exceptions.ValidationError("Import paused by temporary disk capacity guard. Retry after capacity is restored.")
                next_space_check = spool.tell() + 8 * 1024 ** 2
            spool.write((json.dumps(row, separators=(",", ":"), default=str) + "\n").encode("utf-8"))
            progress("preparing", spool.tell())
            if spool.tell() > max_bytes:
                raise exceptions.ValidationError("Export exceeds the configured temporary storage limit.")
        size = spool.tell()
        spool.seek(0)
        return create_dataset_file_from_upload(dataset=dataset, uploaded_by=uploaded_by, metadata=metadata,
            uploaded_file=StreamUpload(spool, name=file_name, size=size, content_type="application/jsonl"))


def aggregate_status(values: list[str]) -> str:
    unique = {str(value or "") for value in values}
    if len(unique) == 1:
        return next(iter(unique))
    return "mixed"


def aggregate_sensitivity(values: list[str]) -> str:
    order = {
        AgentMemoryItem.SENSITIVITY_PUBLIC: 0,
        AgentMemoryItem.SENSITIVITY_INTERNAL: 1,
        AgentMemoryItem.SENSITIVITY_CONFIDENTIAL: 2,
        AgentMemoryItem.SENSITIVITY_RESTRICTED: 3,
    }
    if not values:
        return AgentMemoryItem.SENSITIVITY_INTERNAL
    return max(values, key=lambda value: order.get(value, 1))
