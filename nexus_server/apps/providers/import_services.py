"""Private, encrypted import previews; small transactional chunks, no network I/O."""
from __future__ import annotations

import json
import re
from datetime import timedelta
from urllib.parse import urlsplit

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import exceptions

from apps.audit.services import log_audit
from apps.common.crypto import decrypt_secret, encrypt_secret
from apps.common.subjects import request_subject, subject_digest
from apps.common.resource_catalog import resolve_ownership_project
from apps.common.authorization import has_nexus_permission
from apps.tenancy.models import Tenant
from apps.common.request_context import get_tenant_from_request
from .connection_services import connection_runtime, create_provider_connection
from .models import ProviderAccount, ProviderImportBatch
from .connection_inputs import ProviderConnectionCreateSerializer
from .runtime_integration import runtime_integration
from .services import require_provider_create, update_provider_account


def import_context(request):
    tenant = get_tenant_from_request(request)
    project, _ = resolve_ownership_project(request=request, tenant=tenant, ownership=None)
    require_provider_create(request=request, tenant=tenant, project=project)
    return tenant, project, request_subject(request).subject_hash


def _existing(tenant, api_ref):
    rows = list(ProviderAccount.objects.filter(tenant=tenant, account_id=api_ref).select_related("provider")[:2])
    if len(rows) > 1:
        raise exceptions.ValidationError("Ambiguous API identity; manage it manually or choose another api_ref.")
    return rows[0] if rows else None


def _allowed_existing(account, project, *, updating):
    runtime = connection_runtime(account)
    runtime_integration().validate_import_existing(account=account, runtime=runtime)
    if (account.status == "deleted" or account.auth_mode != "api_key" or account.provider.name != "openai-compatible"
            or not runtime or runtime.runtime_type != "direct_api" or runtime.project_id != (project.pk if project else None)):
        raise exceptions.ValidationError("Identity unavailable in this scope; choose another api_ref.")
    if updating and (runtime.status not in {"created", "stopped", "failed"}
                     or runtime_integration().import_has_publication_dependencies(runtime=runtime)
                     or runtime.sources.exclude(status="deleted").exists()):
        raise exceptions.ValidationError("Stop this API and remove publication/Source dependencies before bulk updates.")
    return runtime


def _validate(tenant, project, apis, duplicate_mode, *, can_update):
    counts = {}
    for row in apis:
        ref = row["api_ref"].strip()
        counts[ref] = counts.get(ref, 0) + 1
    results, payload = [], []
    for raw in apis:
        ref = raw["api_ref"].strip()
        result = {"sheet": "APIs", "line": raw["line"], "api_ref": ref[:128],
                  "name": raw["name"].strip()[:255], "status": "pending", "message": ""}
        data = None
        try:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", ref) or ref.startswith("runtime_") or counts[ref] != 1:
                raise exceptions.ValidationError("api_ref must be unique ASCII letters/numbers/._- and not start with runtime_.")
            url = raw["base_url"].strip()
            parsed = urlsplit(url)
            if parsed.scheme not in {"https", "http"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise exceptions.ValidationError("base_url must be HTTP(S) without credentials, query or fragment.")
            account = _existing(tenant, ref)
            if account and duplicate_mode == "update" and not can_update:
                raise exceptions.ValidationError("Updating existing APIs requires the existing Provider management permission.")
            runtime = _allowed_existing(account, project, updating=duplicate_mode == "update") if account else None
            values = {"account_id": ref, "name": raw["name"].strip(), "engine": "direct_api",
                      "upstream_provider": "openai-compatible", "url": url, "key": raw["api_key"]}
            validator = ProviderConnectionCreateSerializer(data={**values, "key": values["key"] or ("retained" if account else "")})
            if not validator.is_valid():
                raise exceptions.ValidationError("Invalid API fields: " + ", ".join(validator.errors.keys()) + ".")
            if account and duplicate_mode == "skip":
                result.update(status="skipped", message="Existing API; no changes.")
            else:
                result.update(action="update" if account else "create", message="Ready to update." if account else "Ready to create.")
                data = {"connection": values, "existing_id": str(account.pk) if account else "",
                        "revision": f"{account.updated_at.isoformat()}|{runtime.updated_at.isoformat()}" if account else ""}
        except (exceptions.ValidationError, ValueError) as error:
            message = str(error.detail[0]) if isinstance(error, exceptions.ValidationError) else "Invalid API URL."
            result.update(status="invalid", message=message)
        results.append(result)
        payload.append(data)
    return results, payload


def preview(request, apis, *, request_key, duplicate_mode="skip"):
    if not apis:
        raise exceptions.ValidationError({"APIs": "Add at least one API row."})
    if duplicate_mode not in {"skip", "update"}:
        raise exceptions.ValidationError({"duplicate_mode": "Choose skip or update."})
    tenant, project, owner = import_context(request)
    project_id = str(project.pk) if project else ""
    content_hash = subject_digest(json.dumps([apis, project_id, duplicate_mode], sort_keys=True))
    # Same key replays the same preview, not a second batch; serialize within the tenant.
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=tenant.pk)
        prior = ProviderImportBatch.objects.filter(tenant=tenant, owner_subject_hash=owner, request_key=request_key).first()
        if prior:
            if prior.content_hash != content_hash:
                raise exceptions.ValidationError("This request key was used for different import content.")
            return prior
        if ProviderImportBatch.objects.filter(tenant=tenant, owner_subject_hash=owner, expires_at__gt=timezone.now()).exclude(encrypted_payload="").count() >= 5:
            raise exceptions.ValidationError("Finish or discard an existing import first (at most five active previews).")
        results, payload = _validate(tenant, project, apis, duplicate_mode,
                                    can_update=has_nexus_permission(request.user, tenant, "admin"))
        ready = any(row["status"] == "pending" for row in results)
        batch = ProviderImportBatch.objects.create(tenant=tenant, owner_subject_hash=owner, context_project_id=project_id,
            request_key=request_key, content_hash=content_hash, duplicate_mode=duplicate_mode, results=results,
            encrypted_payload=encrypt_secret(json.dumps(payload)) if ready else "", status="preview" if ready else "complete",
            expires_at=timezone.now() + timedelta(minutes=30))
        log_audit(request=request, actor=request.user, action="providers.import.preview", resource_type="provider_import",
                  resource_id=batch.pk, metadata={"api_count": len(apis), "mode": duplicate_mode})
        return batch


