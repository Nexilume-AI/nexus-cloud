"""Attach server-verified Run directory grants to signed Computer commands."""
import posixpath
from rest_framework import exceptions


def within(path, root):
    path, root = (posixpath.normpath(str(value).replace('\\', '/')) for value in (path, root))
    if len(root) > 1 and root[1] == ':':
        path, root = path.casefold(), root.casefold()
    return path == root or path.startswith(root.rstrip('/') + '/')


def authorize_command_roots(*, connection, payload, display_run_id, caller_subject_hash, operation=None):
    from apps.agents.models import AgentDisplayRun
    result = dict(payload)
    # Never trust a root grant supplied by an Agent or other upstream payload.
    result.pop('_authorized_workspace_roots', None)
    if operation is not None and operation not in {'workspace.ensure_directory', 'workspace.list_files', 'workspace.read_file',
            'workspace.write_file', 'workspace.transfer_read', 'workspace.transfer_write', 'command.execute', 'terminal.open'}:
        return result
    if not display_run_id:
        return result
    run = AgentDisplayRun.objects.filter(id=display_run_id,
        computer_binding__connection=connection, caller_subject_hash=caller_subject_hash,
        consumer_tenant_id=connection.tenant_id).first()
    if run is None:
        raise exceptions.PermissionDenied('Computer command is not bound to this caller and Run.')
    roots = [value for value in (run.workspace_root, run.output_root) if value]
    result['_authorized_workspace_roots'] = roots
    # Older workspace runners pass the pairing root for absolute file/command
    # paths. Narrow it to the explicitly selected Run root when that root lies
    # outside the original pairing directory.
    for root in sorted(roots, key=len, reverse=True):
        if within(root, connection.workspace_root):
            continue
        target = result.get('path') or result.get('cwd') or result.get('workspace_root') or ''
        if within(target, root):
            result['workspace_root'] = root
            break
    return result
