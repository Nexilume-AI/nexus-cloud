"""Run-owned snapshots for durable input; no expiring media URL in the queue."""
import hashlib
import uuid

from rest_framework import exceptions

from .image_services import prepare_attachments, bind_attachments, attachment_arguments
from .file_transfers import prepare_files, bind_files, file_arguments
from .models import AgentDisplayAsset


def references(data):
    images, files = data.get("attachments", []), data.get("files", [])
    if not isinstance(images, list) or len(images) > 4 or not isinstance(files, list) or len(files) > 8:
        raise exceptions.ValidationError("Attach at most four images and eight files.")
    try:
        if any(not isinstance(item, dict) or set(item) != {"asset_id"} for item in images):
            raise ValueError()
        images = [{"asset_id": str(uuid.UUID(str(item["asset_id"])))} for item in images]
        files = [str(uuid.UUID(item)) for item in files]
        if len({item["asset_id"] for item in images}) != len(images) or len(set(files)) != len(files):
            raise ValueError()
    except (TypeError, ValueError, AttributeError):
        raise exceptions.ValidationError("Use distinct image asset IDs and file IDs, not URLs or file content.") from None
    return images, files


def prepare(*, request, run, task, images, files):
    from .tool_catalog import effective_mcp_tools
    descriptor = next((item for item in effective_mcp_tools(agent=run.agent, runtime=task.runtime)
                       if item["name"] == task.tool_name), {})
    prepared = prepare_attachments(request=request, descriptor=descriptor, attachments=images, run=run)
    rows = prepare_files(request=request, descriptor=descriptor, references=files, agent_id=run.agent_id, run=run)
    return descriptor, prepared, rows


def snapshot(*, run, prepared, rows):
    # The submission transaction owns DB references. Run asset storage has the
    # same retention semantics as initial-turn image binding.
    blocks = bind_attachments(run=run, prepared=prepared)
    blocks += bind_files(run=run, rows=rows, turn_index=None)
    return {"attachments": [{**ref, "name": item.get("name", "Attached image")}
                            for ref, item in zip(attachment_arguments(prepared), prepared)],
            "files": file_arguments(rows), "blocks": blocks}


def restore_images(*, run, manifest):
    prepared = []
    for ref in manifest.get("attachments", []):
        asset = AgentDisplayAsset.objects.filter(run=run, id=ref["asset_id"]).first()
        if not asset:
            raise exceptions.NotFound("Queued image is no longer available.")
        try:
            with asset.file.open("rb") as stream:
                data = stream.read(2 * 1024 * 1024 + 1)
        except OSError:
            raise exceptions.NotFound("Queued image is no longer available.") from None
        if len(data) != asset.size_bytes or len(data) > 2 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != asset.sha256:
            raise exceptions.NotFound("Queued image is no longer available.")
        prepared.append({"id": asset.id, "data": data, "content_type": asset.content_type,
                         "width": asset.width, "height": asset.height, "name": ref.get("name", "Attached image"),
                         "bound_run_id": run.id})
    return prepared
