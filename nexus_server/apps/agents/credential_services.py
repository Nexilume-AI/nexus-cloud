"""URL elicitation and caller-owned credentials, separate from all Run event stores."""
from datetime import timedelta
import json
from urllib.parse import urlsplit
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from rest_framework import exceptions
from apps.common.crypto import encrypt_secret, decrypt_secret
from apps.common.subjects import request_subject, hash_token
from .models import AgentCredentialAuthorization, AgentDisplayRun

PROVIDERS = {
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1"},
    "anthropic": {"name": "Anthropic", "base_url": "https://api.anthropic.com"},
    "nexus": {"name": "Nexus Cloud", "base_url": "https://cloud.nexilume.com/v1"},
}


def enabled(run):
    allowed = getattr(settings, "NEXUS_AGENT_CREDENTIAL_ENABLED_AGENTS", ())
    if str(run.agent_id) not in allowed:
        raise exceptions.PermissionDenied("Provider authorization is not enabled for this Agent.")


def owner_id(run):
    if run.caller_principal_type == "user":
        return run.caller_principal_id
    # The Personal host authenticates restricted Agent keys as their owner.
    # Other principal types have no verified personal ownership here.
    return None


def scope(run):
    return dict(run__agent_id=run.agent_id, run__consumer_tenant_id=run.consumer_tenant_id,
                run__consumer_project_id=run.consumer_project_id,
                run__caller_subject_hash=run.caller_subject_hash)


def public(row):
    base = str(getattr(settings, "NEXUS_PUBLIC_BASE_URL", "")).rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
        raise exceptions.APIException("Secure authorization URL is not configured.")
    state = row.status if row.expires_at > timezone.now() else "expired"
    return {"authorization_id": str(row.id), "status": state, "provider": row.provider,
            "provider_name": PROVIDERS[row.provider]["name"], "base_url": PROVIDERS[row.provider]["base_url"],
            "agent_name": row.run.agent.name, "agent_id": str(row.run.agent_id),
            "tenant_id": str(row.run.consumer_tenant_id), "project_id": str(row.run.consumer_project_id or ""),
            "caller_type": row.run.caller_principal_type,
            "expires_at": row.expires_at.isoformat(),
            "authorization_url": base + "/api/v1/agent-authorizations/" + str(row.id) + "/page/",
            "credential_supplied": state == "authorized"}


def internal_run(run_id, token):
    from .runtime_services import get_internal_interaction_run
    run = get_internal_interaction_run(run_id=str(run_id), token=token)
    enabled(run)
    if run.run_kind != AgentDisplayRun.KIND_INVOCATION or not run.consumer_tenant_id or not run.caller_subject_hash:
        raise exceptions.PermissionDenied("An authenticated caller Run is required.")
    return run


@transaction.atomic
def request_authorization(run_id, token, provider):
    run = internal_run(run_id, token)
    if run.status != AgentDisplayRun.STATUS_RUNNING:
        raise exceptions.PermissionDenied("Authorization requests require a running invocation.")
    if provider not in PROVIDERS:
        raise exceptions.ValidationError("Unsupported provider.")
    owner = owner_id(run)
    if not owner:
        raise exceptions.PermissionDenied("Use your personal Agent MCP credential or sign in; shared caller keys cannot authorize provider secrets.")
    existing = AgentCredentialAuthorization.objects.filter(
        **scope(run), owner_id=owner, provider=provider, status="authorized", expires_at__gt=timezone.now(),
    ).order_by("-created_at").first()
    if existing:
        return public(existing)
    row, created = AgentCredentialAuthorization.objects.get_or_create(run=run, provider=provider, defaults={
        "owner_id": owner, "expires_at": timezone.now() + timedelta(minutes=10),
    })
    return public(row)


@sensitive_variables()
def internal_status(run_id, token, authorization_id, resolve=False):
    run = internal_run(run_id, token)
    row = AgentCredentialAuthorization.objects.filter(id=authorization_id, **scope(run)).select_related("run__agent").first()
    if not row or str(row.owner_id) != str(owner_id(run)):
        raise exceptions.NotFound()
    result = public(row)
    if resolve:
        if result["status"] != "authorized" or run.status != AgentDisplayRun.STATUS_RUNNING:
            raise exceptions.PermissionDenied("Authorization is not active for this running invocation.")
        # This response goes only to the authenticated runtime callback, never MCP.
        result = {"secret": decrypt_secret(row.encrypted_secret), "provider": row.provider,
                  "base_url": PROVIDERS[row.provider]["base_url"], "expires_at": row.expires_at.isoformat()}
    return result


