"""Authenticated raster previews: no public URLs, source metadata or active content."""
import hashlib
import io
import uuid

from django.http import HttpResponse
from rest_framework.exceptions import NotFound, ValidationError

from .storage_backends import get_dataset_storage_backend


def image_preview(*, request, dataset, object_key, storage_backend, size, sha256, content_type, file_id):
    from PIL import Image, ImageOps
    from apps.gateway.image_media import max_image_bytes, validate_image
    from apps.common.subjects import request_subject
    from .transfers import reserve_export, complete_export
    from .services import log_write
    if content_type not in {"image/png", "image/jpeg", "image/webp"} or not 0 < size <= max_image_bytes():
        raise NotFound("Image preview is unavailable for this file.")
    stream = get_dataset_storage_backend(storage_backend).open(object_key=object_key)
    try:
        content = stream.read(max_image_bytes() + 1)
    finally:
        stream.close()
    if len(content) != size or not sha256 or hashlib.sha256(content).hexdigest() != sha256:
        raise ValidationError("IMAGE_INTEGRITY_MISMATCH: Preview bytes no longer match this release.")
    validate_image(content, content_type)
    with Image.open(io.BytesIO(content)) as source:
        oriented = ImageOps.exif_transpose(source)
        oriented.thumbnail((1280, 1280))
        # Copy pixels only: strips EXIF, GPS, comments and appended/polyglot bytes.
        safe = Image.new("RGBA", oriented.size)
        safe.paste(oriented.convert("RGBA"))
        buffer = io.BytesIO()
        safe.save(buffer, format="PNG")
    result = buffer.getvalue()
    identity = hashlib.sha256(f"preview|{request_subject(request).subject_hash}|{uuid.uuid4()}".encode()).hexdigest()
    transfer = reserve_export(dataset, len(result), identity)
    complete_export(transfer)
    log_write(request=request, action="datasets.file.preview", dataset=dataset, metadata={"file_id": str(file_id)})
    return HttpResponse(result, content_type="image/png", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox", "Content-Disposition": "inline",
        "Content-Length": str(len(result)),
    })
