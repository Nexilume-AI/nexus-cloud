"""Computer-bound Tool Setup keys: live authentication plus commit-time cleanup.

Loaded only by the personal host. Offline presence does not revoke API access;
revocation, replacement, deletion and ownership changes do. Raw/bulk SQL changes
cannot bypass the live binding check even if they do not emit Django signals.
"""
from types import SimpleNamespace
from django.db import transaction
from django.db.models import Q
from django.db.models.signals import post_save, pre_delete
from django.utils import timezone
from apps.common.subjects import request_subject
from apps.workspaces.models import WorkspaceConnection, ComputerRuntimeDevice, WorkspaceToolManagedProfile, ComputerRuntimeCommand
from .models import PersonalRouterCredential, PersonalToolConfigOperation


def binding_valid(credential, *, require_applied):
    ids = (credential.tool_connection_id, credential.tool_device_id, credential.tool_profile_id)
    if credential.issued_for == "exported":
        return not any(ids)
    if credential.issued_for != "tool_setup" or not all(ids):
        return False
    request = SimpleNamespace(user=credential.owner, tenant_id=str(credential.tenant_id),
        project_id=str(credential.project_id), META={}, headers={})
    subject = request_subject(request)
    profiles = WorkspaceToolManagedProfile.objects.filter(pk=credential.tool_profile_id, status="active",
        tool="codex", profile="nexus", tenant_id=credential.tenant_id, project_id=credential.project_id,
        created_by_id=credential.owner_id, connection_id=credential.tool_connection_id,
        connection__status="active", connection__connection_type="runtime",
        connection__tenant_id=credential.tenant_id, connection__project_id=credential.project_id,
        connection__created_by_id=credential.owner_id, connection__owner_subject_type="user",
        connection__owner_subject_hash=subject.subject_hash,
        connection__runtime_device__id=credential.tool_device_id,
        connection__runtime_device__revoked_at__isnull=True)
    if require_applied:
        profiles = profiles.filter(router_credential_id=credential.pk, router_id=credential.router_id)
    return profiles.exists()


def _cancel_commands(ids, using):
    from apps.workspaces.computer_runtime import cancel_runtime_command, TERMINAL_COMMAND_STATUSES
    for command in ComputerRuntimeCommand.objects.using(using).filter(pk__in=ids).exclude(status__in=TERMINAL_COMMAND_STATUSES):
        cancel_runtime_command(command=command, code="TOOL_COMPUTER_REVOKED",
            message="Tool Setup's Computer or profile is no longer authorized.")


def _operation_command_ids(operations):
    return list({pk for row in operations.values_list("command_id", "fence_command_id", "restore_command_id")
        for pk in row if pk is not None})


def revoke_invalid_bindings(*, connection_id, using="default", force=None):
    """After device transaction commit, take the normal Tool Setup Computer lock.

    Never lock credentials/profile while holding the revoke API's Device lock:
    prepare/finish take Computer -> Device -> operation -> command -> key.
    Authentication already fails closed while cleanup is waiting to acquire it.
    """
    with transaction.atomic(using=using):
        WorkspaceConnection.objects.using(using).select_for_update().filter(pk=connection_id).first()
        candidates = PersonalRouterCredential.objects.using(using).filter(issued_for="tool_setup",
            tool_connection_id=connection_id, revoked_at__isnull=True).select_related("owner")
        if force is not None:
            ids = list(candidates.filter(force).values_list("pk", flat=True))
        else:
            ids = [row.pk for row in candidates if not binding_valid(row, require_applied=False)]
        if not ids:
            return
        now = timezone.now()
        PersonalRouterCredential.objects.using(using).filter(pk__in=ids, revoked_at__isnull=True).update(revoked_at=now)
        operations = PersonalToolConfigOperation.objects.using(using).filter(active=True, credential_id__in=ids)
        command_ids = _operation_command_ids(operations)
        operations.update(state="revoked", active=False, completed_at=now, error_code="TOOL_COMPUTER_REVOKED")
        from apps.audit.services import write_audit_log
        # Actor/request are not synthesized for device-signed or ORM deletion.
        row = PersonalRouterCredential.objects.using(using).select_related("tenant").filter(pk__in=ids).first()
        if row is not None:
            write_audit_log(request=None, actor=None, tenant=row.tenant,
                action="workspaces.tool_setup.credentials_revoked", resource_type="workspace_connection",
                resource_id=connection_id, after={"credential_count":len(ids), "pending_command_count":len(command_ids)})
        transaction.on_commit(lambda: _cancel_commands(command_ids, using), using=using)


def _schedule(connection_id, using, force=None):
    def cleanup():
        revoke_invalid_bindings(connection_id=connection_id, using=using, force=force)
        from .agent_credentials import revoke_invalid_bindings as revoke_agent_bindings
        revoke_agent_bindings(connection_id=connection_id, using=using, force=force)
    transaction.on_commit(cleanup, using=using)


def _device_saved(sender, instance, using, raw=False, **kwargs):
    if not raw and instance.revoked_at is not None:
        _schedule(instance.connection_id, using, Q(tool_device_id=instance.pk))


def _connection_saved(sender, instance, using, raw=False, created=False, update_fields=None, **kwargs):
    relevant = {"status", "connection_type", "tenant", "tenant_id", "project", "project_id",
        "created_by", "created_by_id", "owner_subject_hash", "owner_subject_type"}
    if not raw and not created and (update_fields is None or relevant.intersection(update_fields)):
        _schedule(instance.pk, using)


def _profile_saved(sender, instance, using, raw=False, created=False, update_fields=None, **kwargs):
    relevant = {"status", "connection", "connection_id", "tenant", "tenant_id", "project", "project_id",
        "created_by", "created_by_id", "tool", "profile"}
    if not raw and not created and (update_fields is None or relevant.intersection(update_fields)):
        connection_ids = PersonalRouterCredential.objects.using(using).filter(issued_for="tool_setup",
            tool_profile_id=instance.pk).values_list("tool_connection_id", flat=True).distinct()
        from .models import PersonalAgentCredential
        agent_connection_ids = PersonalAgentCredential.objects.using(using).filter(
            tool_profile_id=instance.pk).values_list('tool_connection_id',flat=True).distinct()
        for connection_id in set(connection_ids) | set(agent_connection_ids) | {instance.connection_id}:
            _schedule(connection_id, using)


def _deleted(sender, instance, using, **kwargs):
    if sender is WorkspaceConnection:
        _schedule(instance.pk, using, Q(tool_connection_id=instance.pk))
    elif sender is ComputerRuntimeDevice:
        _schedule(instance.connection_id, using, Q(tool_device_id=instance.pk))
    else:
        # The operation rows cascade with a hard-deleted profile. Capture only
        # command IDs beforehand so queued work is still canceled after commit.
        ids = _operation_command_ids(PersonalToolConfigOperation.objects.using(using).filter(profile=instance, active=True))
        transaction.on_commit(lambda: _cancel_commands(ids, using), using=using)
        _schedule(instance.connection_id, using, Q(tool_profile_id=instance.pk))


def register_lifecycle_receivers():
    for sender, handler in ((WorkspaceConnection, _connection_saved), (ComputerRuntimeDevice, _device_saved),
                            (WorkspaceToolManagedProfile, _profile_saved)):
        uid = "personal-tool-" + sender._meta.label_lower
        post_save.connect(handler, sender=sender, dispatch_uid=uid + "-save", weak=False)
        pre_delete.connect(_deleted, sender=sender, dispatch_uid=uid + "-delete", weak=False)
