"""Bounded-memory, caller/Run-private large files. No binary data enters MCP/AG-UI."""
import hashlib
import io
import math
import re
import struct
import time
import uuid
import wave
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Sum, Q, Exists, OuterRef
from django.utils import timezone
from rest_framework import exceptions

from apps.common.subjects import request_subject
from apps.tenancy.models import Tenant
from apps.common.request_context import get_tenant_from_request
from apps.datasets.storage_backends import get_dataset_storage_backend
from .models import AgentFileTransfer, AgentFilePart, AgentOutputArtifact, AgentDisplayRun

CHUNK_SIZE = 1024 * 1024
STREAM_CHUNK = 256 * 1024
AUDIO_MAX_BYTES = 25 * 1024 * 1024
AUDIO_MAX_SECONDS = 5 * 60
AUDIO_CONTENT_TYPES = {
    "audio/webm", "video/webm", "audio/ogg", "application/ogg",
    "audio/wav", "audio/x-wav", "audio/mp4", "audio/m4a", "audio/x-m4a",
}


def limits():
    return {"max_file_bytes": int(getattr(settings, "NEXUS_AGENT_FILE_MAX_BYTES", 5 * 1024**3)),
        "chunk_bytes": CHUNK_SIZE, "max_files": 8}


def metadata(row):
    return {"file_id": str(row.pk), "name": row.name, "content_type": row.content_type,
        "turn_index": row.turn_index,
        "size_bytes": row.size_bytes, "received_bytes": row.received_bytes, "sha256": row.sha256,
        "state": row.state, "error_code": row.error_code, "chunk_bytes": CHUNK_SIZE,
        "artifact_id": str(row.artifact_id) if row.artifact_id else None,
        # run_output is internal provenance, not a new SDK enum value.
        "source_kind": "upload" if row.source_kind == "run_output" else row.source_kind,
        "source_label": "Run output reference" if row.source_kind == "run_output" else row.source_label,
        "expires_at": row.expires_at.isoformat()}


def owner_transfer(request, file_id, *, run=None):
    if run:
        query = AgentFileTransfer.objects.filter(run=run, caller_subject_hash=run.caller_subject_hash)
    else:
        tenant = get_tenant_from_request(request)
        query = AgentFileTransfer.objects.filter(tenant=tenant, caller_subject_hash=request_subject(request).subject_hash,
            project_id=getattr(request, "project_id", None) or None, direction="input")
    row = query.filter(pk=file_id).exclude(state__in=["canceled", "purged"]).first()
    if not row or (row.run_id is None and row.expires_at <= timezone.now()):
        raise exceptions.NotFound("File not found.")
    return row


