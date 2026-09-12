"""Owner-bound Tool Setup metadata; remote I/O and rotation are separate steps.

These helpers never write a Computer or issue/revoke credentials. A configuration
workflow must verify the remote write before recording a new revision here.
"""
import hashlib
import re
from django.db import transaction
from django.utils.crypto import constant_time_compare
from rest_framework import exceptions
from apps.workspaces.connection_core import WorkspaceConfigConflict, WorkspaceNotFound
from apps.workspaces.context_policy import terminal_context_valid
from apps.workspaces.execution import get_terminal_session
from apps.workspaces.models import WorkspaceConnection, WorkspaceToolManagedProfile
from .router_credentials import _current


@transaction.atomic
def get_profile(*, request, session_id, create=False):
    session = get_terminal_session(request=request, session_id=str(session_id))
    # Lock the Computer, not a missing profile row. This serializes first-create
    # without an uncommitted remote command or silent duplicate profile.
    WorkspaceConnection.objects.select_for_update().get(pk=session.connection_id)
    if not terminal_context_valid(session):
        raise WorkspaceNotFound("Open a valid Computer terminal before configuring tools.")
    profile = WorkspaceToolManagedProfile.objects.filter(connection=session.connection,
        tool="codex", profile="nexus", status="active").select_related("router_credential").first()
    if profile is not None and (profile.tenant_id != session.tenant_id or
            profile.project_id != session.project_id or profile.created_by_id != request.user.pk):
        raise WorkspaceNotFound("Tool profile is unavailable in this context.")
    if profile is None and create:
        profile = WorkspaceToolManagedProfile.objects.create(tenant=session.tenant, project=session.project,
            connection=session.connection, created_by=request.user, tool="codex", profile="nexus")
    return profile


@transaction.atomic
def bind_router_credential(*, request, session_id, credential_id, expected_revision, applied_revision):
    if not isinstance(applied_revision, str) or re.fullmatch(r"[0-9a-f]{64}", applied_revision) is None:
        raise exceptions.ValidationError("A verified configuration revision is required.")
    profile = get_profile(request=request, session_id=session_id, create=True)
    if not isinstance(expected_revision, str) or not constant_time_compare(profile.config_revision, expected_revision):
        raise WorkspaceConfigConflict()
    credential, owner = _current(request, credential_id)
    if owner.pk != request.user.pk or credential.tenant_id != profile.tenant_id or credential.project_id != profile.project_id:
        raise WorkspaceNotFound("Router credential is unavailable in this context.")
    profile.router = credential.router
    profile.provider_runtime = None
    profile.router_credential = credential
    profile.config_revision = applied_revision
    profile.save(update_fields=["router", "provider_runtime", "router_credential", "config_revision", "updated_at"])
    return profile


def router_credential_state(*, request, session_id, remote_token):
    profile = get_profile(request=request, session_id=session_id)
    if profile is None or profile.router_credential_id is None:
        return "not_configured" if not remote_token else "needs_repair"
    try:
        credential, owner = _current(request, profile.router_credential_id)
    except exceptions.APIException:
        return "needs_repair"
    if (not isinstance(remote_token, str) or len(remote_token) > 128 or
            owner.pk != request.user.pk or credential.tenant_id != profile.tenant_id or
            credential.project_id != profile.project_id or credential.router_id != profile.router_id):
        return "needs_repair"
    return "ready" if constant_time_compare(credential.token_hash,
        hashlib.sha256(remote_token.encode("utf-8")).hexdigest()) else "needs_repair"
