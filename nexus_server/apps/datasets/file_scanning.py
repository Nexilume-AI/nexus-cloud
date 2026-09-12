"""Bounded, fail-closed file inspection shared by imports and Agent outputs.

Raster validation is not OCR, malware detection or a licence determination.
Unknown binary formats require a dedicated scanner; never bless them as text.
"""
import hashlib
import io
from pathlib import PurePosixPath

from django.conf import settings
from rest_framework.exceptions import ValidationError

TEXT_TYPES = {
    ".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown",
    ".csv": "text/csv", ".json": "application/json", ".jsonl": "application/jsonl",
    ".ndjson": "application/x-ndjson",
}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
ERROR_MESSAGES = {
    "FILE_TYPE_UNSUPPORTED": "This file type has no approved scanner. Use TXT, Markdown, CSV, JSON/JSONL, PNG, JPEG or WebP.",
    "FILE_TYPE_MISMATCH": "The filename, declared type and file contents must agree.",
    "FILE_TOO_LARGE": "The file exceeds the import byte limit.",
    "FILE_EMPTY": "Choose a non-empty file.",
    "IMAGE_INVALID": "The image is invalid, animated, truncated or exceeds the image size/pixel limit.",
    "IMAGE_METADATA_SENSITIVE": "The image contains location or sensitive metadata. Remove it before importing.",
    "FILE_SENSITIVE_CONTENT": "Sensitive content was detected. Remove it before importing.",
    "BINARY_SCANNER_REQUIRED": "This binary file needs a supported binary scanner; it has not been approved.",
    "SCAN_LEXICAL_SPAN_TOO_LONG": "This file contains text spans that cannot be safely scanned.",
}


def import_limit():
    return min(max(1, int(getattr(settings, "NEXUS_DATASET_IMPORT_MAX_BYTES", 10 * 1024 ** 2))), 100 * 1024 ** 2)


def import_capabilities():
    from apps.gateway.image_media import max_image_bytes
    return {"extensions": list(TEXT_TYPES) + list(IMAGE_TYPES), "max_bytes": import_limit(),
            "image_max_bytes": max_image_bytes(),
            "scan_description": "Text secret rules; image format, decode, dimensions and metadata checks. No OCR or antivirus verdict."}


def scan_asset_stream(stream, *, file_name, content_type=""):
    """Return non-sensitive scan facts; stream must be seekable and bounded by caller."""
    from apps.agents.output_scanning import scan_output_stream
    from apps.gateway.image_media import max_image_bytes, validate_image
    suffix = PurePosixPath(str(file_name).replace("\\", "/")).suffix.lower()
    expected = IMAGE_TYPES.get(suffix) or TEXT_TYPES.get(suffix)
    declared = str(content_type or "").split(";", 1)[0].strip().lower()
    stats = {"pipeline": "asset_rules_v1", "scope": "format_integrity", "error_code": "",
             "finding_count": 0, "replacement_count": 0, "sensitive_key_count": 0,
             "content_type": expected or declared, "sha256": "", "size_bytes": 0}
    if not expected:
        stats.update(scan_output_stream(stream))
        stats["error_code"] = "FILE_TYPE_UNSUPPORTED"
        return stats
    # Browsers frequently send text/plain for Markdown/CSV and octet-stream for JSONL.
    compatible = {"", "application/octet-stream", expected}
    if suffix in TEXT_TYPES:
        compatible |= {"text/plain", "application/json"} if suffix in {".jsonl", ".ndjson"} else {"text/plain"}
    if declared not in compatible:
        stats.update(scan_output_stream(stream))
        stats["error_code"] = "FILE_TYPE_MISMATCH"
        return stats
    if suffix in TEXT_TYPES:
        stats.update(scan_output_stream(stream))
        stats["scope"] = "text_secret_rules"
        if not stats["error_code"] and stats["finding_count"]:
            stats["error_code"] = "FILE_SENSITIVE_CONTENT"
        return stats
    content = stream.read(max_image_bytes() + 1)
    stats.update(size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
                 scope="raster_integrity_and_metadata", complete=len(content) <= max_image_bytes())
    try:
        mime, dimensions = validate_image(content, expected)
        from PIL import Image
        with Image.open(io.BytesIO(content)) as picture:
            exif = picture.getexif()
            if 34853 in exif:  # GPSInfo: never publish precise location accidentally.
                stats["error_code"] = "IMAGE_METADATA_SENSITIVE"
            metadata = "\n".join(str(value) for value in [*picture.info.values(), *exif.values()] if isinstance(value, str))
            if metadata:
                meta_scan = scan_output_stream(io.BytesIO(metadata.encode("utf-8")))
                if meta_scan["error_code"] or meta_scan["finding_count"]:
                    stats["error_code"] = "IMAGE_METADATA_SENSITIVE"
        stats.update(content_type=mime, width=dimensions[0], height=dimensions[1])
    except (ValidationError, OSError, ValueError, SyntaxError):
        stats["error_code"] = "IMAGE_INVALID"
    return stats


def require_scan(stats):
    code = stats.get("error_code")
    if code:
        raise ValidationError({"file": f"{code}: {ERROR_MESSAGES.get(code, 'The file could not be safely scanned.') }"})
