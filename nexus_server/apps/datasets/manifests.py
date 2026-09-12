"""Immutable normalized manifests, with read compatibility for historical JSON."""
from itertools import islice
from .models import DatasetVersionEntry


def raw_entries(version, file_id=None):
    if version.snapshot_json.get("format") == 2:
        rows = version.entries.all()
        if file_id:
            rows = rows.filter(file_id=file_id)
        yield from rows.order_by("file_id").values_list("payload", flat=True).iterator(chunk_size=100)
    else:
        for row in version.snapshot_json.get("files", []):
            if isinstance(row, dict) and (not file_id or str(row.get("file_id")) == str(file_id)):
                yield row


def store_entries(version, rows):
    """Caller holds the parent collection lock and transaction."""
    iterator = iter(rows)
    while batch := list(islice(iterator, 100)):
        DatasetVersionEntry.objects.bulk_create([DatasetVersionEntry(version=version,
            file_id=row["file_id"], created_at=version.created_at, payload=row) for row in batch])


def release_summary(version):
    cached = getattr(version, "_summary", None)
    if cached is not None:
        return cached
    if version.snapshot_json.get("summary"):
        return version.snapshot_json["summary"]
    if version.snapshot_json.get("format") == 2:
        result = {}
        for name, key in [("formats", "content_type"), ("source_types", "metadata_json__source_type"),
                          ("license_statuses", "metadata_json__license_status"), ("sensitivity_levels", "metadata_json__sensitivity_level")]:
            result[name] = sorted(str(value) for value in version.entries.order_by().values_list(f"payload__{key}", flat=True).distinct() if value)
        result["provenance_agent_count"] = version.entries.exclude(payload__metadata_json__agent_id__isnull=True).order_by().values("payload__metadata_json__agent_id").distinct().count()
        version._summary = result
        return result
    fields = {key: set() for key in ["formats", "source_types", "license_statuses", "sensitivity_levels", "agents"]}
    for row in raw_entries(version):
        metadata = row.get("metadata_json") or {}
        fields["formats"].add(str(row.get("content_type") or "unknown"))
        for name, key in [("source_types", "source_type"), ("license_statuses", "license_status"), ("sensitivity_levels", "sensitivity_level"), ("agents", "agent_id")]:
            if metadata.get(key):
                fields[name].add(str(metadata[key]))
    result = {name: sorted(values) for name, values in fields.items() if name != "agents"}
    result["provenance_agent_count"] = len(fields["agents"])
    version._summary = result
    return result


def public_entry(version, item):
    return {"file_id": item.get("file_id", ""), "file_name": item.get("file_name", ""),
        "size_bytes": item.get("size_bytes", 0), "sha256": item.get("sha256", ""),
        "storage_backend": item.get("storage_backend", ""), "metadata_json": item.get("metadata_json", {}),
        "download_url": f"/api/v1/datasets/{version.dataset_id}/versions/{version.id}/files/{item.get('file_id', '')}/download/"}