@transaction.atomic
def create_transfer(*, request, data, run=None):
    from .runtime_services import get_runtime_use_agent, enforce_api_key_agent_policy, resolve_project_from_request
    tenant = run.consumer_tenant if run else get_tenant_from_request(request)
    if run and (run.run_kind != "invocation" or run.status != "running"):
        raise exceptions.NotFound("Active invocation not found.")
    agent = run.agent if run else get_runtime_use_agent(request=request, tenant=tenant, agent_id=data["agent_id"])
    if not run:
        enforce_api_key_agent_policy(api_key=getattr(request, "api_key", None), agent=agent)
    project = run.consumer_project if run else resolve_project_from_request(request=request, tenant=tenant)
    size = data["size_bytes"]
    idempotency_key = str(data.get("idempotency_key") or "").strip()
    if len(idempotency_key) > 128:
        raise exceptions.ValidationError({"idempotency_key": "Operation key is too long."})
    if not 0 <= size <= limits()["max_file_bytes"]:
        raise exceptions.ValidationError("File exceeds the configured size limit.")
    source_kind = str(data.get("source_kind") or "upload")
    content_type = str(data.get("content_type") or "application/octet-stream").lower()
    if source_kind == "audio" and (
        size > AUDIO_MAX_BYTES or content_type not in AUDIO_CONTENT_TYPES
    ):
        raise exceptions.ValidationError(
            {"source_kind": "Audio must be WebM/Opus, Ogg/Opus, WAV, or M4A and at most 25 MiB."}
        )
    name = re.sub(r'[\\/\x00-\x1f\x7f]', '_', data["name"]).strip()[:255]
    if not name or name in {".", ".."}:
        raise exceptions.ValidationError({"name": "A file name is required."})
    if run is not None and idempotency_key:
        existing = AgentFileTransfer.objects.filter(
            run=run,
            idempotency_key=idempotency_key,
        ).first()
        if existing is not None:
            if (
                existing.name != name
                or existing.size_bytes != size
                or existing.content_type != content_type
                or existing.expected_sha256 != str(data.get("sha256") or "")
            ):
                raise exceptions.ValidationError({"code": "FILE_OPERATION_DIVERGED"})
            return existing
    # Reservation includes ready files as well as uploads; no unbounded disk staging.
    Tenant.objects.select_for_update(no_key=True).get(pk=tenant.pk)
    reserved = AgentFileTransfer.objects.filter(tenant=tenant).exclude(state="purged").exclude(source_kind="run_output")
    total = reserved.aggregate(total=Sum("size_bytes"))["total"] or 0
    cap = int(getattr(settings, "NEXUS_AGENT_FILE_TENANT_BYTES", 50 * 1024**3))
    if total + size > cap or reserved.filter(state__in=["uploading", "queued", "processing"]).count() >= 32:
        raise exceptions.ValidationError("Agent file storage or active-transfer limit reached. Cancel unused uploads.")
    turn_index = None
    if run is not None:
        task = getattr(run, "execution_task", None)
        turn_index = max(int((getattr(task, "request_json", {}) or {}).get("turn_index") or 1), 1)
    return AgentFileTransfer.objects.create(tenant=tenant, project=project,
        agent=agent, run=run, caller_subject_hash=run.caller_subject_hash if run else request_subject(request).subject_hash,
        turn_index=turn_index,
        direction="output" if run else "input", name=name, content_type=content_type,
        source_kind=source_kind,
        source_label=str(data.get("source_label") or "")[:512],
        idempotency_key=idempotency_key,
        size_bytes=size, expected_sha256=data.get("sha256", ""), storage_backend=get_dataset_storage_backend().name,
        computer_revision=run.computer_revision if run else 0, expires_at=timezone.now() + timedelta(hours=24))


def put_part(row, *, offset, body, digest):
    if offset < 0 or offset % CHUNK_SIZE or not body or len(body) > CHUNK_SIZE:
        raise exceptions.ValidationError("Invalid file chunk boundary.")
    actual = hashlib.sha256(body).hexdigest()
    if actual != digest:
        raise exceptions.ValidationError("Chunk SHA-256 mismatch.")
    # Short per-transfer lock bounds concurrent retries. Only one MiB is written.
    with transaction.atomic():
        locked = AgentFileTransfer.objects.select_for_update().get(pk=row.pk)
        index = offset // CHUNK_SIZE
        existing = locked.parts.filter(index=index).first()
        if existing:
            if existing.sha256 != actual or existing.size_bytes != len(body):
                raise FileConflict("This chunk was already uploaded with different content.")
            if locked.received_bytes > offset:
                return locked
        if locked.state != "uploading" or locked.expires_at <= timezone.now():
            raise FileConflict("This upload is closed or expired.")
        if offset != locked.received_bytes or len(body) != min(CHUNK_SIZE, locked.size_bytes - offset):
            raise FileConflict("Upload offset changed. Refresh its status and resume.")
        if not existing:
            existing = AgentFilePart.objects.create(transfer=locked, index=index,
                object_key=f"{locked.tenant_id}/agent-files/{locked.id}/parts/{index}", size_bytes=len(body), sha256=actual)
    # Commit the reserved key before storage I/O so a process crash never leaves
    # an untracked object. Retries reuse that exact key and serialized offset.
    with transaction.atomic():
        locked = AgentFileTransfer.objects.select_for_update().get(pk=row.pk)
        if locked.received_bytes > offset:
            return locked
        if locked.state != "uploading" or locked.expires_at <= timezone.now():
            raise FileConflict("This upload is closed or expired.")
        upload = ContentFile(body)
        upload.object_key = existing.object_key
        backend = get_dataset_storage_backend(locked.storage_backend)
        try:
            if backend.name == "local":
                # This transfer lock excludes writers; clear only this reserved
                # chunk's temporary file left by a terminated process.
                backend.delete(object_key=existing.object_key + ".part")
            backend.save(tenant=locked.tenant, dataset_id=f"agent-files/{locked.id}/parts", file_name=str(index), uploaded_file=upload)
        except Exception:
            raise FileUnavailable() from None
        locked.received_bytes += len(body)
        locked.save(update_fields=["received_bytes", "updated_at"])
        return locked