def get_batch(request, batch_id):
    tenant, project, owner = import_context(request)
    batch = ProviderImportBatch.objects.filter(pk=batch_id, tenant=tenant, owner_subject_hash=owner,
                                             context_project_id=str(project.pk) if project else "").first()
    if not batch:
        raise exceptions.NotFound()
    if batch.expires_at <= timezone.now() and batch.encrypted_payload:
        ProviderImportBatch.objects.filter(pk=batch.pk).update(encrypted_payload="", status="expired")
        batch.encrypted_payload, batch.status = "", "expired"
    return batch


def public_batch(batch):
    return {"id": str(batch.pk), "status": batch.status, "duplicate_mode": batch.duplicate_mode,
            "expires_at": batch.expires_at.isoformat(), "results": batch.results,
            "remaining": sum(row["status"] == "pending" for row in batch.results),
            "failed": sum(row["status"] == "failed" for row in batch.results),
            "invalid": sum(row["status"] == "invalid" for row in batch.results)}


def _apply(request, tenant, project, data, mode):
    values = dict(data["connection"])
    account = _existing(tenant, values["account_id"])
    if account:
        runtime = _allowed_existing(account, project, updating=mode == "update")
        if mode == "skip":
            return "skipped", account
        revision = f"{account.updated_at.isoformat()}|{runtime.updated_at.isoformat()}"
        if str(account.pk) != data["existing_id"] or revision != data["revision"]:
            raise exceptions.ValidationError("API changed since preview; import a fresh preview before overwriting.")
        # Empty credentials mean preserve, never erase. Ownership cannot be changed by a file.
        update = {"name": values["name"], "url": values["url"]}
        if values["key"]:
            update["key"] = values["key"]
        account = update_provider_account(request=request, account_identifier=str(account.pk), data=update)
        outcome = "updated"
    else:
        if data["existing_id"]:
            raise exceptions.ValidationError("API changed since preview; import a fresh preview.")
        values["ownership"] = {"scope": "project", "project_id": str(project.pk)} if project else {"scope": "organization"}
        account = create_provider_connection(request=request, data=values, create_only=True, refresh_result=False)
        outcome = "created"
    return outcome, account


def commit(request, batch_id, *, confirm_updates=False, allow_partial=False, retry_failed=False):
    batch = get_batch(request, batch_id)
    tenant, project, _ = import_context(request)
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=tenant.pk)
        batch = ProviderImportBatch.objects.select_for_update().get(pk=batch.pk)
        if batch.status == "complete":
            return batch
        if batch.expires_at <= timezone.now() or not batch.encrypted_payload:
            raise exceptions.ValidationError("Import expired. Upload a fresh preview; credentials are no longer available.")
        if batch.duplicate_mode == "update" and not confirm_updates:
            raise exceptions.ValidationError("Explicitly confirm updates to existing API configurations.")
        if any(row["status"] == "invalid" for row in batch.results) and not allow_partial:
            raise exceptions.ValidationError("Fix invalid rows, or explicitly confirm importing valid APIs only.")
        payload = json.loads(decrypt_secret(batch.encrypted_payload))
        if retry_failed:
            for row in batch.results:
                if row["status"] == "failed":
                    row["status"] = "pending"
        processed = 0
        for index, row in enumerate(batch.results):
            if row["status"] != "pending" and not (retry_failed and row["status"] == "failed"):
                continue
            if processed >= 5:
                break
            processed += 1
            try:
                # Each API succeeds atomically; a failed API doesn't roll back other APIs.
                with transaction.atomic():
                    outcome, account = _apply(request, tenant, project, payload[index], batch.duplicate_mode)
                row.update(status=outcome, connection_id=str(account.pk), message="Imported; start the Runtime for automatic Model Offer discovery." if outcome != "skipped" else "Existing API; no changes.")
                payload[index] = None  # Erase credentials as soon as this API is completed.
            except exceptions.APIException as error:
                # Safe, actionable categories only. Upstream exception bodies are never returned.
                code = getattr(error, "default_code", "validation_error")
                message = runtime_integration().import_failure_message(error=error)
                row.update(status="failed", message=message, error_code=str(code))
            except IntegrityError:
                row.update(status="failed", message="Identity conflict; review this API and retry with a fresh preview.", error_code="identity_conflict")
        remaining = any(row["status"] in {"pending", "failed"} for row in batch.results)
        batch.status = "processing" if any(row["status"] == "pending" for row in batch.results) else "partial" if remaining else "complete"
        batch.encrypted_payload = encrypt_secret(json.dumps(payload)) if remaining else ""
        batch.save(update_fields=["status", "results", "encrypted_payload", "updated_at"])
        log_audit(request=request, actor=request.user, action="providers.import.commit", resource_type="provider_import",
                  resource_id=batch.pk, metadata={"processed": processed, "status": batch.status})
        return batch


def clear_expired_payloads():
    return ProviderImportBatch.objects.filter(expires_at__lte=timezone.now()).exclude(encrypted_payload="").update(
        encrypted_payload="", status="expired")
