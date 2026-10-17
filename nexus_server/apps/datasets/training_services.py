"""Training workbench on immutable Dataset files and existing storage quotas."""
import json
import uuid
import hashlib
import logging
from contextvars import ContextVar
from functools import wraps

from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework import exceptions
from apps.common.redaction import redact_payload
from apps.tenancy.models import Tenant
from .models import Dataset, DatasetFile, DatasetVersion
from .services import get_mutable_dataset, create_dataset_file_from_bytes, version_file_metadata, log_write
from .storage_backends import get_dataset_storage_backend
from .training_schema import SCHEMA, MAX_BYTES, encoded, digest, content_digest, normalize_trajectory, training_row, assign_splits, messages_from_events

_writes = ContextVar("training_writes", default=None)


def cleanup_failed_writes(fn):
    """Remove new objects after a rolled-back multi-file export, never existing data."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        writes = []
        token = _writes.set(writes)
        try:
            return fn(*args, **kwargs)
        except Exception:
            for file in writes:
                try:
                    if not DatasetFile.objects.filter(pk=file.pk).exists():
                        get_dataset_storage_backend(file.storage_backend).delete(object_key=file.object_key)
                except Exception:
                    logging.getLogger(__name__).warning("Training object cleanup deferred for file %s", file.pk)
            raise
        finally:
            _writes.reset(token)
    return wrapped


def sample_rows(dataset):
    return dataset.files.filter(status="active", metadata_json__training__kind="sample")


def require_private(dataset):
    if dataset.visibility != "private":
        raise exceptions.ValidationError("Use a private collection to prepare training data. Unpublish this collection before changing its training samples or exports.")


def sample_summary(file):
    training = {k: v for k, v in file.metadata_json["training"].items() if k not in {"request_key", "request_hash"}}
    return {"id": str(file.id), "created_at": file.created_at.isoformat(), "sha256": file.sha256, **training}


def sample_file(request, dataset_id, sample_id, lock=False):
    dataset = get_mutable_dataset(request=request, dataset_id=dataset_id)
    rows = sample_rows(dataset)
    file = (rows.select_for_update() if lock else rows).filter(id=sample_id).first()
    if file is None:
        raise exceptions.NotFound("Training sample not found.")
    return dataset, file


def read_sample(file):
    with get_dataset_storage_backend(file.storage_backend).open(object_key=file.object_key) as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != file.sha256:
        raise exceptions.ValidationError("Training source failed its size or SHA-256 integrity check.")
    return json.loads(raw)


def store_json(dataset, user, name, value, metadata):
    raw = encoded(value) + b"\n"
    return store_bytes(dataset, user, name, raw, metadata)


def store_bytes(dataset, user, name, raw, metadata):
    # Training has a known JSON schema. The generic lexical scanner treats
    # timestamps, numerical tool arguments and numeric call IDs as phone numbers.
    # Validate/redact content structurally while retaining protocol identifiers.
    kind = metadata["training"]["kind"]
    if kind in {"sample", "sft"}:
        for line in raw.splitlines():
            value = json.loads(line)
            if kind == "sample":
                normalized, _, _ = normalize_trajectory(value)
                expected = normalized
            else:
                normalized, warnings, _ = normalize_trajectory({**value, "context": {"task_id": "export-validation"}})
                if warnings:
                    raise exceptions.ValidationError("A training row failed structural validation.")
                expected = {"messages": normalized["messages"], **({"tools": normalized["tools"]} if normalized["tools"] else {})}
            if encoded(expected) != encoded(value):
                raise exceptions.ValidationError("Training content failed the structured redaction check.")
    elif kind != "recipe":
        raise exceptions.ValidationError("Unknown training file format.")
    # Recipes are composed only here from verified identities, hashes and
    # sanitized annotations. No arbitrary caller-supplied metadata is accepted.
    stats = {"pipeline": "training_schema_v1", "scope": "structured_text", "finding_count": 0,
             "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw), "error_code": ""}
    file = create_dataset_file_from_bytes(dataset=dataset, uploaded_by=user, file_name=name,
        content=raw, content_type="application/jsonl", metadata={
            "source_type": "user_upload", "license_status": "internal", "consent_status": "approved",
            "scan_status": "passed", "policy_status": "approved", "scan_metadata": stats,
            "sensitivity_level": "internal", **metadata})
    if _writes.get() is not None:
        _writes.get().append(file)
    return file


@cleanup_failed_writes
@transaction.atomic
def ingest(request, dataset_id, *, trajectory, request_key, rights_confirmed, correction_of=None, source_run=None, extra_warnings=()):
    if rights_confirmed is not True:
        raise exceptions.ValidationError("Confirm the right to store and use this trajectory for training.")
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128:
        raise exceptions.ValidationError("A stable request_key (1–128 characters) is required.")
    dataset = get_mutable_dataset(request=request, dataset_id=dataset_id)
    normalized, warnings, group_keys = normalize_trajectory(trajectory)
    if len(encoded(normalized)) + 1 > MAX_BYTES:
        raise exceptions.ValidationError("Normalized trajectory exceeds 1 MiB.")
    require_private(dataset)
    # Hash submitted identity, never store potentially secret idempotency keys.
    key = digest([str(request.user.pk), request_key])
    source = {"kind": "nexus_run", "agent_id": str(source_run.agent_id), "run_id": str(source_run.id)} if source_run else {"kind": "external_client", "reported_by": str(request.user.pk)}
    if correction_of:
        _, original = sample_file(request, dataset_id, correction_of)
        original_training = original.metadata_json["training"]
        source = {"kind": "correction", "sample_id": str(original.id), "source_sha256": original.sha256, "reported_by": str(request.user.pk)}
        group_keys = sorted(set(group_keys + original_training["group_keys"] + ["content:" + original_training["content_hash"]]))
    identity = salted_hmac("training-submission", encoded([trajectory, source]), algorithm="sha256").hexdigest()
    Tenant.objects.select_for_update(no_key=True).get(pk=dataset.tenant_id)
    dataset = get_mutable_dataset(request=request, dataset_id=dataset_id)
    require_private(dataset)
    previous = sample_rows(dataset).filter(metadata_json__training__request_key=key).first()
    if previous:
        if previous.metadata_json["training"]["request_hash"] != identity:
            raise exceptions.ValidationError("request_key was already used with different trajectory content.")
        return previous
    content_hash = content_digest(normalized)
    warning_list = sorted(set(warnings) | set(extra_warnings))
    training = {"kind": "sample", "schema": SCHEMA, "source": source, "request_key": key,
        "request_hash": identity, "content_hash": content_hash, "group_keys": group_keys,
        "context": normalized["context"], "execution_status": normalized["execution_status"],
        "message_count": len(normalized["messages"]), "tool_count": len(normalized["tools"]),
        "warnings": warning_list, "revision": 1,
        "annotation": {"decision": "pending", "outcome": "unknown", "score": None, "feedback": "", "feedback_source": "human", "trainable_assistant_indices": None}}
    file = store_json(dataset, request.user, f"training/samples/{uuid.uuid4().hex}.jsonl", normalized,
        {"training": training, "redaction_status": "passed", "redaction_metadata": {"pipeline": "training_rules_v1"}})
    log_write(request=request, action="datasets.training.ingest", dataset=dataset, metadata={"file_id": str(file.id), "source_kind": source["kind"]})
    return file


@transaction.atomic
def annotate(request, dataset_id, sample_id, data):
    # Same lock ordering as imports/versions prevents quota and annotation races.
    dataset = get_mutable_dataset(request=request, dataset_id=dataset_id)
    require_private(dataset)
    Tenant.objects.select_for_update(no_key=True).get(pk=dataset.tenant_id)
    dataset, file = sample_file(request, dataset_id, sample_id, lock=True)
    require_private(dataset)
    training = file.metadata_json["training"]
    if data["revision"] != training["revision"]:
        raise exceptions.ValidationError("The sample changed. Reload before saving your review.")
    annotation = {k: v for k, v in data.items() if k != "revision"}
    annotation["feedback"] = redact_payload(annotation.get("feedback", ""))[0]
    selected = annotation.get("trainable_assistant_indices")
    trajectory = read_sample(file)
    if selected is not None and (len(set(selected)) != len(selected) or any(i >= len(trajectory["messages"]) or trajectory["messages"][i]["role"] != "assistant" for i in selected)):
        raise exceptions.ValidationError("Training targets must be distinct assistant message indices.")
    if annotation["decision"] == "approved" and training["warnings"]:
        raise exceptions.ValidationError("This trajectory has structural warnings. Import a corrected complete trajectory before approving it.")
    if annotation["decision"] == "approved" and not any(m["role"] == "assistant" and (m["content"] or m.get("tool_calls")) and (i in selected if selected is not None else m.get("weight", 1)) for i, m in enumerate(trajectory["messages"])):
        raise exceptions.ValidationError("Select at least one non-empty assistant training target.")
    annotation.update(reviewed_by=str(request.user.pk), reviewed_at=timezone.now().isoformat())
    training.update(annotation=annotation, revision=training["revision"] + 1)
    file.save(update_fields=["metadata_json", "updated_at"])
    log_write(request=request, action="datasets.training.annotate", dataset=dataset, metadata={"file_id": str(file.id), "revision": training["revision"]})
    return file


def import_run(request, dataset_id, agent_id, run_id, request_key, rights_confirmed):
    from .agent_asset_services import resolve_export_context
    from .agent_asset_policy import check_export_source
    dataset, agent = resolve_export_context(request=request, dataset_id=dataset_id, agent_id=agent_id)
    run = agent.display_runs.filter(tenant=dataset.tenant, id=run_id).first()
    if run is None:
        raise exceptions.NotFound("Agent run not found.")
    check_export_source(request=request, agent=agent, run=run)
    if run.status not in {"completed", "failed"} or run.redaction_status != "passed":
        raise exceptions.ValidationError("Finish and redact the source run before collecting a training sample.")
    events = list(run.events.order_by("seq")[:5001])
    if not events or len(events) > 5000 or any(e.redacted_payload_json is None for e in events):
        raise exceptions.ValidationError("A source run must have 1–5000 redacted events.")
    messages, warnings = messages_from_events(events)
    return ingest(request, dataset_id, trajectory={"messages": messages, "tools": [],
        "execution_status": run.status, "context": {"task_id": str(run.id), "client": "nexus-display"}},
        request_key=request_key, rights_confirmed=rights_confirmed, source_run=run, extra_warnings=warnings)


@cleanup_failed_writes
@transaction.atomic
def export_sft(request, dataset_id, *, request_key, seed, validation_percent, test_percent, include_failures=False):
    if validation_percent + test_percent >= 100:
        raise exceptions.ValidationError("Leave at least 1% for training.")
    if redact_payload(seed)[0] != seed:
        raise exceptions.ValidationError("Use a non-sensitive split seed.")
    dataset = get_mutable_dataset(request=request, dataset_id=dataset_id)
    Tenant.objects.select_for_update(no_key=True).get(pk=dataset.tenant_id)
    dataset = Dataset.objects.select_for_update(no_key=True).get(pk=dataset.pk)
    require_private(dataset)
    key = digest([str(request.user.pk), request_key])
    settings = {"seed": seed, "validation_percent": validation_percent, "test_percent": test_percent, "include_failures": include_failures}
    previous = dataset.versions.filter(snapshot_json__training__request_key=key).first()
    if previous:
        if previous.snapshot_json["training"]["settings"] != settings:
            raise exceptions.ValidationError("This export request_key was already used with different settings.")
        return previous
    # Include all samples in grouping, even excluded ones, so a rejected retry
    # connecting two sessions cannot allow its approved siblings to cross splits.
    files = list(sample_rows(dataset).select_for_update().order_by("created_at", "id")[:501])
    if len(files) > 500:
        raise exceptions.ValidationError("v1 exports up to 500 trajectories per collection. Create a focused collection.")
    records = [{"id": str(f.id), **f.metadata_json["training"]} for f in files]
    splits = assign_splits(records, seed, validation_percent, test_percent)
    partitions = {"train": [], "validation": [], "test": []}
    manifest, skipped, seen, total_bytes = [], [], set(), 0
    for file, record in zip(files, records):
        annotation = record["annotation"]
        reason = ""
        if record["warnings"]: reason = "structural_warnings"
        elif annotation["decision"] != "approved": reason = "not_approved"
        elif annotation["outcome"] != "success" and not (include_failures and annotation["outcome"] == "failure"): reason = "outcome_not_selected"
        elif record["content_hash"] in seen: reason = "exact_duplicate"
        if reason:
            skipped.append({"sample_id": str(file.id), "reason": reason})
            continue
        row = training_row(read_sample(file), annotation)
        raw = encoded(row) + b"\n"
        total_bytes += len(raw)
        if total_bytes > 32 * 1024 * 1024:
            raise exceptions.ValidationError("v1 export is limited to 32 MiB. Use a smaller collection.")
        split = splits[str(file.id)]
        partitions[split].append(raw)
        seen.add(record["content_hash"])
        manifest.append({"sample_id": str(file.id), "source_sha256": file.sha256, "revision": record["revision"],
            "split": split, "group_keys": record["group_keys"], "annotation": annotation, "source": record["source"]})
    if not manifest:
        raise exceptions.ValidationError("No eligible samples. Approve complete trajectories and label their outcomes first.")
    if not partitions["train"]:
        raise exceptions.ValidationError("No task group landed in the training split. Adjust the seed or use 0% validation/test for a small dataset.")
    recipe = {"schema": "nexus.sft-export.v1", "trajectory_schema": SCHEMA, "settings": settings,
        "split_algorithm": "task-session-content-components-sha256-v1", "samples": manifest, "skipped": skipped,
        "counts": {k: len(v) for k, v in partitions.items()}, "created_at": timezone.now().isoformat()}
    export_id = uuid.uuid4().hex
    exported = []
    for split, rows in partitions.items():
        if rows:
            exported.append(store_bytes(dataset, request.user, f"training/exports/{export_id}/{split}.jsonl", b"".join(rows),
                {"training": {"kind": "sft", "split": split, "export_id": export_id}}))
    exported.append(store_json(dataset, request.user, f"training/exports/{export_id}/manifest.jsonl", recipe,
        {"training": {"kind": "recipe", "export_id": export_id}}))
    from .manifests import store_entries, release_summary
    version = DatasetVersion.objects.create(tenant=dataset.tenant, project=dataset.project, dataset=dataset,
        version=f"v{dataset.versions.count() + 1}", created_by=request.user,
        file_count=len(exported), size_bytes=sum(f.size_bytes for f in exported),
        snapshot_json={"format": 2, "release_notes": "Agent training export", "training": {"request_key": key,
            "settings": settings, "counts": recipe["counts"], "skipped_count": len(skipped), "export_id": export_id}})
    store_entries(version, ({"file_id": str(f.id), "file_name": f.file_name, "size_bytes": f.size_bytes,
        "content_type": f.content_type, "object_key": f.object_key, "sha256": f.sha256,
        "storage_backend": f.storage_backend, "metadata_json": version_file_metadata(f.metadata_json)} for f in exported))
    version.snapshot_json["summary"] = release_summary(version)
    version.save(update_fields=["snapshot_json", "updated_at"])
    dataset.current_version = version.version
    dataset.save(update_fields=["current_version", "updated_at"])
    log_write(request=request, action="datasets.training.export", dataset=dataset, metadata={"version": version.version, "count": len(manifest)})
    return version