class FileConflict(exceptions.APIException):
    status_code = 409
    default_code = "AGENT_FILE_CONFLICT"
    default_detail = "File transfer state changed. Refresh and retry."


class FileChangedDuringImport(FileConflict):
    default_code = "FILE_CHANGED_DURING_IMPORT"
    default_detail = "The Computer file changed while it was being imported. Refresh and retry."


class AudioInputNotSupported(exceptions.APIException):
    status_code = 400
    default_code = "AUDIO_INPUT_NOT_SUPPORTED"
    default_detail = "This Agent tool does not accept audio input."


class FileUnavailable(exceptions.APIException):
    status_code = 503
    default_code = "AGENT_FILE_STORAGE_UNAVAILABLE"
    default_detail = "File storage is temporarily unavailable. Retry the same chunk."


@transaction.atomic
def complete_transfer(row):
    row = AgentFileTransfer.objects.select_for_update().get(pk=row.pk)
    if row.state in {"queued", "processing", "ready"}:
        return row
    if row.state not in {"uploading", "failed"} or row.received_bytes != row.size_bytes or row.expires_at <= timezone.now():
        raise FileConflict("Upload is incomplete or expired.")
    row.state, row.error_code = "queued", ""
    row.save(update_fields=["state", "error_code", "updated_at"])
    return row


@transaction.atomic
def cancel_transfer(row):
    row = AgentFileTransfer.objects.select_for_update().get(pk=row.pk)
    if row.run_id and row.state == "ready":
        raise FileConflict("A delivered Run file cannot be canceled as a draft.")
    row.state = "canceled"
    row.lease_id = None
    row.save(update_fields=["state", "lease_id", "updated_at"])


class PartsUpload:
    def __init__(self, row):
        self.row, self.object_key, self.content_type = row, row.object_key, row.content_type
        self.name, self.size = row.name, row.size_bytes

    def chunks(self):
        backend = get_dataset_storage_backend(self.row.storage_backend)
        digest, count, tick = hashlib.sha256(), 0, 0
        for part in self.row.parts.filter(index__gte=0).order_by("index").iterator(chunk_size=100):
            stream = backend.open(object_key=part.object_key)
            part_hash = hashlib.sha256()
            try:
                while True:
                    chunk = stream.read(STREAM_CHUNK)
                    if not chunk:
                        break
                    if time.monotonic() - tick > 10:
                        if not AgentFileTransfer.objects.filter(pk=self.row.pk, lease_id=self.row.lease_id, state="processing").update(
                            lease_expires_at=timezone.now() + timedelta(minutes=2)):
                            raise RuntimeError("TRANSFER_FENCED")
                        tick = time.monotonic()
                    digest.update(chunk); part_hash.update(chunk); count += len(chunk)
                    if count > self.row.size_bytes:
                        raise ValueError("FILE_SIZE_MISMATCH")
                    yield chunk
                if part_hash.hexdigest() != part.sha256:
                    raise ValueError("CHUNK_INTEGRITY_MISMATCH")
            finally:
                stream.close()
        if count != self.row.size_bytes or (self.row.expected_sha256 and digest.hexdigest() != self.row.expected_sha256):
            raise ValueError("FILE_INTEGRITY_MISMATCH")


