"""Shared image validation; execution and settlement use the explicit host."""
import hashlib
import hmac
import uuid

from django.conf import settings
from rest_framework.exceptions import APIException, ValidationError

# Compatibility export used by candidate filtering and existing integrations.
from apps.common.gateway_lifecycle import image_price
from apps.datasets.media_services import get_media_asset, media_storage_root
from .image_media import max_image_bytes, validate_image


class ImageOperationError(APIException):
    status_code = 409
    default_code = "IMAGE_OPERATION_CONFLICT"
    default_detail = "Image request is already in progress or has a different payload."


class ImageInvocationFailed(APIException):
    status_code = 502
    default_code = "IMAGE_OPERATION_FAILED"


def digest(value):
    return hmac.new(settings.SECRET_KEY.encode(), value, hashlib.sha256).hexdigest()


def asset_bytes(request, asset_id):
    try:
        asset_id = uuid.UUID(str(asset_id))
    except (ValueError, TypeError) as exc:
        raise ValidationError("Invalid Nexus image asset reference.") from exc
    asset = get_media_asset(request=request, asset_id=str(asset_id))
    root = media_storage_root()
    path = (root / asset.storage_path).resolve()
    if root not in path.parents or not path.is_file():
        raise ValidationError("Image asset content is unavailable.")
    with path.open("rb") as stream:
        data = stream.read(max_image_bytes() + 1)
    mime, _ = validate_image(data, asset.content_type)
    return data, mime


def input_files(request, operation):
    if operation == "images.generate":
        if request.FILES or "image" in request.data or "mask" in request.data:
            raise ValidationError("Use images/edits for image inputs.")
        return []
    files = []
    for field in request.FILES:
        if field not in {"image", "image[]", "mask"}:
            raise ValidationError("Unsupported image upload field.")
        for uploaded in request.FILES.getlist(field):
            data = uploaded.read(max_image_bytes() + 1)
            mime, _ = validate_image(data, uploaded.content_type)
            files.append((field, data, mime))
    if not files:
        # JSON callers can reuse only their own protected Nexus media assets.
        refs = request.data.get("image", [])
        refs = [refs] if isinstance(refs, str) else refs
        if not isinstance(refs, list) or len(refs) > 4:
            raise ValidationError("image must contain one to four nexus-media references.")
        for ref in refs:
            if not isinstance(ref, str) or not ref.startswith("nexus-media://"):
                raise ValidationError("Image edits require uploads or owned nexus-media references.")
            data, mime = asset_bytes(request, ref.removeprefix("nexus-media://"))
            files.append(("image[]" if len(refs) > 1 else "image", data, mime))
        mask = request.data.get("mask")
        if mask:
            if not isinstance(mask, str) or not mask.startswith("nexus-media://"):
                raise ValidationError("mask requires an owned nexus-media reference.")
            data, mime = asset_bytes(request, mask.removeprefix("nexus-media://"))
            files.append(("mask", data, mime))
    images = [item for item in files if item[0] != "mask"]
    if not 1 <= len(images) <= (1 if operation == "images.variation" else 4):
        raise ValidationError("Incorrect number of image inputs.")
    if sum(item[0] == "mask" for item in files) > 1 or (operation == "images.variation" and len(files) != 1):
        raise ValidationError("Unsupported mask input.")
    if sum(len(item[1]) for item in files) > 25 * 1024 * 1024:
        raise ValidationError("Combined image inputs exceed 25 MiB.")
    return files


def image_response(request, record, response_format):
    from .integration import gateway_integration
    return gateway_integration().image_response(request=request, record=record, response_format=response_format)


def images(*, request, payload, operation):
    from .integration import gateway_integration
    return gateway_integration().images(request=request, payload=payload, operation=operation)
