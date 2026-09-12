"""Real owned HTTP/DB and signed device protocol, not physical Computer/phone execution.

Folder race negatives inject a concurrent DB change at directory validation;
the separate existing ASGI bridge executes positive folder I/O with the real SDK.
"""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.agents.models import Agent, AgentComputerBinding, AgentWorkspaceGrant, AgentMobileGrant
from apps.workspaces.models import WorkspaceConnection, WorkspaceTerminalSession, ComputerRuntimeDevice
from apps.tenancy.models import Project
from . import test_computer_runtime_http as computers, test_mobile_http as mobiles, test_agent_file_http as files


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PUBLIC_BASE_URL="https://personal.example")
class PersonalAgentDeviceHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        computers.PersonalComputerRuntimeTests.setUpTestData.__func__(cls)

    def setUp(self):
        computers.PersonalComputerRuntimeTests.setUp(self)
        self.device_client = APIClient()
        self.agent = Agent.objects.create(tenant=self.row.tenant, project=self.row.project,
            created_by=self.row.owner, name="Device-agent", computer_requirement="required",
            workspace_capabilities=["files.list", "files.read", "command.execute"],
            mobile_requirement="required", mobile_capabilities=["mobile.observe", "mobile.tap"])
        self.base = f"/api/v1/agents/{self.agent.pk}/"

    pair = computers.PersonalComputerRuntimeTests.pair
    ticket = computers.PersonalComputerRuntimeTests.ticket
    connect = computers.PersonalComputerRuntimeTests.connect
    create_device = mobiles.PersonalMobileHTTPTests.create_device
    device_path = mobiles.PersonalMobileHTTPTests.device_path

    def attach(self, device):
        return self.client.post(self.base + "computer-bindings/",
            {"connection_id": str(device.connection_id)}, format="json")

    def test_signed_computer_pairing_attach_repeat_and_delete(self):
        device, key = self.pair()
        self.connect(device, key)
        response = self.attach(device)
        self.assertEqual(response.status_code, 201, response.data)
        binding_id = response.data["id"]
        self.assertEqual(response.data["connection_id"], str(device.connection_id))
        self.assertNotIn("public_key", str(response.data))
        self.assertEqual(self.attach(device).data["id"], binding_id)
        listing = self.client.get(self.base + "computer-bindings/")
        self.assertEqual(listing.status_code, 200, listing.data)
        self.assertEqual([row["id"] for row in listing.data], [binding_id])
        self.assertFalse(AgentWorkspaceGrant.objects.exists(), "Attach must not silently grant scopes")
        path = self.base + f"computer-bindings/{binding_id}/"
        self.assertEqual(self.client.delete(path).status_code, 204)
        self.assertEqual(self.client.get(self.base + "computer-bindings/").data, [])
        self.assertEqual(self.client.delete(path).status_code, 404)
        self.assertTrue(ComputerRuntimeDevice.objects.filter(pk=device.pk).exists())

    def test_offline_missing_capability_and_legacy_computer_cannot_attach(self):
        device, key = self.pair()
        self.connect(device, key)
        ComputerRuntimeDevice.objects.filter(pk=device.pk).update(last_seen_at=timezone.now()-timedelta(days=1))
        response = self.attach(device)
        self.assertEqual(response.status_code, 503, response.data)
        self.assertIn("COMPUTER_RUNTIME_OFFLINE", str(response.data))
        ComputerRuntimeDevice.objects.filter(pk=device.pk).update(last_seen_at=timezone.now(), capabilities={"workspace.v1": 1})
        response = self.attach(device)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn("terminal.v1", str(response.data))
        WorkspaceConnection.objects.filter(pk=device.connection_id).update(connection_type="ssh")
        self.assertEqual(self.attach(device).status_code, 404)
        self.assertFalse(AgentComputerBinding.objects.exists())

    def test_scope_grants_are_explicit_bounded_revocable_and_independent(self):
        for endpoint, scopes, extra in (
            ("workspace-grant/", ["files.list", "files.read"], "files.write"),
            ("mobile-grant/", ["mobile.observe"], "mobile.type_text"),
        ):
            response = self.client.get(self.base + endpoint)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["status"], "not_granted")
            granted = self.client.put(self.base + endpoint, {"scopes": scopes}, format="json")
            self.assertEqual(granted.status_code, 200, granted.data)
            self.assertEqual(set(granted.data["scopes"]), set(scopes))
            denied = self.client.put(self.base + endpoint, {"scopes": [extra]}, format="json")
            self.assertEqual(denied.status_code, 400, denied.data)
            self.assertEqual(set(self.client.get(self.base + endpoint).data["scopes"]), set(scopes))
            self.assertEqual(self.client.delete(self.base + endpoint).status_code, 204)
            self.assertEqual(self.client.get(self.base + endpoint).data["scopes"], [])

    def test_mobile_pairing_attach_change_default_and_delete(self):
        first, token = self.create_device(name="First phone")
        second, _ = self.create_device(name="Second phone")
        bindings = []
        for device in (first, second):
            response = self.client.post(self.base + "mobile-bindings/", {"device_id": str(device.pk)}, format="json")
            self.assertEqual(response.status_code, 201, response.data)
            self.assertNotIn(token, str(response.data))
            bindings.append(response.data["id"])
        self.assertFalse(AgentMobileGrant.objects.exists())
        first_path = self.base + f"mobile-bindings/{bindings[0]}/"
        response = self.client.patch(first_path, {"is_default": True}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        listing = self.client.get(self.base + "mobile-bindings/")
        self.assertEqual([r["id"] for r in listing.data if r["is_default"]], [bindings[0]])
        self.assertEqual(self.client.delete(first_path).status_code, 204)
        self.assertEqual([r["id"] for r in self.client.get(self.base + "mobile-bindings/").data], [bindings[1]])

    def test_foreign_device_and_foreign_agent_are_hidden(self):
        other = get_user_model().objects.create_user(username="foreign-device-owner")
        device, key = self.pair()
        self.connect(device, key)
        WorkspaceConnection.objects.filter(pk=device.connection_id).update(created_by=other)
        self.assertEqual(self.attach(device).status_code, 404)
        phone, _ = self.create_device()
        type(phone).objects.filter(pk=phone.pk).update(created_by=other)
        self.assertEqual(self.client.post(self.base + "mobile-bindings/",
            {"device_id": str(phone.pk)}, format="json").status_code, 404)
        self.agent.project = Project.objects.create(tenant=self.row.tenant, name="Not personal")
        self.agent.save(update_fields=["project"])
        for endpoint in ("computer-bindings/", "mobile-bindings/", "workspace-grant/", "mobile-grant/"):
            self.assertEqual(self.client.get(self.base + endpoint).status_code, 404, endpoint)

    def test_session_mutations_require_csrf_and_organization_grant_is_not_exposed(self):
        session = APIClient(enforce_csrf_checks=True)
        session.force_login(self.row.owner)
        for endpoint, method, data in (
            ("workspace-grant/", "put", {"scopes": ["files.list"]}),
            ("mobile-grant/", "put", {"scopes": ["mobile.observe"]}),
            ("computer-bindings/", "post", {"connection_id": str(uuid4())}),
            ("mobile-bindings/", "post", {"device_id": str(uuid4())}),
        ):
            self.assertEqual(getattr(session, method)(self.base + endpoint, data, format="json").status_code, 403)
        self.assertEqual(self.client.get(self.base + "project-context-grant/").status_code, 404)
        self.assertIn(APIClient().get(self.base + "computer-bindings/").status_code, (401, 403))


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PUBLIC_BASE_URL="https://personal.example")
class PersonalRunDeviceHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        files.PersonalAgentFileHTTPTests.setUpTestData.__func__(cls)

    def setUp(self):
        files.PersonalAgentFileHTTPTests.setUp(self)
        self.peer = APIClient()
        self.headers = {"HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN": self.display_token()}

    display_token = files.PersonalAgentFileHTTPTests.display_token
    pair = computers.PersonalComputerRuntimeTests.pair
    ticket = computers.PersonalComputerRuntimeTests.ticket
    connect = computers.PersonalComputerRuntimeTests.connect

    def test_completed_run_switch_preserves_identity_and_clears_only_host_context(self):
        from apps.agents.context_extension import cleared_run_context_fields
        self.assertEqual(cleared_run_context_fields(), {})
        device, key = self.pair()
        self.connect(device, key)
        self.agent.computer_requirement = "required"
        self.agent.workspace_capabilities = ["files.list"]
        self.agent.save(update_fields=["computer_requirement", "workspace_capabilities"])
        response = self.client.put(f"/api/v1/agents/{self.agent.pk}/workspace-grant/",
            {"scopes": ["files.list"]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.run.status = "completed"
        self.run.workspace_capabilities_snapshot = ["files.list"]
        self.run.save(update_fields=["status", "workspace_capabilities_snapshot"])
        previous_write_token = self.run.write_token
        response = self.client.post(self.base + "computer/",
            {"connection_id": str(device.connection_id), "expected_revision": 0}, format="json", **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["id"], str(self.run.pk))
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "completed")
        self.assertEqual(self.run.computer_revision, 1)
        self.assertEqual(self.run.computer_binding.connection_id, device.connection_id)
        self.assertEqual(self.run.workspace_cwd, ".")
        self.assertNotEqual(self.run.write_token, previous_write_token)
        self.assertEqual(self.run.workspace_delegate_token_hash, "")
        self.assertFalse(hasattr(self.run, "billing_token_hash"))
        self.assertEqual(self.run.computer_attachments.count(), 2)
        changed = self.run.events.get(payload_json__name="nexus.computer.changed")
        self.assertFalse(changed.payload_json["value"]["files_migrated"])
        stale = self.client.post(self.base + "computer/",
            {"connection_id": str(device.connection_id), "expected_revision": 0}, format="json", **self.headers)
        self.assertEqual(stale.status_code, 409, stale.data)
        self.run.refresh_from_db()
        self.assertEqual(self.run.computer_revision, 1)

    def test_run_device_routes_require_current_display_authority(self):
        for headers in ({}, {"HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN": "wrong"}):
            self.assertEqual(self.client.get(self.base + "computer/", **headers).status_code, 404)
            self.assertEqual(self.client.patch(self.base + "computer/", {"path": "."}, format="json", **headers).status_code, 404)
            self.assertEqual(self.client.post(self.base + "terminal-ticket/", {}, format="json", **headers).status_code, 404)
        self.assertEqual(self.client.get(self.base + "computer/", **self.headers).status_code, 404)
        response = self.client.post(self.base + "terminal-ticket/", {}, format="json", **self.headers)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("terminal has not started", str(response.data))

    def test_computer_switch_rejects_active_turn_before_any_device_side_effect(self):
        response = self.client.post(self.base + "computer/",
            {"connection_id": str(uuid4()), "expected_revision": 0}, format="json", **self.headers)
        self.assertEqual(response.status_code, 409, response.data)
        self.run.refresh_from_db()
        self.assertIsNone(self.run.computer_binding_id)
        self.assertEqual(self.run.computer_revision, 0)
        self.assertFalse(AgentComputerBinding.objects.exists())

    def test_terminal_ticket_binds_exact_caller_run_and_session(self):
        from django.core.cache import cache
        from apps.common.subjects import hash_token
        connection = WorkspaceConnection.objects.create(tenant=self.row.tenant, project=self.row.project,
            created_by=self.row.owner, name="Session record", connection_type="runtime",
            owner_subject_hash=self.run.caller_subject_hash)
        session = WorkspaceTerminalSession.objects.create(tenant=self.row.tenant, project=self.row.project,
            created_by=self.row.owner, connection=connection, display_run=self.run, status="active")
        response = self.client.post(self.base + "terminal-ticket/", {}, format="json", **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        ticket = response.data["ticket"]
        self.assertEqual(response.data["expires_in"], 60)
        self.assertEqual(cache.get("agent-terminal-ticket:" + hash_token(ticket)),
            {"run_id": str(self.run.pk), "session_id": str(session.pk),
             "caller_subject_hash": self.run.caller_subject_hash})
        self.assertIn(str(self.run.pk), response.data["websocket_url"])
        # This proves ticket authorization, not a running Terminal process.

    def test_folder_validation_rechecks_computer_and_caller_before_commit(self):
        from unittest.mock import patch
        connection = WorkspaceConnection.objects.create(tenant=self.row.tenant, project=self.row.project,
            created_by=self.row.owner, name="Race record", connection_type="runtime",
            owner_subject_hash=self.run.caller_subject_hash)
        binding = AgentComputerBinding.objects.create(tenant=self.row.tenant, project=self.row.project,
            agent=self.agent, connection=connection, caller_subject_hash=self.run.caller_subject_hash)
        self.run.computer_binding = binding
        self.run.workspace_root = "/authorized/workspace"
        self.run.workspace_cwd = "."
        self.run.save(update_fields=["computer_binding", "workspace_root", "workspace_cwd"])
        original_subject = self.run.caller_subject_hash
        for changes, status in (({"computer_revision": 1}, 409),
                                ({"workspace_root": "/changed/workspace"}, 409),
                                ({"caller_subject_hash": "different-caller"}, 404)):
            with self.subTest(changes=changes):
                type(self.run).objects.filter(pk=self.run.pk).update(computer_revision=0,
                    workspace_root="/authorized/workspace", caller_subject_hash=original_subject)
                def validation(**kwargs):
                    type(self.run).objects.filter(pk=self.run.pk).update(**changes)
                    return {"items": []}
                with patch("apps.workspaces.execution.list_workspace_files", side_effect=validation) as remote:
                    response = self.client.patch(self.base + "computer/", {"path": "nested"},
                        format="json", **self.headers)
                    self.assertEqual(response.status_code, status, response.data)
                    self.assertEqual(remote.call_count, 1)
                self.run.refresh_from_db()
                self.assertEqual(self.run.workspace_cwd, ".")