def process_one():
    with transaction.atomic():
        row = AgentFileTransfer.objects.select_for_update(skip_locked=True).filter(
            Q(state="queued") | Q(state="processing", lease_expires_at__lt=timezone.now()), expires_at__gt=timezone.now()).first()
        if not row:
            return False
        row.state, row.lease_id = "processing", uuid.uuid4()
        row.attempts += 1
        row.lease_expires_at = timezone.now() + timedelta(minutes=2)
        row.object_key = f"{row.tenant_id}/agent-files/{row.id}/{row.lease_id}_file"
        row.save()
        AgentFilePart.objects.create(transfer=row, index=-row.attempts, object_key=row.object_key, size_bytes=row.size_bytes, sha256="")
    backend = get_dataset_storage_backend(row.storage_backend)
    try:
        stored = backend.save(tenant=row.tenant, dataset_id=f"agent-files/{row.id}", file_name=row.name, uploaded_file=PartsUpload(row))
        if row.source_kind == "audio":
            stream = backend.open(object_key=stored.object_key)
            try:
                audio_bytes = stream.read(AUDIO_MAX_BYTES + 1)
            finally:
                stream.close()
            signature = audio_bytes[:16]
            valid_audio = (
                signature.startswith(b"\x1aE\xdf\xa3")
                or signature.startswith(b"OggS")
                or (signature.startswith(b"RIFF") and signature[8:12] == b"WAVE")
                or (len(signature) >= 12 and signature[4:8] == b"ftyp")
            )
            if not valid_audio:
                raise ValueError("INVALID_AUDIO_CONTAINER")
            duration = audio_duration_seconds(audio_bytes, row.content_type)
            if duration is None or duration <= 0 or duration > AUDIO_MAX_SECONDS:
                raise ValueError("INVALID_AUDIO_DURATION")
        with transaction.atomic():
            locked = AgentFileTransfer.objects.select_for_update().get(pk=row.pk)
            if locked.state != "processing" or locked.lease_id != row.lease_id:
                raise RuntimeError("TRANSFER_FENCED")
            if locked.direction == "output":
                artifact = AgentOutputArtifact.objects.create(tenant=locked.run.tenant, project=locked.run.agent.project,
                    agent=locked.agent, run=locked.run, turn_index=locked.turn_index,
                    runtime=locked.run.runtime, computer_revision=locked.computer_revision,
                    workspace_path=f"uploads/{locked.id}/{locked.name}", original_file_name=locked.name,
                    content_type=locked.content_type, size_bytes=stored.size_bytes, sha256=stored.sha256,
                    snapshot_status="ready", snapshot_storage_backend=stored.backend, snapshot_object_key=stored.object_key,
                    snapshotted_at=timezone.now(), producer_step="sdk.file_upload")
                locked.artifact = artifact
            locked.state, locked.sha256, locked.error_code = "ready", stored.sha256, ""
            locked.save()
    except Exception:
        # Raw backend errors can contain object endpoints or credentials.
        AgentFileTransfer.objects.filter(pk=row.pk, lease_id=row.lease_id, state="processing").update(state="failed", error_code="FILE_FINALIZATION_FAILED")
        try:
            backend.delete(object_key=row.object_key)
        except Exception:
            pass
    return True


