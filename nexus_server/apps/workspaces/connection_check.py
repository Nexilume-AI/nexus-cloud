"""Owner-authorized connection checks through the configured transport."""
from django.db import transaction
from django.utils import timezone
from .connection_core import get_workspace_connection, require_workspace_own
from .models import WorkspaceConnection
from .runner_dispatch import workspace_runner_for


def check_workspace_connection(*, request, connection_id):
    connection = get_workspace_connection(request=request, connection_id=connection_id)
    require_workspace_own(request=request, tenant=connection.tenant,
        action="workspace.connection.use_own", connection=connection)
    result = workspace_runner_for(connection).test(connection=connection)
    status = WorkspaceConnection.TEST_SUCCEEDED if result.ok else WorkspaceConnection.TEST_FAILED
    with transaction.atomic():
        current = WorkspaceConnection.objects.select_for_update().get(pk=connection.pk)
        current.last_test_status = status
        current.last_test_error = result.error[:1024]
        current.last_test_at = timezone.now()
        current.metadata = {**current.metadata, "last_facts": result.facts}
        current.save(update_fields=["last_test_status", "last_test_error", "last_test_at", "metadata", "updated_at"])
    return {"status": status, "facts": result.facts, "checks": result.checks, "error": result.error}
