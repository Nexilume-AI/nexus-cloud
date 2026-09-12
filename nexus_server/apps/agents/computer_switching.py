"""Explicit, caller-authorized device changes between completed Run turns."""
import posixpath
import secrets

from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions

from .models import AgentDisplayRun, AgentRunComputerAttachment, AgentTaskExecution, AgentComputerBinding


class ComputerSwitchConflict(exceptions.APIException):
    status_code = 409
    default_code = "RUN_COMPUTER_CHANGE_CONFLICT"
    default_detail = "Stop this Run and wait for completion before changing its Computer."


def computer_payload(run):
    connection = run.computer_binding.connection if run.computer_binding_id else None
    device = getattr(connection, "runtime_device", None) if connection else None
    attachment = run.computer_attachments.filter(revision=run.computer_revision).first()
    history = list(run.computer_attachments.order_by("revision").values("revision", "computer_name"))
    return {
        "revision": run.computer_revision,
        "name": connection.name if connection else "",
        "online": bool(device and device.online),
        "platform": device.platform if device else "",
        "browser_available": bool(device and device.online and (device.capabilities or {}).get("browser.v1")),
        "event_cursor": attachment.after_event_seq if attachment else 0,
        "history": history,
    }


def event_computer_revision(event):
    attachment = event.run.computer_attachments.filter(after_event_seq__lt=event.seq).order_by("-revision").first()
    return attachment.revision if attachment else 0


@transaction.atomic
def switch_private_run_computer(*, request, run_id, connection_id, expected_revision):
    from .services import get_private_display_run, create_computer_binding, AgentNotFound
    from apps.workspaces.connection_core import get_workspace_connection
    from apps.workspaces.models import WorkspaceTerminalSession, ComputerRuntimeCommand
    from .models import AgentBrowserSession, AgentDisplayEvent

    visible = get_private_display_run(request=request, run_id=run_id)
    # Match Worker/cancel lock order: envelope -> Run -> Task.
    execution = AgentTaskExecution.objects.select_for_update(of=("self",)).filter(task__run=visible).first()
    run = AgentDisplayRun.objects.select_for_update().get(pk=visible.pk)
    if run.status not in {"completed", "failed"} or (execution and execution.state in {"queued", "running"}):
        raise ComputerSwitchConflict()
    if run.runtime_invocations.filter(status="pending").exists() or ComputerRuntimeCommand.objects.filter(
        display_run_id=run.pk, status__in=["queued", "dispatched", "running"], expires_at__gt=timezone.now()).exists():
        raise ComputerSwitchConflict("Previous Computer operations are still settling. Retry shortly.")
    if run.computer_revision != expected_revision:
        raise ComputerSwitchConflict("The Run Computer changed in another tab. Refresh before choosing again.")
    if run.agent.computer_requirement == "disabled":
        raise exceptions.ValidationError("This Agent does not accept a Computer.")
    connection = get_workspace_connection(request=request, connection_id=connection_id)
    if (connection.tenant_id != run.consumer_tenant_id or connection.owner_subject_hash != run.caller_subject_hash
        or connection.project_id not in {None, run.consumer_project_id}):
        raise AgentNotFound()
    existing = AgentComputerBinding.objects.filter(tenant_id=run.consumer_tenant_id, agent=run.agent,
        caller_subject_hash=run.caller_subject_hash, connection=connection, status="active").first()
    binding = create_computer_binding(request=request, agent_id=str(run.agent_id), connection_id=str(connection.id),
        is_default=bool(existing and existing.is_default))
    from apps.workspaces.computer_runtime import ensure_runtime_connection
    for scope in run.workspace_capabilities_snapshot or []:
        ensure_runtime_connection(connection, operation={"browser.control":"browser.open", "command.execute":"command.execute",
            "tool.setup":"tool_setup.detect"}.get(scope, "workspace.test"))
    if run.computer_binding_id == binding.pk:
        return run

    previous_name = run.computer_binding.connection.name if run.computer_binding_id else "No Computer"
    terminal = WorkspaceTerminalSession.objects.filter(display_run=run).first()
    browser = AgentBrowserSession.objects.filter(run=run).first()
    old, _ = AgentRunComputerAttachment.objects.get_or_create(run=run, revision=run.computer_revision, defaults={
        "binding": run.computer_binding, "computer_name": previous_name,
        "workspace_root": run.workspace_root, "output_root": run.output_root})
    old.terminal_session, old.browser_session = terminal, browser
    old.save(update_fields=["terminal_session", "browser_session"])
    # Preserve rows and original device identities, release only current pointers.
    if terminal:
        terminal.display_run = None
        terminal.status, terminal.ended_at = "closed", timezone.now()
        terminal.save(update_fields=["display_run", "status", "ended_at", "updated_at"])
    if browser:
        browser.run = None
        browser.status, browser.closed_at = "closed", timezone.now()
        browser.save(update_fields=["run", "status", "closed_at"])
    run.output_artifacts.filter(computer_revision=run.computer_revision, snapshot_status="pending").update(
        snapshot_status="failed", snapshot_error="Original Computer snapshot was not completed before switching. No files were migrated.")
    run.computer_revision += 1
    run.computer_binding = binding
    agent_root = posixpath.join(str(connection.workspace_root or "~/.nexus").replace("\\", "/").rstrip("/"), "agents", str(run.agent_id))
    run.workspace_root = posixpath.join(agent_root, "workspace")
    run.workspace_cwd = "."
    run.output_root = posixpath.join(agent_root, "runs", str(run.id), "computers", str(run.computer_revision), "outputs")
    run.write_token = secrets.token_urlsafe(32)
    for kind in ("workspace_delegate", "browser_delegate", "interaction"):
        setattr(run, kind + "_token_hash", "")
        setattr(run, kind + "_token_expires_at", None)
    from .context_extension import cleared_run_context_fields
    for field_name, field_value in cleared_run_context_fields().items():
        setattr(run, field_name, field_value)
    run.save()
    run.context_grants.filter(redeemed_at__isnull=True).update(expires_at=timezone.now())
    cursor = run.events.order_by("-seq").values_list("seq", flat=True).first() or 0
    AgentRunComputerAttachment.objects.create(run=run, revision=run.computer_revision, binding=binding,
        computer_name=connection.name, workspace_root=run.workspace_root, output_root=run.output_root, after_event_seq=cursor)
    # Status only in the developer trace; caller-only payload supplies names/history.
    # This control-plane record is written under the Run lock. Keep the event
    # ingestion API closed to business reporters after the Run has ended.
    AgentDisplayEvent.objects.create(tenant=run.tenant, agent=run.agent, run=run, seq=cursor + 1,
        event_type="CUSTOM", payload_json={"type":"CUSTOM", "name":"nexus.computer.changed",
        "value":{"revision":run.computer_revision, "status":"ready_for_next_turn", "files_migrated":False}}, visibility="public")
    return run