def audio_duration_seconds(data: bytes, content_type: str) -> float | None:
    """Read duration from a bounded accepted container without invoking external tools."""

    media_type = str(content_type or "").split(";", 1)[0].strip().lower()
    try:
        if media_type in {"audio/wav", "audio/x-wav"}:
            with wave.open(io.BytesIO(data), "rb") as source:
                rate = source.getframerate()
                return source.getnframes() / rate if rate > 0 else None
        if media_type in {"audio/ogg", "application/ogg"}:
            if b"OpusHead" not in data[:4096]:
                return None
            granule = 0
            offset = 0
            while True:
                offset = data.find(b"OggS", offset)
                if offset < 0 or offset + 14 > len(data):
                    break
                granule = max(granule, int.from_bytes(data[offset + 6:offset + 14], "little"))
                offset += 4
            return granule / 48_000 if granule else None
        if media_type in {"audio/mp4", "audio/m4a", "audio/x-m4a"}:
            marker = data.find(b"mvhd")
            if marker < 0 or marker + 32 > len(data):
                return None
            version = data[marker + 4]
            if version == 0:
                scale = int.from_bytes(data[marker + 16:marker + 20], "big")
                duration = int.from_bytes(data[marker + 20:marker + 24], "big")
            elif version == 1:
                scale = int.from_bytes(data[marker + 24:marker + 28], "big")
                duration = int.from_bytes(data[marker + 28:marker + 36], "big")
            else:
                return None
            return duration / scale if scale else None
        if media_type in {"audio/webm", "video/webm"}:
            scale = _ebml_unsigned(data, b"\x2a\xd7\xb1") or 1_000_000
            duration = _ebml_float(data, b"\x44\x89")
            seconds = duration * scale / 1_000_000_000 if duration is not None else None
            return seconds if seconds is not None and math.isfinite(seconds) else None
    except (EOFError, OSError, ValueError, wave.Error, struct.error):
        return None
    return None


def _ebml_value(data: bytes, marker: bytes) -> bytes | None:
    offset = data.find(marker)
    if offset < 0:
        return None
    cursor = offset + len(marker)
    if cursor >= len(data):
        return None
    first = data[cursor]
    mask, width = 0x80, 1
    while width <= 8 and not first & mask:
        mask >>= 1
        width += 1
    if width > 8 or cursor + width > len(data):
        return None
    size = first & (mask - 1)
    for byte in data[cursor + 1:cursor + width]:
        size = (size << 8) | byte
    start = cursor + width
    return data[start:start + size] if 0 < size <= 8 and start + size <= len(data) else None


def _ebml_unsigned(data: bytes, marker: bytes) -> int | None:
    value = _ebml_value(data, marker)
    return int.from_bytes(value, "big") if value else None


def _ebml_float(data: bytes, marker: bytes) -> float | None:
    value = _ebml_value(data, marker)
    if value is None or len(value) not in {4, 8}:
        return None
    return float(struct.unpack(">f" if len(value) == 4 else ">d", value)[0])


def cleanup(limit=50):
    AgentFileTransfer.objects.filter(run__isnull=True, expires_at__lt=timezone.now()).exclude(state__in=["canceled", "purged"]).update(state="canceled", lease_id=None)
    AgentFileTransfer.objects.filter(state__in=["uploading", "queued", "processing"], expires_at__lt=timezone.now()).update(state="canceled", lease_id=None)
    # Clean committed parts and abandoned attempts, retaining the immutable object.
    disposable = AgentFilePart.objects.filter(transfer_id=OuterRef("pk")).exclude(object_key=OuterRef("object_key"))
    candidates = AgentFileTransfer.objects.annotate(has_parts=Exists(disposable)).filter(
        Q(state="canceled") | Q(state="failed", parts__index__lt=0) | Q(state="ready", has_parts=True)).values_list("pk", flat=True).distinct()[:limit]
    for pk in list(candidates):
        # Recheck under the same lock used by retry/cancel/finalization. Never
        # delete an object that a concurrent retry has just started writing.
        with transaction.atomic():
            row = AgentFileTransfer.objects.select_for_update(skip_locked=True).filter(pk=pk, state__in=["ready", "failed", "canceled"]).first()
            if not row:
                continue
            backend = get_dataset_storage_backend(row.storage_backend)
            parts = row.parts.exclude(object_key=row.object_key if row.state == "ready" else "")
            if row.state == "failed":
                parts = parts.filter(index__lt=0)
            for part in parts.all()[:100]:
                try:
                    backend.delete(object_key=part.object_key)
                    if backend.name == "local":
                        backend.delete(object_key=part.object_key + ".part")
                    part.delete()
                except Exception:
                    continue
            if row.state == "canceled" and not row.parts.exists():
                AgentFileTransfer.objects.filter(pk=row.pk, state="canceled").update(state="purged")


