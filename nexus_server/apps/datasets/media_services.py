from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.http import FileResponse
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from rest_framework import exceptions, status

from apps.audit.services import log_audit
from apps.common.models import SoftDeleteModel
from apps.common.project_scope import scope_queryset_to_current_project
from apps.tenancy.models import Project, Tenant
from .media_policy import invoke_media_policy

from .models import MediaAsset


MEDIA_URI_PREFIX = "nexus-media://"
MEDIA_SIGNER_SALT = "nexus.media.asset"


def get_tenant_from_request(request):
    return invoke_media_policy("media_request_tenant", request=request)


class MediaAssetNotFound(exceptions.APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Media asset not found."
    default_code = "MEDIA_ASSET_NOT_FOUND"


class MediaAssetInvalid(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Invalid media asset."
    default_code = "MEDIA_ASSET_INVALID"


def list_media_assets(*, request):
    tenant = get_tenant_from_request(request)
    return scope_queryset_to_current_project(visible_media_assets(user=request.user, tenant=tenant), request).order_by("-created_at")


def upload_media_asset(*, request, uploaded_file, purpose: str, expires_at=None) -> MediaAsset:
    tenant = get_tenant_from_request(request)
    if uploaded_file is None:
        raise exceptions.ValidationError("file is required.")
    size = int(getattr(uploaded_file, "size", 0) or 0)
    max_size = int(getattr(settings, "NEXUS_MEDIA_MAX_UPLOAD_BYTES", 25_000_000))
    if size <= 0:
        raise exceptions.ValidationError("file must not be empty.")
    if size > max_size:
        raise exceptions.ValidationError(f"file may not exceed {max_size} bytes.")
    content_type = str(getattr(uploaded_file, "content_type", "") or "application/octet-stream")
    allowed = {value.strip() for value in getattr(settings, "NEXUS_MEDIA_ALLOWED_CONTENT_TYPES", []) if value.strip()}
    if allowed and content_type not in allowed:
        raise exceptions.ValidationError("content_type is not allowed.")
    project = resolve_project_from_request(request=request, tenant=tenant)
    creation_fields = invoke_media_policy("media_creation_fields", request=request, purpose=purpose)
    storage_path, sha256 = save_media_file(tenant=tenant, uploaded_file=uploaded_file)
    asset = MediaAsset.objects.create(
        tenant=tenant,
        project=project,
        owner=request_user_or_none(request.user),
        **creation_fields,
        purpose=purpose,
        file_name=safe_filename(getattr(uploaded_file, "name", "asset")),
        storage_path=storage_path,
        content_type=content_type,
        size_bytes=size,
        sha256=sha256,
        expires_at=expires_at,
    )
    log_audit(
        request=request,
        action="media.asset.upload",
        actor=request_user_or_none(request.user),
        resource_type="media_asset",
        resource_id=asset.pk,
        metadata={"purpose": purpose, "content_type": content_type, "size_bytes": size, "sha256": sha256},
    )
    return asset


def get_media_asset(*, request, asset_id: str) -> MediaAsset:
    tenant = get_tenant_from_request(request)
    asset = scope_queryset_to_current_project(visible_media_assets(user=request.user, tenant=tenant), request).filter(id=asset_id).first()
    if asset is None or asset_is_expired(asset):
        raise MediaAssetNotFound()
    return asset


def delete_media_asset(*, request, asset_id: str) -> MediaAsset:
    asset = get_mutable_media_asset(request=request, asset_id=asset_id)
    asset.delete()
    log_audit(
        request=request,
        action="media.asset.delete",
        actor=request_user_or_none(request.user),
        resource_type="media_asset",
        resource_id=asset.pk,
        metadata={"sha256": asset.sha256, "size_bytes": asset.size_bytes},
    )
    return asset


def signed_media_url(*, request, asset_id: str, expires_in: int | None = None) -> str:
    asset = get_media_asset(request=request, asset_id=asset_id)
    ttl = min(int(expires_in or getattr(settings, "NEXUS_MEDIA_SIGNED_URL_TTL_SECONDS", 300)), 3600)
    token = sign_media_asset(asset=asset, expires_in=ttl)
    log_audit(
        request=request,
        action="media.asset.signed_url.create",
        actor=request_user_or_none(request.user),
        resource_type="media_asset",
        resource_id=asset.pk,
        metadata={"expires_in": ttl},
    )
    path = f"/api/v1/media/assets/{asset.id}/content/?token={token}"
    # The TLS front door talks HTTP to an internal backend Host. Deriving this
    # URL from that request can expose https://127.0.0.1:8000 to clients. Use
    # operator-configured public origin, never untrusted forwarded host headers.
    public_base = str(getattr(settings, "NEXUS_PUBLIC_BASE_URL", "") or "").rstrip("/")
    if public_base:
        parsed = urlparse(public_base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise MediaAssetInvalid("Cloud public media address is not configured correctly.")
        return public_base + path
    return request.build_absolute_uri(path)


def open_signed_media_content(*, asset_id: str, token: str) -> FileResponse:
    try:
        signed_value = signing.Signer(salt=MEDIA_SIGNER_SALT).unsign_object(token)
    except signing.BadSignature as exc:
        raise MediaAssetInvalid("Signed media URL is invalid or expired.") from exc
    signed_asset_id = str(signed_value.get("asset_id", "")) if isinstance(signed_value, dict) else ""
    expires_at = parse_datetime(str(signed_value.get("expires_at", ""))) if isinstance(signed_value, dict) else None
    if not hmac.compare_digest(signed_asset_id, str(asset_id)):
        raise MediaAssetInvalid("Signed media URL does not match asset.")
    if expires_at is None or expires_at <= timezone.now():
        raise MediaAssetInvalid("Signed media URL is invalid or expired.")
    asset = MediaAsset.objects.filter(id=asset_id, status=SoftDeleteModel.STATUS_ACTIVE).first()
    if asset is None or asset_is_expired(asset):
        raise MediaAssetNotFound()
    path = media_storage_root() / asset.storage_path
    resolved = path.resolve()
    root = media_storage_root()
    if root not in resolved.parents and resolved != root:
        raise MediaAssetInvalid("Invalid media storage path.")
    if not resolved.exists() or not resolved.is_file():
        raise MediaAssetNotFound("Media asset content not found.")
    return FileResponse(resolved.open("rb"), content_type=asset.content_type, filename=asset.file_name)


def resolve_media_references_for_provider(*, request, payload: dict[str, Any]) -> dict[str, Any]:
    copied = dict(payload)
    copied["messages"] = [resolve_message_media(request=request, message=message) for message in payload.get("messages", [])]
    return copied


def resolve_message_media(*, request, message: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(message, dict):
        return message
    copied = dict(message)
    content = copied.get("content")
    if isinstance(content, list):
        copied["content"] = [resolve_content_part_media(request=request, part=part) for part in content]
    return copied


def resolve_content_part_media(*, request, part: Any) -> Any:
    if not isinstance(part, dict) or part.get("type") != "image_url":
        return part
    image_url = part.get("image_url")
    if not isinstance(image_url, dict):
        return part
    url = image_url.get("url")
    if not isinstance(url, str) or not url.startswith(MEDIA_URI_PREFIX):
        return part
    asset_id = media_asset_id_from_uri(url)
    signed_url = signed_media_url(request=request, asset_id=asset_id)
    copied = dict(part)
    copied["image_url"] = dict(image_url)
    copied["image_url"]["url"] = signed_url
    return copied


def media_asset_id_from_uri(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme != "nexus-media" or not parsed.netloc:
        raise MediaAssetInvalid("Invalid nexus-media URI.")
    return parsed.netloc


def sign_media_asset(*, asset: MediaAsset, expires_in: int) -> str:
    expires_at = timezone.now() + timedelta(seconds=expires_in)
    return signing.Signer(salt=MEDIA_SIGNER_SALT).sign_object(
        {"asset_id": str(asset.id), "expires_at": expires_at.isoformat()}
    )


def visible_media_assets(*, user, tenant: Tenant):
    return invoke_media_policy("visible_media_assets", user=user, tenant=tenant)


def get_mutable_media_asset(*, request, asset_id: str) -> MediaAsset:
    return invoke_media_policy("get_mutable_media_asset", request=request, asset_id=asset_id)


def resolve_project_from_request(*, request, tenant: Tenant) -> Project | None:
    project_id = getattr(request, "project_id", "")
    if not project_id:
        return None
    project = Project.objects.filter(tenant=tenant, id=project_id, status=SoftDeleteModel.STATUS_ACTIVE).first()
    if project is None:
        raise exceptions.NotFound("Project not found.")
    return project


def save_media_file(*, tenant: Tenant, uploaded_file) -> tuple[str, str]:
    root = media_storage_root()
    target_dir = root / str(tenant.id) / uuid.uuid4().hex
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}_{safe_filename(getattr(uploaded_file, 'name', 'asset'))}"
    target = target_dir / filename
    digest = hashlib.sha256()
    with target.open("wb") as handle:
        for chunk in uploaded_file.chunks():
            digest.update(chunk)
            handle.write(chunk)
    return target.relative_to(root).as_posix(), digest.hexdigest()


def safe_filename(name: str) -> str:
    value = Path(str(name)).name.strip().replace("\\", "_").replace("/", "_")
    return value or "asset"


def media_storage_root() -> Path:
    return Path(settings.NEXUS_MEDIA_STORAGE_ROOT).resolve()


def request_user_or_none(principal):
    user_model = get_user_model()
    return principal if isinstance(principal, user_model) else None


def asset_is_expired(asset: MediaAsset) -> bool:
    return bool(asset.expires_at and asset.expires_at <= timezone.now())
