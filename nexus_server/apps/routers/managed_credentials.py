"""Stable Router API access, encrypted at rest and never stored in browser state caches."""
import hashlib
import json
from cryptography.fernet import InvalidToken
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.crypto import constant_time_compare
from django.views.decorators.debug import sensitive_variables
from rest_framework import exceptions
from .credential_examples import router_credential_examples, system_one_credential_examples
from .integration import router_integration
from .models import Router, RouterCredentialVault
from .services import get_usable_router, log_write
from apps.common.crypto import encrypt_secret, decrypt_secret


class CredentialConflict(exceptions.APIException):
    status_code = 409
    default_detail = "Router API key changed. Reload before rotating it."
    default_code = "ROUTER_CREDENTIAL_CHANGED"


def _binding(router, owner, credential_id):
    return [str(value) for value in (router.pk, router.tenant_id, router.project_id, owner.pk, credential_id)]


def api_access(*, request, router_id, action="read", expected_id=None, confirmed=False):
    if not isinstance(request.user, get_user_model()):
        raise exceptions.PermissionDenied("Sign in to manage Router API keys.")
    # Permission initialization must finish outside our credential transaction.
    # Otherwise concurrent requests can hold IAM writes while waiting for its process lock.
    get_usable_router(request=request, router_id=router_id)
    return _locked_api_access(request=request, router_id=router_id, action=action,
                              expected_id=expected_id, confirmed=confirmed)


@transaction.atomic
@sensitive_variables("token", "stored", "value", "exported", "encrypted")
def _locked_api_access(*, request, router_id, action, expected_id, confirmed):
    Router.objects.select_for_update().get(pk=router_id)
    # Recheck current ownership/permissions after acquiring the creation/rotation lock.
    router = get_usable_router(request=request, router_id=router_id)
    host = router_integration()
    backend = settings.NEXUS_ROUTER_INTEGRATION
    vault = RouterCredentialVault.objects.filter(router=router, owner=request.user, backend=backend).first()
    state = host.managed_credential(request=request, router=router, credential_id=vault.credential_id) if vault else None
    token = None
    key_status = state["status"] if state else "missing"
    if vault and key_status == "active":
        try:
            stored = json.loads(decrypt_secret(vault.encrypted_key))
            value = stored["key"]
            if not isinstance(value, str) or stored["binding"] != _binding(router, request.user, vault.credential_id) or not constant_time_compare(
                hashlib.sha256(value.encode("ascii")).hexdigest(), state["hash"],
            ):
                raise ValueError("Credential binding mismatch")
            token = value
        except (InvalidToken, ValueError, KeyError, TypeError, UnicodeError):
            key_status = "repair_required"

    if action == "rotate":
        if not confirmed:
            raise exceptions.ValidationError("Confirm rotation before replacing the current API key.")
        if not vault or str(vault.credential_id) != str(expected_id):
            raise CredentialConflict()
    elif action not in {"read", "ensure"}:
        raise exceptions.ValidationError("Unsupported credential action.")
    if action == "rotate" or (action == "ensure" and vault is None):
        if router.status != Router.STATUS_DEPLOYED:
            raise exceptions.ValidationError("Deploy this Router before creating an API key.")
        exported = host.export_router_credentials(request=request, router_id=router.pk)
        token = exported["gateway_api_key"]
        credential_id = exported["gateway_api_key_id"]
        encrypted = encrypt_secret(json.dumps({"key": token, "binding": _binding(router, request.user, credential_id)}))
        if vault:
            host.managed_credential(request=request, router=router, credential_id=vault.credential_id, revoke=True)
        vault, _ = RouterCredentialVault.objects.update_or_create(
            router=router, owner=request.user, backend=backend,
            defaults={"credential_id": credential_id, "encrypted_key": encrypted},
        )
        state = host.managed_credential(request=request, router=router, credential_id=vault.credential_id)
        key_status = state["status"]
        log_write(request=request, action=f"routers.credentials.{action}", router=router,
                  metadata={"credential_id": str(vault.credential_id)})

    result = {"status": key_status, "credential_id": str(vault.credential_id) if vault else None,
              "prefix": state.get("prefix", "") if state else "", "credentials": None}
    if token and key_status == "active":
        models = state["models"]
        from apps.gateway.services import router_model_catalog
        operations = {row["id"]: row.get("model_contract", {}).get("operations", [])
                      for row in router_model_catalog(tenant=router.tenant, router=router) if row["id"] in models}
        chat_models = [name for name in models if "chat.completions" in operations.get(name, [])]
        recommended = (chat_models or models or [""])[0]
        base = request.build_absolute_uri("/api/v1/openai/v1").rstrip("/")
        invoke = request.build_absolute_uri(f"/api/v1/routers/{router.pk}/invoke/")
        result["credentials"] = {
            "router_id": str(router.pk), "router_name": router.name, "router_strategy": router.strategy,
            "gateway_api_base_url": base, "gateway_chat_completions_url": base + "/chat/completions",
            "gateway_models_url": base + "/models", "router_invoke_url": invoke,
            "gateway_api_key": token, "gateway_api_key_id": str(vault.credential_id),
            "gateway_authorization_header": "Bearer " + token, "available_models": models, "model_scope": "router",
            "recommended_model": recommended, "expires_at": state.get("expires_at"),
            "model_operations": operations,
            "systemone": system_one_credential_examples(base_url=base,
                models=[name for name in models if "systemone" in operations.get(name, [])]),
            **router_credential_examples(base_url=base, api_key=token, model=recommended, invoke_url=invoke),
        }
        if action == "read":
            log_write(request=request, action="routers.credentials.read", router=router,
                      metadata={"credential_id": str(vault.credential_id)})
    return result
