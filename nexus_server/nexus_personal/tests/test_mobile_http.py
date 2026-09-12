"""Real Mobile HTTP, queue and Run delegate services; no physical-phone claim.

Device protocol messages are submitted by the test client. No executor, policy,
database, billing service or successful command handler is mocked.
"""
import base64
import uuid
from datetime import timedelta
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from apps.agents import mobile_access, workspace_grants
from apps.agents.models import Agent, AgentDisplayRun, AgentMobileLease
from apps.common.resource_limits import capability_state
from apps.common.subjects import request_subject
from apps.mobile.models import MobileDevice, MobileCommand
from apps.tenancy.models import Project, Tenant
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7S8AAAAASUVORK5CYII=")


class PersonalMobileHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="mobile-owner@example.test", password=PASSWORD)
        cls.owner_token = Token.objects.create(user=cls.row.owner)

    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.owner_token.key)
        self.device_client = APIClient()

    def request(self):
        return SimpleNamespace(user=self.row.owner, META={}, headers={}, query_params={},
            tenant_id=str(self.row.tenant_id), project_id=str(self.row.project_id))

    def device_path(self, device, suffix=""):
        return f"/api/v1/mobile-devices/{device.pk}/{suffix}"

    def command_path(self, command, suffix=""):
        return f"/api/v1/mobile-commands/{command}/{suffix}"

    def create_device(self, *, pair=True, **overrides):
        response = self.client.post("/api/v1/mobile-devices/", {"name": "Personal phone", **overrides}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        token = response.data["pairing_token"]
        device = MobileDevice.objects.get(pk=response.data["id"])
        self.assertNotEqual(device.token_hash, token)
        self.assertEqual(device.project_id, self.row.project_id)
        self.device_client.credentials(HTTP_X_NEXUS_MOBILE_TOKEN=token)
        if pair:
            response = self.device_client.post(self.device_path(device, "device/heartbeat/"),
                {"capabilities": {"accessibility": True}, "online_status": "online"}, format="json")
            self.assertEqual(response.status_code, 200, response.data)
            device.refresh_from_db()
            self.assertEqual(device.lifecycle_status, "online")
        return device, token

    def enqueue(self, device, action="observe", **fields):
        response = self.client.post(self.device_path(device, "commands/"), {"action": action, **fields}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def next_command(self, device):
        response = self.device_client.post(self.device_path(device, "device/commands/next/"), {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def result(self, command, result):
        response = self.device_client.post(self.command_path(command, "device/result/"),
            {"status": "succeeded", "result": result}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def run_fixture(self, device):
        request = self.request()
        scopes = ["mobile.observe", "mobile.screen.capture", "mobile.tap"]
        agent = Agent.objects.create(tenant=self.row.tenant, project=self.row.project,
            name="Mobile-agent", created_by=self.row.owner, mobile_requirement="required", mobile_capabilities=scopes)
        mobile_access.set_mobile_grant(request=request, agent=agent, scopes=scopes)
        binding = mobile_access.create_mobile_binding(request=request, agent_id=str(agent.pk), device_id=str(device.pk))
        token, digest, expiry = mobile_access.issue_mobile_delegate_token(capabilities=scopes)
        run = AgentDisplayRun.objects.create(tenant=self.row.tenant, agent=agent,
            consumer_tenant=self.row.tenant, consumer_project=self.row.project,
            caller_subject_hash=request_subject(request).subject_hash, caller_principal_type="user",
            caller_principal_id=str(self.row.owner_id), run_kind="invocation", status="running",
            interaction_mode="task", mobile_binding=binding, mobile_capabilities_snapshot=scopes,
            mobile_delegate_token_hash=digest, mobile_delegate_token_expires_at=expiry)
        return agent, run, token

    def test_pair_queue_observe_screenshot_download_and_result_replay_rejection(self):
        device, token = self.create_device()
        response = self.client.get("/api/v1/mobile-devices/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data[0]["id"], str(device.pk))
        self.assertNotIn("pairing_token", response.data[0])
        self.assertNotIn("token_hash", response.data[0])
        command = self.enqueue(device)
        self.assertEqual(self.next_command(device)["id"], command["id"])
        self.assertEqual(self.result(command["id"], {"text": "Personal screen"})["status"], "succeeded")
        self.assertEqual(self.client.get(self.device_path(device)).data["last_observation"], {"text": "Personal screen"})
        replay = self.device_client.post(self.command_path(command["id"], "device/result/"), {"status": "succeeded"}, format="json")
        self.assertEqual(replay.status_code, 400, replay.data)
        shot = self.enqueue(device, "capture_screen")
        self.assertEqual(self.next_command(device)["id"], shot["id"])
        result = self.result(shot["id"], {"screenshot_base64": base64.b64encode(PNG).decode(), "content_type": "image/png"})
        self.assertNotIn("screenshot_base64", result["result"])
        download = self.client.get(self.device_path(device, "screenshot/"))
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, PNG)
        self.assertEqual(self.next_command(device), {"command": None})

    def test_approval_cancel_expiry_and_invalid_coordinates_are_real(self):
        device, _ = self.create_device()
        command = self.enqueue(device, "type_text", arguments={"text": "Hello"})
        self.assertEqual(command["status"], "pending_approval")
        self.assertEqual(self.next_command(device), {"command": None})
        response = self.client.post(self.command_path(command["id"], "approve/"), {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.next_command(device)["id"], command["id"])
        # The existing device protocol cannot recall an already-dispatched
        # command. Do not report cancellation of external effects as success.
        self.assertEqual(self.client.post(self.command_path(command["id"], "cancel/"), {}, format="json").status_code, 400)
        self.result(command["id"], {"typed": True})
        canceled = self.enqueue(device)
        self.assertEqual(self.client.post(self.command_path(canceled["id"], "cancel/"), {}, format="json").status_code, 200)
        command = self.enqueue(device)
        MobileCommand.objects.filter(pk=command["id"]).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.next_command(device), {"command": None})
        self.assertEqual(MobileCommand.objects.get(pk=command["id"]).status, "canceled")
        response = self.client.post(self.device_path(device, "commands/"),
            {"action": "tap_coordinates", "arguments": {"x": -1, "y": 2}}, format="json")
        self.assertEqual(response.status_code, 400, response.data)

    def test_token_rotation_expiry_disabled_owner_and_no_owner_token_substitution(self):
        device, token = self.create_device(pair=False)
        expired = (timezone.now() - timedelta(seconds=1)).isoformat()
        device.metadata["pairing_token_expires_at"] = expired
        device.save()
        self.assertEqual(self.device_client.post(self.device_path(device, "device/heartbeat/"), {}, format="json").status_code, 403)
        response = self.client.post(self.device_path(device, "rotate-token/"), {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        replacement = response.data["pairing_token"]
        self.assertNotEqual(token, replacement)
        for invalid in (token, self.owner_token.key, "invalid"):
            self.device_client.credentials(HTTP_X_NEXUS_MOBILE_TOKEN=invalid)
            self.assertEqual(self.device_client.post(self.device_path(device, "device/heartbeat/"), {}, format="json").status_code, 403)
        self.device_client.credentials(HTTP_X_NEXUS_MOBILE_TOKEN=replacement)
        self.assertEqual(self.device_client.post(self.device_path(device, "device/heartbeat/"), {}, format="json").status_code, 200)
        get_user_model().objects.filter(pk=self.row.owner_id).update(is_active=False)
        # The personal host rejects every request when its durable owner is
        # disabled, before the endpoint's dedicated token authentication.
        self.assertEqual(self.device_client.post(self.device_path(device, "device/heartbeat/"), {}, format="json").status_code, 503)

    @override_settings(NEXUS_PERSONAL_AGENT_LIMITS={"agents.mobile_devices": 1})
    def test_real_capacity_and_project_scope_do_not_create_orphans(self):
        project = Project.objects.create(tenant=self.row.tenant, name="Other")
        response = self.client.post("/api/v1/mobile-devices/", {"name": "Denied", "project_id": str(project.pk)}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(MobileDevice.objects.count(), 0)
        device, _ = self.create_device()
        response = self.client.post("/api/v1/mobile-devices/", {"name": "Extra"}, format="json")
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(capability_state(tenant=self.row.tenant, code="agents.mobile_devices")["used"], 1)
        self.assertEqual(self.client.delete(self.device_path(device)).status_code, 200)
        self.create_device()

    def test_device_context_isolation_even_with_valid_token(self):
        device, _ = self.create_device()
        project = Project.objects.create(tenant=self.row.tenant, name="Different")
        MobileDevice.objects.filter(pk=device.pk).update(project=project)
        self.assertEqual(self.client.get("/api/v1/mobile-devices/").data, [])
        self.assertEqual(self.client.get(self.device_path(device)).status_code, 404)
        self.assertEqual(self.device_client.post(self.device_path(device, "device/heartbeat/"), {}, format="json").status_code, 403)
        other = get_user_model().objects.create_user(username="other")
        MobileDevice.objects.filter(pk=device.pk).update(project=self.row.project, created_by=other)
        self.assertEqual(self.client.get("/api/v1/mobile-devices/").data, [])
        self.assertEqual(self.client.get(self.device_path(device)).status_code, 404)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + Token.objects.create(user=other).key)
        self.assertIn(self.client.get(self.device_path(device)).status_code, (401, 403))

    def test_agent_delegate_deduplication_results_and_screen_isolation(self):
        device, _ = self.create_device()
        agent, run, token = self.run_fixture(device)
        payload = {"action": "capture_screen", "client_request_id": str(uuid.uuid4())}
        command = mobile_access.create_mobile_delegate_command(run_id=str(run.pk), token=token, data=payload)
        replay = mobile_access.create_mobile_delegate_command(run_id=str(run.pk), token=token, data=payload)
        self.assertEqual(command["id"], replay["id"])
        with self.assertRaises(mobile_access.AgentMobileCommandIdempotencyConflict):
            mobile_access.create_mobile_delegate_command(run_id=str(run.pk), token=token, data={**payload, "action": "observe"})
        self.assertEqual(self.next_command(device)["id"], command["id"])
        self.result(command["id"], {"screenshot_base64": base64.b64encode(PNG).decode(), "content_type": "image/png"})
        result = mobile_access.mobile_delegate_command(run_id=str(run.pk), command_id=command["id"], token=token)
        self.assertEqual(base64.b64decode(result["result"]["screenshot_base64"]), PNG)
        self.assertEqual(self.client.get(self.device_path(device, "screenshot/")).status_code, 404)
        with self.assertRaises(mobile_access.MobileNotFound):
            mobile_access.mobile_delegate_command(run_id=str(run.pk), command_id=command["id"], token="wrong")
        mobile_access.close_mobile_run(run=run)
        self.assertFalse(AgentMobileLease.objects.filter(run=run, status="active").exists())

    def test_agent_mobile_policy_reduction_cancels_real_queue_and_releases_lease(self):
        device, _ = self.create_device()
        agent, run, token = self.run_fixture(device)
        command = mobile_access.create_mobile_delegate_command(run_id=str(run.pk), token=token, data={"action": "observe"})
        self.assertTrue(AgentMobileLease.objects.filter(run=run, status="active").exists())
        response = self.client.patch(f"/api/v1/agents/{agent.pk}/",
            {"mobile_requirement": "disabled", "mobile_capabilities": []}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(MobileCommand.objects.get(pk=command["id"]).status, "canceled")
        self.assertFalse(AgentMobileLease.objects.filter(run=run, status="active").exists())
        with self.assertRaises(mobile_access.MobileNotFound):
            mobile_access.mobile_delegate_status(run_id=str(run.pk), token=token)

    def test_delegate_scope_expiry_and_deleted_device_are_not_bypassed(self):
        device, _ = self.create_device()
        agent, run, token = self.run_fixture(device)
        with self.assertRaises(mobile_access.AgentMobilePermissionRequired):
            mobile_access.create_mobile_delegate_command(run_id=str(run.pk), token=token,
                data={"action": "open_app", "arguments": {"package": "test.app"}})
        run.mobile_delegate_token_expires_at = timezone.now() - timedelta(seconds=1)
        run.save()
        with self.assertRaises(mobile_access.MobileNotFound):
            mobile_access.mobile_delegate_status(run_id=str(run.pk), token=token)
        self.assertEqual(self.client.delete(self.device_path(device)).status_code, 200)
        run.mobile_delegate_token_expires_at = timezone.now() + timedelta(minutes=5)
        run.save()
        with self.assertRaises(mobile_access.MobileNotFound):
            mobile_access.mobile_delegate_status(run_id=str(run.pk), token=token)

    def test_workspace_grant_still_enforces_declared_scopes_without_private_imports(self):
        agent = Agent.objects.create(tenant=self.row.tenant, project=self.row.project,
            name="Workspace-agent", created_by=self.row.owner, workspace_capabilities=["files.read"])
        request = self.request()
        grant = workspace_grants.set_workspace_grant(request=request, agent=agent, scopes=["files.read"])
        self.assertEqual(str(grant.project_id), str(self.row.project_id))
        self.assertEqual(workspace_grants.effective_workspace_capabilities(request=request, agent=agent), ["files.read"])
        with self.assertRaises(exceptions.ValidationError):
            workspace_grants.set_workspace_grant(request=request, agent=agent, scopes=["command.execute"])
