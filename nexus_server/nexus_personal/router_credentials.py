"""Personal Router capability issuance and live enforcement; secrets never persist."""
from datetime import timedelta
import hashlib
import json
import secrets
import shlex
import uuid
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from rest_framework import exceptions
from apps.audit.services import write_audit_log
from apps.routers.models import Router
from .models import PersonalRouterCredential
from .resource_catalog import PersonalResourceCatalog

PREFIX = "np-router-"
CATALOG_ROUTES = {"openai-compatible-models", "openai-compatible-models-slash",
                  "claude-compatible-models", "claude-compatible-models-slash"}
CALL_ROUTES = {"gateway-chat-completions", "openai-compatible-chat-completions",
               "openai-compatible-chat-completions-slash", "openai-compatible-responses",
               "openai-compatible-responses-slash", "claude-compatible-messages",
               "claude-compatible-messages-slash", "router-invoke"}


def _denied():
    raise exceptions.AuthenticationFailed("Router credential is invalid, expired or revoked.")


def _current(request, credential_id):
    from .authentication import validate_owner
    row = PersonalRouterCredential.objects.select_related("owner", "router").filter(
        pk=credential_id, revoked_at__isnull=True, expires_at__gt=timezone.now()).first()
    if row is None:
        _denied()
    user, tenant_id, project_id = validate_owner(request, row.owner)
    if str(row.tenant_id) != tenant_id or str(row.project_id) != project_id:
        _denied()
    from .tool_credentials import binding_valid
    if not binding_valid(row, require_applied=True):
        _denied()
    # Authentication calls this before DRF assigns request.user. Use an explicit
    # owner context; never reuse an unvalidated caller-supplied resource object.
    from types import SimpleNamespace
    scope_request = SimpleNamespace(user=user, META=request.META, headers=request.headers, query_params={})
    router, _ = PersonalResourceCatalog()._resource(request=scope_request, resource_type="router", obj=row.router)
    if router.status != Router.STATUS_DEPLOYED:
        raise exceptions.NotFound("Router is unavailable.")
    return row, user


def current_credential(request):
    value = getattr(request, "_nexus_personal_router_credential_id", None)
    return _current(request, value)[0] if value is not None else None


def authenticate(request, token):
    try:
        selector, secret = token[len(PREFIX):].split(".")
        if len(selector) != 32 or len(secret) != 43:
            _denied()
        credential_id = uuid.UUID(hex=selector)
    except (ValueError, TypeError):
        _denied()
    stored = PersonalRouterCredential.objects.filter(pk=credential_id).values_list("token_hash", flat=True).first()
    if stored is None or not constant_time_compare(stored, hashlib.sha256(token.encode("ascii")).hexdigest()):
        _denied()
    row, user = _current(request, credential_id)
    match = getattr(request, "resolver_match", None)
    name = getattr(match, "url_name", "")
    if not ((request.method in {"GET", "HEAD"} and name in CATALOG_ROUTES)
            or (request.method == "POST" and name in CALL_ROUTES)):
        raise exceptions.PermissionDenied("This credential only permits its Router model API.")
    path_router = getattr(match, "kwargs", {}).get("router_id")
    if path_router is not None and str(path_router) != str(row.router_id):
        raise exceptions.PermissionDenied("This credential cannot call another Router.")
    request._nexus_personal_router_credential_id = row.pk
    return user, row


def prepare_request(*, request, tenant, payload):
    row = current_credential(request)
    if row is None:
        return
    if str(getattr(tenant, "pk", tenant)) != str(row.tenant_id):
        _denied()
    if payload.get("router_id") and str(payload["router_id"]) != str(row.router_id):
        raise exceptions.PermissionDenied("This credential cannot call another Router.")
    if payload.get("model") not in row.model_names:
        raise exceptions.PermissionDenied("This model is not included in the Router credential.")
    if payload.get("_nexus_operation", "chat.completions") not in {"chat.completions", "responses"}:
        raise exceptions.PermissionDenied("This operation is not included in the Router credential.")
    payload["router_id"] = str(row.router_id)


