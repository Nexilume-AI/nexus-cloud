"""Explicit caller UI selection of a directory on its current Computer."""
from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions


def _run(request, run_id):
    from .services import get_private_display_run, AgentNotFound
    run = get_private_display_run(request=request, run_id=run_id)
    if not run.computer_binding_id:
        raise AgentNotFound()
    connection = run.computer_binding.connection
    if connection.owner_subject_hash != run.caller_subject_hash or connection.tenant_id != run.consumer_tenant_id:
        raise AgentNotFound()
    return run


def browse(*, request, run_id, path=''):
    from apps.workspaces.computer_runtime import execute_runtime_command
    run = _run(request, run_id)
    result = execute_runtime_command(connection=run.computer_binding.connection,
        operation='workspace.browse_directories', required_scope='workspace.select',
        payload={'path': path or run.workspace_root}, timeout_seconds=15,
        display_run_id=str(run.id), caller_subject_hash=run.caller_subject_hash)
    return {**result, 'cwd': run.workspace_cwd or '.', 'workspace_root': run.workspace_root,
            'revision': run.computer_revision, 'device': True}


def _idle(run, execution):
    from .computer_switching import ComputerSwitchConflict
    from apps.workspaces.models import ComputerRuntimeCommand
    if run.status not in {'completed', 'failed'} or (execution and execution.state in {'queued', 'running'}):
        raise ComputerSwitchConflict('Stop this Run and wait before selecting a different workspace.')
    if run.runtime_invocations.filter(status='pending').exists() or ComputerRuntimeCommand.objects.filter(
        display_run_id=run.id, status__in=['queued', 'dispatched', 'running'], expires_at__gt=timezone.now()).exists():
        raise ComputerSwitchConflict('Previous Computer operations are still settling. Retry shortly.')


def select(*, request, run_id, path, confirmed, expected_root, expected_revision):
    from .models import AgentDisplayRun, AgentTaskExecution
    from .computer_switching import ComputerSwitchConflict
    if confirmed is not True or not isinstance(path, str) or not path or len(path) > 4096:
        raise exceptions.ValidationError('Select a directory and explicitly confirm access for this Run.')
    run = _run(request, run_id)
    _idle(run, AgentTaskExecution.objects.filter(task__run=run).first())
    # Perform remote validation outside the DB transaction; no implicit mkdir.
    checked = browse(request=request, run_id=run_id, path=path)
    with transaction.atomic():
        execution = AgentTaskExecution.objects.select_for_update(of=('self',)).filter(task__run=run).first()
        locked = AgentDisplayRun.objects.select_for_update().get(pk=run.pk)
        _run(request, run_id)
        _idle(locked, execution)
        if (locked.computer_binding_id, locked.computer_revision, locked.workspace_root) != (
            run.computer_binding_id, expected_revision, expected_root):
            raise ComputerSwitchConflict('The Computer or workspace changed. Refresh and choose again.')
        locked.workspace_root = checked['path'].replace('\\', '/')
        locked.workspace_cwd = '.'
        locked.workspace_delegate_token_hash = ''
        locked.workspace_delegate_token_expires_at = None
        locked.save(update_fields=['workspace_root', 'workspace_cwd', 'workspace_delegate_token_hash', 'workspace_delegate_token_expires_at', 'updated_at'])
        locked.context_grants.filter(redeemed_at__isnull=True).update(expires_at=timezone.now())
    return {**checked, 'workspace_root': locked.workspace_root, 'cwd': '.'}
