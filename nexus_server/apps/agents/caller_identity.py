"""Opaque runtime identity derived exclusively from authenticated Run records.

A Key is one caller, including across MCP sessions. End-user headers are tracing
labels, never an independent security principal or a way around caller quotas.
"""
import json

from rest_framework.exceptions import PermissionDenied
from apps.common.subjects import subject_digest


def principal_filter(kind, principal_id, prefix="run__"):
    kinds = ("api_key", "api_key_external") if kind in ("api_key", "api_key_external") else (kind,)
    if kind not in ("api_key", "api_key_external", "user", "service_account") or not principal_id:
        raise PermissionDenied("An authenticated Agent caller is required.")
    return {prefix + "caller_principal_type__in": kinds,
            prefix + "caller_principal_id": str(principal_id)}


def runtime_identity(run):
    principal_filter(run.caller_principal_type, run.caller_principal_id)
    if not run.consumer_tenant_id:
        raise PermissionDenied("An authenticated consumer tenant is required.")
    kind = "api_key" if run.caller_principal_type == "api_key_external" else run.caller_principal_type
    # JSON avoids ambiguities in compound identities. Neither raw Key nor account
    # identifiers leave Cloud. Project and Agent scopes prevent cross-resource reuse.
    parts = ["agent-caller-v1", str(run.consumer_tenant_id), kind, str(run.caller_principal_id)]
    quota = subject_digest(json.dumps(parts, separators=(",", ":")))
    owner = subject_digest(json.dumps(parts + [str(run.consumer_project_id or ""), str(run.agent_id)], separators=(",", ":")))
    return {"version": 1, "principal_type": kind, "owner_scope": owner, "quota_scope": quota}