def _audit(request, router, action, credential):
    write_audit_log(request=request, actor=request.user, tenant=router.tenant, action=action,
        resource_type="router", resource_id=router.pk, after={"credential_id": str(credential.pk)})


@transaction.atomic
def export_router_credentials(*, request, router_id):
    from apps.routers.services import get_mutable_router
    from apps.gateway.services import router_model_catalog
    from .gateway_integration import authorize_router
    router = get_mutable_router(request=request, router_id=router_id)
    authorize_router(request=request, tenant=router.tenant, router=router)
    models = [row["id"] for row in router_model_catalog(tenant=router.tenant, router=router) if row["available"]]
    if not models:
        raise exceptions.ValidationError("Connect an available model before exporting credentials.")
    _, tenant_id, project_id = PersonalResourceCatalog()._context(request, router.tenant)
    credential_id = uuid.uuid4()
    token = PREFIX + credential_id.hex + "." + secrets.token_urlsafe(32)
    row = PersonalRouterCredential.objects.create(pk=credential_id, owner=request.user,
        tenant_id=tenant_id, project_id=project_id, router=router, model_names=models,
        token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(), expires_at=timezone.now() + timedelta(days=90))
    base = request.build_absolute_uri("/api/v1/openai/v1").rstrip("/")
    invoke_url = request.build_absolute_uri(f"/api/v1/routers/{router.pk}/invoke/")
    payload = json.dumps({"model": models[0], "messages": [{"role": "user", "content": "hello"}]})
    def curl(url):
        return "curl " + shlex.quote(url) + " -H " + shlex.quote("Authorization: Bearer " + token) + \
            " -H 'Content-Type: application/json' -d " + shlex.quote(payload)
    _audit(request, router, "routers.credentials.export", row)
    return {"router_id": str(router.pk), "router_name": router.name, "router_strategy": router.strategy,
        "gateway_api_base_url": base, "gateway_chat_completions_url": base + "/chat/completions",
        "gateway_models_url": base + "/models", "router_invoke_url": invoke_url,
        "gateway_api_key": token, "gateway_api_key_id": str(row.pk),
        "gateway_authorization_header": "Bearer " + token, "recommended_model": models[0],
        "available_models": models, "expires_at": row.expires_at, "credential_type": "personal_router",
        "env": "OPENAI_BASE_URL=" + base + "\nOPENAI_API_KEY=" + token + "\n",
        "curl": curl(base + "/chat/completions"), "invoke_curl": curl(invoke_url)}


def list_credentials(*, request, router_id):
    from apps.routers.services import get_mutable_router
    from django.db.models import Q
    router = get_mutable_router(request=request, router_id=router_id)
    now = timezone.now()
    query = router.personal_credentials.filter(owner=request.user)
    cursor = request.query_params.get("cursor")
    if cursor:
        try:
            anchor = query.filter(pk=uuid.UUID(cursor)).first()
        except (TypeError, ValueError):
            anchor = None
        if anchor is None:
            raise exceptions.ValidationError("Credential cursor is unavailable; reload the list.")
        query = query.filter(Q(created_at__lt=anchor.created_at) | Q(created_at=anchor.created_at, pk__lt=anchor.pk))
    rows = list(query.order_by("-created_at", "-pk")[:101])
    items = [{"id": str(row.pk), "prefix": PREFIX + row.pk.hex[:8], "models": row.model_names,
             "created_at": row.created_at, "expires_at": row.expires_at, "last_used_at": row.last_used_at,
             "status": "revoked" if row.revoked_at else "expired" if row.expires_at <= now else "active"}
            for row in rows[:100]]
    return {"items": items, "next_cursor": str(rows[99].pk) if len(rows) > 100 else None}


@transaction.atomic
def revoke_credential(*, request, router_id, credential_id):
    from apps.routers.services import get_mutable_router
    router = get_mutable_router(request=request, router_id=router_id)
    row = PersonalRouterCredential.objects.select_for_update().filter(pk=credential_id, router=router, owner=request.user).first()
    if row is None:
        raise exceptions.NotFound("Router credential not found.")
    if row.revoked_at is None:
        row.revoked_at = timezone.now()
        row.save(update_fields=["revoked_at"])
        _audit(request, router, "routers.credentials.revoke", row)