def prepare_files(*, request, descriptor, references, agent_id, run=None):
    if not references:
        return []
    schema = descriptor.get("input_schema", {}).get("properties", {}).get("files", {})
    if not isinstance(schema, dict) or schema.get("type") != "array":
        raise exceptions.ValidationError({"files": "This Agent tool does not declare file inputs."})
    if not isinstance(references, list) or len(references) > 8 or len(set(map(str, references))) != len(references):
        raise exceptions.ValidationError({"files": "Provide at most eight distinct file IDs."})
    rows = []
    for reference in references:
        try:
            row = owner_transfer(request, uuid.UUID(str(reference)))
        except (ValueError, TypeError):
            raise exceptions.NotFound("File not found.") from None
        if row.source_kind == "audio" or row.agent_id != agent_id or row.state != "ready" or (row.run_id and (not run or row.run_id != run.pk)):
            raise exceptions.NotFound("File not found or not ready for this Run.")
        from .file_directory import validate_reference
        validate_reference(row)
        rows.append(row)
    return rows


def prepare_audio(*, request, descriptor, references, agent_id, run=None):
    if not references:
        return []
    schema = descriptor.get("input_schema", {}).get("properties", {}).get("audio", {})
    modalities = set(descriptor.get("input_modalities") or [])
    if "audio" not in modalities or not isinstance(schema, dict) or schema.get("type") != "array":
        raise AudioInputNotSupported()
    if not isinstance(references, list) or len(references) > 8 or len(set(map(str, references))) != len(references):
        raise exceptions.ValidationError({"audio": "Provide at most eight distinct audio file IDs."})
    rows = []
    for reference in references:
        try:
            row = owner_transfer(request, uuid.UUID(str(reference)))
        except (ValueError, TypeError):
            raise exceptions.NotFound("Audio file not found.") from None
        if row.source_kind != "audio" or row.agent_id != agent_id or row.state != "ready" or (row.run_id and (not run or row.run_id != run.pk)):
            raise exceptions.NotFound("Audio file not found or not ready for this Run.")
        rows.append(row)
    return rows


def file_arguments(rows):
    return [{k: metadata(row)[k] for k in (
        "file_id", "name", "content_type", "size_bytes", "sha256", "source_kind", "source_label"
    )} for row in rows]


def tool_file_arguments(references, schema):
    """Project additive provenance metadata onto the tool's declared contract.

    Run Context and stored snapshots retain full provenance. Legacy MCP file
    references keep their original five fields; newer tools opt in to each
    provenance field in items.properties. Never strip arbitrary tool arguments
    or core file fields to make an invalid request pass validation.
    """
    items = schema.get("items", {}) if isinstance(schema, dict) else {}
    properties = items.get("properties", {}) if isinstance(items, dict) else {}
    declared = properties if isinstance(properties, dict) else {}
    omitted = {"source_kind", "source_label"} - declared.keys()
    return [{key: value for key, value in reference.items() if key not in omitted}
            for reference in references]


def bind_files(*, run, rows, turn_index: int | None = 1):
    # Called in the same transaction that creates/resumes the Run.
    blocks = []
    for row in sorted(rows, key=lambda item: str(item.pk)):
        from .file_directory import validate_reference
        validate_reference(row)
        changed = AgentFileTransfer.objects.filter(pk=row.pk, state="ready", caller_subject_hash=run.caller_subject_hash,
            tenant_id=run.consumer_tenant_id, project_id=run.consumer_project_id, agent_id=run.agent_id).filter(Q(run__isnull=True) | Q(run=run)).update(run=run)
        if not changed:
            raise FileConflict("This file was attached elsewhere or canceled. Refresh before sending.")
        # Record the first Turn that consumes a pre-Run immutable snapshot.
        # Reusing that snapshot later must not rewrite its original lineage.
        if turn_index is not None:
            AgentFileTransfer.objects.filter(pk=row.pk, turn_index__isnull=True).update(
                turn_index=max(int(turn_index or 1), 1)
            )
    # Preserve the caller's attachment order, not the database lock order. These
    # are private download references, never storage URLs or Computer paths.
    for row in rows:
        blocks.append({"type": "file", "name": row.name, "content_type": row.content_type,
            "size_bytes": row.size_bytes, "status": "attached",
            "url": f"/api/v1/agent-runs/{run.pk}/files/{row.pk}/download/"})
    return blocks


