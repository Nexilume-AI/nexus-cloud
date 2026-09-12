"""Real personal terminal HTTP, ticket and context checks; no fake shell."""
from asgiref.sync import async_to_sync
from unittest.mock import AsyncMock, patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token
from apps.accounts.models import AccountProfile
from apps.common.jwt import issue_token_pair
from rest_framework import exceptions
from apps.workspaces import asgi, execution
from apps.workspaces.models import WorkspaceTerminalSession
from nexus_personal.services import provision_owner
from . import test_computer_runtime_http as computer_http
from .test_installation import PASSWORD


@override_settings(NEXUS_PUBLIC_BASE_URL="https://personal.example")
class PersonalTerminalHTTPTests(TestCase):
    setUp = computer_http.PersonalComputerRuntimeTests.setUp
    pair = computer_http.PersonalComputerRuntimeTests.pair
    ticket = computer_http.PersonalComputerRuntimeTests.ticket
    connect = computer_http.PersonalComputerRuntimeTests.connect

    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="terminal-owner@example.test", password=PASSWORD)
        cls.owner_token = Token.objects.create(user=cls.row.owner)

    def create(self):
        device, _ = self.pair()
        response = self.client.post("/api/v1/workspace-terminal-sessions/", {"connection_id": str(device.connection_id)}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return device, WorkspaceTerminalSession.objects.get(pk=response.data["id"])

    def ticket_scope(self, session):
        response = self.client.post(f"/api/v1/workspace-terminal-sessions/{session.pk}/ticket/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        token = response.data["ticket"]
        return {"path": response.data["websocket_url"], "subprotocols": ["nexus-terminal-v1", "nexus-terminal-ticket." + token]}

    def test_create_resume_list_and_ticket_is_single_use_and_not_serialized(self):
        device, session = self.create()
        again = self.client.post("/api/v1/workspace-terminal-sessions/", {"connection_id": str(device.connection_id)}, format="json")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.data["id"], str(session.pk))
        self.assertEqual(WorkspaceTerminalSession.objects.count(), 1)
        scope = self.ticket_scope(session)
        response = self.client.get(f"/api/v1/workspace-terminal-sessions/{session.pk}/")
        self.assertNotIn("_websocket_tickets", response.data["metadata"])
        self.assertEqual(async_to_sync(asgi.resolve_terminal_session)(scope).pk, session.pk)
        with self.assertRaises(ValueError):
            async_to_sync(asgi.resolve_terminal_session)(scope)
        self.assertEqual(len(self.client.get("/api/v1/workspace-terminal-sessions/").data), 1)

    def test_ticket_rejects_closed_session_and_revoked_device(self):
        device, session = self.create()
        scope = self.ticket_scope(session)
        self.assertEqual(self.client.post(f"/api/v1/workspace-terminal-sessions/{session.pk}/close/", {}, format="json").status_code, 200)
        with self.assertRaises(ValueError):
            async_to_sync(asgi.resolve_terminal_session)(scope)
        self.assertEqual(self.client.post(f"/api/v1/workspace-terminal-sessions/{session.pk}/ticket/", {}, format="json").status_code, 404)
        response = self.client.post("/api/v1/workspace-terminal-sessions/", {"connection_id": str(device.connection_id)}, format="json")
        resumed = WorkspaceTerminalSession.objects.get(pk=response.data["id"])
        scope = self.ticket_scope(resumed)
        self.client.post(f"/api/v1/computers/{device.connection_id}/revoke/", {}, format="json")
        with self.assertRaises(ValueError):
            async_to_sync(asgi.resolve_terminal_session)(scope)

    def test_context_change_hides_session_and_invalidates_ticket(self):
        device, session = self.create()
        scope = self.ticket_scope(session)
        other = get_user_model().objects.create_user(username="terminal-other")
        device.connection.created_by = other
        device.connection.save(update_fields=["created_by"])
        self.assertEqual(self.client.get("/api/v1/workspace-terminal-sessions/").data, [])
        self.assertEqual(self.client.get(f"/api/v1/workspace-terminal-sessions/{session.pk}/").status_code, 404)
        with self.assertRaises(ValueError):
            async_to_sync(asgi.resolve_terminal_session)(scope)

    def test_disabled_owner_invalidates_already_issued_ticket(self):
        _, session = self.create()
        scope = self.ticket_scope(session)
        AccountProfile.objects.filter(user=self.row.owner).update(status="disabled")
        with self.assertRaises(ValueError):
            async_to_sync(asgi.resolve_terminal_session)(scope)

    def test_workspace_path_and_limits_still_reject_escape(self):
        for path in ("../outside", "/outside", "~/outside", "..\\outside"):
            with self.assertRaises(execution.WorkspaceError):
                execution.resolve_workspace_relative_path(path=path)
        self.assertEqual(execution.resolve_workspace_relative_path(base="sub", path="file.txt"), "sub/file.txt")
        with self.assertRaises(execution.WorkspaceError):
            execution.normalize_workspace_command_timeout(0)

    def test_socket_jwt_requires_current_personal_access_claims(self):
        _, session = self.create()
        tokens = issue_token_pair(user=self.row.owner, tenant_id=str(self.row.tenant_id), project_id=str(self.row.project_id))
        scope = {"path": f"/ws/workspace-terminals/{session.pk}/", "query_string": b"", "headers": [
            (b"authorization", ("Bearer " + tokens["access_token"]).encode())]}
        self.assertEqual(async_to_sync(asgi.resolve_terminal_session)(scope).pk, session.pk)
        scope["headers"] = [(b"authorization", ("Bearer " + tokens["refresh_token"]).encode())]
        with self.assertRaises(exceptions.AuthenticationFailed):
            async_to_sync(asgi.resolve_terminal_session)(scope)
        scope["headers"] = [(b"authorization", ("Bearer " + tokens["access_token"]).encode()),
                            (b"x-nexus-project", b"foreign-project")]
        with self.assertRaises(exceptions.PermissionDenied):
            async_to_sync(asgi.resolve_terminal_session)(scope)

    def test_terminal_open_failure_does_not_log_device_error_contents(self):
        _, session = self.create()
        scope = self.ticket_scope(session)
        marker = "terminal-sensitive-error-fixture-not-a-credential"
        receive, send = AsyncMock(), AsyncMock()
        # Inject a failure before any terminal opens; no fake shell/runner executes.
        with patch.object(asgi, "open_terminal_for_session", side_effect=RuntimeError(marker)), \
                self.assertLogs("apps.workspaces.asgi", level="WARNING") as logs:
            async_to_sync(asgi.workspace_terminal)(scope=scope, receive=receive, send=send)
        send.assert_awaited_once_with({"type": "websocket.close", "code": 1011})
        receive.assert_not_awaited()
        self.assertIn(str(session.pk), "\n".join(logs.output))
        self.assertIn("RuntimeError", "\n".join(logs.output))
        self.assertNotIn(marker, "\n".join(logs.output))
        for record in logs.records:
            self.assertNotIn(marker, repr(record.args))
            self.assertIsNone(record.exc_info)
            self.assertIsNone(record.stack_info)