def browser_row(request, authorization_id, lock=False):
    if getattr(request, "api_key", None) is not None or not request.user.is_authenticated:
        raise exceptions.PermissionDenied("Sign in with the credential owner's personal account.")
    query = AgentCredentialAuthorization.objects.select_related("run__agent")
    if lock: query = query.select_for_update()
    row = query.filter(id=authorization_id, owner_id=request.user.pk).first()
    if not row or str(owner_id(row.run)) != str(request.user.pk):
        raise exceptions.NotFound()
    enabled(row.run)
    return row


@transaction.atomic
@sensitive_variables()
def decide(request, authorization_id):
    row = browser_row(request, authorization_id, lock=True)
    action = request.data.get("action")
    if action in ("decline", "revoke"):
        row.status = "revoked" if row.status == "authorized" else "cancelled"
        row.encrypted_secret = ""
    elif action == "authorize":
        if row.status != "pending" or row.expires_at <= timezone.now():
            raise exceptions.ValidationError("Authorization is no longer pending. Start a new request.")
        secret = request.data.get("secret")
        if not isinstance(secret, str) or not 8 <= len(secret) <= 8192 or any(c.isspace() for c in secret):
            raise exceptions.ValidationError("Enter a valid provider API key or access token.")
        if request.data.get("consent") is not True:
            raise exceptions.ValidationError("Explicit consent is required.")
        ttl = request.data.get("ttl_seconds", 3600)
        if type(ttl) is not int or not 300 <= ttl <= 86400:
            raise exceptions.ValidationError("Authorization lifetime must be 5 minutes to 24 hours.")
        row.encrypted_secret = encrypt_secret(secret)
        row.status = "authorized"
        row.expires_at = timezone.now() + timedelta(seconds=ttl)
    else:
        raise exceptions.ValidationError("Unknown authorization action.")
    row.save(update_fields=["status", "encrypted_secret", "expires_at", "updated_at"])
    return public(row)


def chunks(request, run, session, seen):
    if run is None or session is None: return
    caps = session.client_capabilities_json or {}
    if caps.get("elicitation_url") is not True: return
    if (session.caller_subject_hash != request_subject(request).subject_hash or
            session.caller_subject_hash != run.caller_subject_hash): return
    for row in run.credential_authorizations.select_related("run__agent").all():
        key = "credential:" + str(row.id)
        status = public(row)["status"]
        if status == "pending" and key not in seen:
            from django.core.cache import cache
            request_id = "nexus-credential-" + str(row.id)
            cache.set(request_id, {"session_hash": session.external_session_hash,
                      "subject_hash": session.caller_subject_hash, "agent_id": str(run.agent_id)}, timeout=600)
            seen.add(key)
            rpc = {"jsonrpc":"2.0", "id":request_id, "method":"elicitation/create", "params":{
                "mode":"url", "elicitationId":str(row.id), "url":public(row)["authorization_url"],
                "message":"Authorize " + PROVIDERS[row.provider]["name"] + " access for " + run.agent.name + ". Enter secrets only on the authenticated Cloud page."}}
            yield ("event: message\ndata: " + json.dumps(rpc) + "\n\n").encode()
        elif status == "authorized" and key in seen and key + ":done" not in seen:
            seen.add(key + ":done")
            yield ("event: message\ndata: " + json.dumps({"jsonrpc":"2.0", "method":"notifications/elicitation/complete",
                "params":{"elicitationId":str(row.id)}}) + "\n\n").encode()


@transaction.atomic
def handle_response(request, agent_id, payload, headers):
    from django.core.cache import cache
    from .runtime_services import RuntimeMCPResult, _header_value
    rid = str(payload.get("id") or "")
    if not rid.startswith("nexus-credential-") or "method" in payload: return None
    binding = cache.get(rid)
    response = RuntimeMCPResult(status_code=202, headers={}, body=b"")
    if not isinstance(binding, dict): return response
    if (binding.get("agent_id") != str(agent_id) or
        binding.get("subject_hash") != request_subject(request).subject_hash or
        binding.get("session_hash") != hash_token(_header_value(headers,"Mcp-Session-Id") or "")): return response
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if result.get("action") in ("decline", "cancel"):
        row = AgentCredentialAuthorization.objects.select_for_update().filter(id=rid.removeprefix("nexus-credential-")).first()
        if row:
            row.status="cancelled"; row.encrypted_secret=""
            row.save(update_fields=["status","encrypted_secret","updated_at"])
    # accept only acknowledges opening the URL; it never grants access.
    cache.delete(rid)
    return response