@transaction.atomic
def snapshot_computer_text(*, request, agent, relative_path: str, content: str, source_size: int):
    """Store a caller Computer text file as an immutable pre-Run input."""

    body = content.encode("utf-8")
    if len(body) != int(source_size):
        raise FileChangedDuringImport()
    digest = hashlib.sha256(body).hexdigest()
    row = create_transfer(
        request=request,
        data={
            "agent_id": agent.id,
            "name": posix_name(relative_path),
            "size_bytes": len(body),
            "content_type": "text/plain; charset=utf-8",
            "sha256": digest,
            "source_kind": "computer",
            "source_label": relative_path,
        },
    )
    row.state = "processing"
    row.lease_id = uuid.uuid4()
    row.object_key = f"{row.tenant_id}/agent-files/{row.id}/{row.lease_id}_file"
    row.save(update_fields=["state", "lease_id", "object_key", "updated_at"])
    upload = ContentFile(body)
    upload.object_key = row.object_key
    backend = get_dataset_storage_backend(row.storage_backend)
    try:
        stored = backend.save(
            tenant=row.tenant,
            dataset_id=f"agent-files/{row.id}",
            file_name=row.name,
            uploaded_file=upload,
        )
    except Exception:
        AgentFileTransfer.objects.filter(pk=row.pk).update(state="failed", error_code="FILE_IMPORT_FAILED")
        raise FileUnavailable() from None
    row.state = "ready"
    row.object_key = stored.object_key
    row.size_bytes = stored.size_bytes
    row.received_bytes = stored.size_bytes
    row.sha256 = stored.sha256
    row.error_code = ""
    row.save(update_fields=["state", "object_key", "size_bytes", "received_bytes", "sha256", "error_code", "updated_at"])
    return row


def posix_name(path: str) -> str:
    value = str(path or "").replace("\\", "/").rstrip("/")
    return value.rsplit("/", 1)[-1] or "computer-file.txt"


@transaction.atomic
def create_mcp_file_context(*, request, runtime, tool_name, body):
    """Bind standard tools/call file references before dispatch, atomically with Run."""
    import json
    from .runtime_services import create_invocation_display_context
    from .tool_catalog import effective_mcp_tools
    payload = json.loads(body)
    arguments = (payload.get("params") or {}).get("arguments") or {}
    refs = arguments.get("files") if isinstance(arguments, dict) else None
    rows = []
    if refs:
        descriptor = next((item for item in effective_mcp_tools(agent=runtime.agent, runtime=runtime) if item["name"] == tool_name), {})
        if not isinstance(refs, list) or any(not isinstance(item, dict) or not item.get("file_id") for item in refs):
            raise exceptions.ValidationError({"files": "Use uploaded file references with file_id."})
        rows = prepare_files(request=request, descriptor=descriptor, references=[item["file_id"] for item in refs], agent_id=runtime.agent_id)
        # Ignore client-supplied name, size, URLs and checksums.
        arguments["files"] = tool_file_arguments(file_arguments(rows),
            descriptor.get("input_schema", {}).get("properties", {}).get("files", {}))
        payload["params"]["arguments"] = arguments
        body = json.dumps(payload).encode()
    run, context = create_invocation_display_context(runtime=runtime, tool_name=tool_name, request=request)
    bind_files(run=run, rows=rows)
    return run, context, body
