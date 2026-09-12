"""Private Display image inputs are snapshots, owned by one caller and Run."""
import hashlib
import uuid
from django.core.files.base import ContentFile
from rest_framework import exceptions
from apps.datasets.media_services import get_media_asset, media_storage_root
from apps.gateway.image_media import validate_image
from .models import AgentDisplayAsset


def accepts_image_attachments(schema):
    if not isinstance(schema, dict) or not isinstance(schema.get("properties", {}), dict):
        return False
    field = (schema or {}).get("properties", {}).get("attachments", {})
    return isinstance(field, dict) and field.get("type") == "array"


def prepare_attachments(*, request, descriptor, attachments, run=None):
    if attachments is None or attachments == []:
        return []
    if not isinstance(attachments, list) or len(attachments) > 4:
        raise exceptions.ValidationError({"attachments": "Attach at most four images."})
    if not accepts_image_attachments(descriptor.get("input_schema")):
        raise exceptions.ValidationError({"attachments": "AGENT_IMAGE_INPUT_UNSUPPORTED: This Agent does not accept images."})
    prepared = []
    for reference in attachments:
        try:
            asset_id = uuid.UUID(str(reference["asset_id"]))
        except (TypeError, ValueError, KeyError) as exc:
            raise exceptions.ValidationError({"attachments": "Invalid image asset reference."}) from exc
        if run is not None and run.display_assets.filter(pk=asset_id).exists():
            from .follow_up_attachments import restore_images
            prepared.extend(restore_images(run=run, manifest={"attachments": [{"asset_id": str(asset_id)}]}))
            continue
        asset = get_media_asset(request=request, asset_id=str(asset_id))
        key = getattr(request, "api_key", None)
        if (key is not None and asset.api_key_id != key.id) or (key is None and asset.owner_id != request.user.id):
            raise exceptions.NotFound("Image asset not found.")
        root = media_storage_root()
        path = (root / asset.storage_path).resolve()
        if root not in path.parents or not path.is_file():
            raise exceptions.NotFound("Image asset not found.")
        with path.open("rb") as stream:
            data = stream.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise exceptions.ValidationError({"attachments": "Each Run image must be at most 2 MiB."})
        mime, dimensions = validate_image(data, asset.content_type)
        name = str(asset.file_name or "Attached image").replace("\\", "/").rsplit("/", 1)[-1]
        prepared.append({"id": uuid.uuid4(), "data": data, "name": name, "content_type": mime, "width": dimensions[0], "height": dimensions[1]})
    return prepared


def attachment_arguments(prepared):
    return [{"asset_id": str(item["id"]), "content_type": item["content_type"]} for item in prepared]


def bind_attachments(*, run, prepared):
    blocks = []
    for item in prepared:
        if item.get("bound_run_id") == run.id:
            blocks.append({"type": "image", "url": f"/api/v1/agent-runs/{run.id}/display-assets/{item['id']}/", "alt": item.get("name", "Attached image"), "title": item.get("name", "Attached image")})
            continue
        asset = AgentDisplayAsset(id=item["id"], run=run, content_type=item["content_type"], size_bytes=len(item["data"]),
            sha256=hashlib.sha256(item["data"]).hexdigest(), width=item["width"], height=item["height"])
        asset.file.save("input." + item["content_type"].split("/")[1], ContentFile(item["data"]), save=True)
        blocks.append({"type": "image", "url": f"/api/v1/agent-runs/{run.id}/display-assets/{asset.id}/", "alt": item.get("name", "Attached image"), "title": item.get("name", "Attached image")})
    return blocks
