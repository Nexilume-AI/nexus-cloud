"""Shared Mobile protocol/HTTP guards; device messages are fixtures, not physical execution."""
from __future__ import annotations
import base64
import json
from datetime import timedelta
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.audit.models import AuditLog
from apps.mobile.models import MobileCommand, MobileDevice


class MobileHTTPGuards:
    def test_create_device_returns_pairing_token_once_and_redacts_list(self) -> None:
        response = self.client.post(
            "/api/v1/mobile-devices/",
            {
                "name": "pixel-test",
                "platform": MobileDevice.PLATFORM_ANDROID,
                "device_identifier": "android-123",
                "capabilities": {"accessibility": True, "screenshot": True},
            },
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 201, response.content)
        payload = self.mobile_payload(response)
        token = payload["pairing_token"]
        self.assertTrue(token.startswith("mnx-mobile-"))
        self.assertEqual(payload["token_prefix"], token[:22])
        self.assertEqual(payload["lifecycle_status"], MobileDevice.LIFECYCLE_AWAITING_PAIRING)
        self.assertEqual(payload["recommended_action"], "continue_pairing")
        self.assertIsNotNone(payload["pairing_expires_at"])
        device = MobileDevice.objects.get(id=payload["id"])
        self.assertNotEqual(device.token_hash, token)

        list_response = self.client.get("/api/v1/mobile-devices/", HTTP_X_NEXUS_TENANT=str(self.tenant.id))
        self.assertEqual(list_response.status_code, 200, list_response.content)
        list_body = list_response.content.decode("utf-8")
        self.assertNotIn("pairing_token", list_body)
        self.assertNotIn(token, list_body)
        self.assertTrue(AuditLog.objects.filter(action="mobile.device.create", resource_id=str(device.id)).exists())

    def test_heartbeat_pairs_device_and_preserves_server_metadata(self) -> None:
        device, token = self._create_device()
        expires_at = device.metadata["pairing_token_expires_at"]

        response = self.client.post(
            f"/api/v1/mobile-devices/{device.id}/device/heartbeat/",
            {
                "current_package": "com.example.mobile",
                "capabilities": {"accessibility": True, "screenshot": True},
                "metadata": {
                    "build": "android-1",
                    "paired_at": "2000-01-01T00:00:00Z",
                    "pairing_token_expires_at": "2000-01-01T00:00:00Z",
                },
            },
            format="json",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )

        self.assertEqual(response.status_code, 200, response.content)
        payload = self.mobile_payload(response)
        self.assertEqual(payload["lifecycle_status"], MobileDevice.LIFECYCLE_ONLINE)
        self.assertEqual(payload["recommended_action"], "open_control")
        device.refresh_from_db()
        self.assertNotEqual(device.metadata["paired_at"], "2000-01-01T00:00:00Z")
        self.assertEqual(device.metadata["pairing_token_expires_at"], expires_at)
        self.assertEqual(device.metadata["build"], "android-1")

    def test_paired_device_requires_accessibility_before_polling_commands(self) -> None:
        device, token = self._create_device()

        heartbeat_response = self.client.post(
            f"/api/v1/mobile-devices/{device.id}/device/heartbeat/",
            {
                "current_package": "com.nexus.mobile",
                "capabilities": {"accessibility": False, "screen_observation": False},
            },
            format="json",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )

        self.assertEqual(heartbeat_response.status_code, 200, heartbeat_response.content)
        payload = self.mobile_payload(heartbeat_response)
        self.assertEqual(payload["lifecycle_status"], MobileDevice.LIFECYCLE_SETUP_REQUIRED)
        self.assertEqual(payload["recommended_action"], "complete_setup")
        self.assertIsNotNone(payload["paired_at"])

        next_response = self.client.post(
            f"/api/v1/mobile-devices/{device.id}/device/commands/next/",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )
        self.assertEqual(next_response.status_code, 409, next_response.content)
        self.assertEqual(next_response.json()["error"]["code"], "MOBILE_DEVICE_NOT_READY")

        self._mark_device_ready(device, token)
        ready_response = self.client.get(
            f"/api/v1/mobile-devices/{device.id}/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(ready_response.status_code, 200, ready_response.content)
        self.assertEqual(self.mobile_payload(ready_response)["lifecycle_status"], MobileDevice.LIFECYCLE_ONLINE)

    def test_expired_unpaired_token_requires_new_pairing_qr(self) -> None:
        device, token = self._create_device()
        device.metadata["pairing_token_expires_at"] = (timezone.now() - timedelta(seconds=1)).isoformat()
        device.save(update_fields=["metadata", "updated_at"])

        response = self.client.post(
            f"/api/v1/mobile-devices/{device.id}/device/heartbeat/",
            {},
            format="json",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )

        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(response.json()["error"]["code"], "MOBILE_PAIRING_TOKEN_EXPIRED")

    def test_high_risk_command_requires_approval_before_device_poll(self) -> None:
        device, token = self._create_device()
        self._mark_device_ready(device, token)

        create_response = self.client.post(
            f"/api/v1/mobile-devices/{device.id}/commands/",
            {"action": MobileCommand.ACTION_TYPE_TEXT, "arguments": {"text": "secret"}},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(create_response.status_code, 201, create_response.content)
        command_id = self.mobile_payload(create_response)["id"]
        self.assertEqual(self.mobile_payload(create_response)["status"], MobileCommand.STATUS_PENDING_APPROVAL)

        next_response = self.client.post(
            f"/api/v1/mobile-devices/{device.id}/device/commands/next/",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )
        self.assertEqual(next_response.status_code, 200, next_response.content)
        self.assertIsNone(self.mobile_payload(next_response)["command"])

        approve_response = self.client.post(
            f"/api/v1/mobile-commands/{command_id}/approve/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(approve_response.status_code, 200, approve_response.content)
        self.assertEqual(self.mobile_payload(approve_response)["status"], MobileCommand.STATUS_QUEUED)

        next_response = self.client.post(
            f"/api/v1/mobile-devices/{device.id}/device/commands/next/",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )
        self.assertEqual(next_response.status_code, 200, next_response.content)
        self.assertEqual(self.mobile_payload(next_response)["id"], command_id)
        self.assertEqual(self.mobile_payload(next_response)["status"], MobileCommand.STATUS_RUNNING)

        result_response = self.client.post(
            f"/api/v1/mobile-commands/{command_id}/device/result/",
            {"status": MobileCommand.STATUS_SUCCEEDED, "result": {"typed": True}},
            format="json",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )
        self.assertEqual(result_response.status_code, 200, result_response.content)
        self.assertEqual(self.mobile_payload(result_response)["status"], MobileCommand.STATUS_SUCCEEDED)
        self.assertTrue(self.mobile_payload(result_response)["result"]["typed"])

    def test_client_cannot_downgrade_server_inferred_command_risk(self) -> None:
        device, _token = self._create_device()

        response = self.client.post(
            f"/api/v1/mobile-devices/{device.id}/commands/",
            {
                "action": MobileCommand.ACTION_TYPE_TEXT,
                "arguments": {"text": "sensitive value"},
                "risk_level": MobileCommand.RISK_LOW,
                "requires_approval": False,
            },
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 201, response.content)
        payload = self.mobile_payload(response)
        self.assertEqual(payload["risk_level"], MobileCommand.RISK_HIGH)
        self.assertTrue(payload["requires_approval"])
        self.assertEqual(payload["status"], MobileCommand.STATUS_PENDING_APPROVAL)

    def test_command_arguments_are_validated_before_queueing(self) -> None:
        device, _token = self._create_device(approval_mode=MobileDevice.APPROVAL_AUTO)

        missing_text = self.client.post(
            f"/api/v1/mobile-devices/{device.id}/commands/",
            {"action": MobileCommand.ACTION_TYPE_TEXT, "arguments": {}},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        invalid_coordinate = self.client.post(
            f"/api/v1/mobile-devices/{device.id}/commands/",
            {
                "action": MobileCommand.ACTION_TAP_COORDINATES,
                "arguments": {"x": 1.2, "y": 0.5},
            },
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(missing_text.status_code, 400, missing_text.content)
        self.assertEqual(invalid_coordinate.status_code, 400, invalid_coordinate.content)
        self.assertEqual(MobileCommand.objects.filter(device=device).count(), 0)

    def test_mobile_mcp_tool_call_creates_command(self) -> None:
        device, _token = self._create_device(approval_mode=MobileDevice.APPROVAL_AUTO)
        request_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "mobile_tap_text", "arguments": {"text": "Settings"}},
        }

        response = self.client.post(
            f"/api/v1/mobile-devices/{device.id}/mcp/",
            data=json.dumps(request_body),
            content_type="application/json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 200, response.content)
        payload = json.loads(response.content.decode("utf-8"))
        self.assertEqual(payload["id"], 1)
        self.assertEqual(payload["result"]["structuredContent"]["status"], MobileCommand.STATUS_QUEUED)
        command = MobileCommand.objects.get(id=payload["result"]["structuredContent"]["command_id"])
        self.assertEqual(command.action, MobileCommand.ACTION_TAP_TEXT)
        self.assertEqual(command.arguments["text"], "Settings")

    def test_delete_mobile_command_unblocks_device_delete(self) -> None:
        device, _token = self._create_device(approval_mode=MobileDevice.APPROVAL_AUTO)
        create_response = self.client.post(
            f"/api/v1/mobile-devices/{device.id}/commands/",
            {"action": MobileCommand.ACTION_OPEN_APP, "arguments": {"package": "com.android.settings"}},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(create_response.status_code, 201, create_response.content)
        command_id = self.mobile_payload(create_response)["id"]
        self.assertEqual(self.mobile_payload(create_response)["status"], MobileCommand.STATUS_QUEUED)

        delete_command_response = self.client.delete(
            f"/api/v1/mobile-commands/{command_id}/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(delete_command_response.status_code, 200, delete_command_response.content)
        self.assertEqual(self.mobile_payload(delete_command_response)["status"], MobileCommand.STATUS_DELETED)
        self.assertFalse(
            MobileCommand.objects.exclude(status=MobileCommand.STATUS_DELETED).filter(id=command_id).exists()
        )

        list_response = self.client.get(
            f"/api/v1/mobile-devices/{device.id}/commands/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(list_response.status_code, 200, list_response.content)
        self.assertEqual(self.mobile_payload(list_response), [])

        device_delete_response = self.client.delete(
            f"/api/v1/mobile-devices/{device.id}/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(device_delete_response.status_code, 200, device_delete_response.content)
        self.assertTrue(AuditLog.objects.filter(action="mobile.command.delete", resource_id=command_id).exists())

    def test_deleting_device_cancels_queued_commands_and_rejects_device_token(self):
        device, token = self._create_device(approval_mode=MobileDevice.APPROVAL_AUTO)
        command = MobileCommand.objects.create(
            tenant=self.tenant, device=device, action=MobileCommand.ACTION_OPEN_APP,
            arguments={"package": "com.nexus.mobile"}, status=MobileCommand.STATUS_QUEUED,
        )
        removed = self.client.delete(
            f"/api/v1/mobile-devices/{device.id}/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(removed.status_code, 200)
        command.refresh_from_db()
        self.assertEqual(command.status, MobileCommand.STATUS_CANCELED)
        polled = APIClient().post(
            f"/api/v1/mobile-devices/{device.id}/device/commands/next/",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )
        self.assertIn(polled.status_code, (403, 404))

    @override_settings(NEXUS_MOBILE_SCREENSHOT_TTL_SECONDS=30)
    def test_screen_capture_is_validated_served_without_cache_and_expires(self) -> None:
        device, token = self._create_device(approval_mode=MobileDevice.APPROVAL_AUTO)
        self._mark_device_ready(device, token)
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )

        create_response = self.client.post(
            f"/api/v1/mobile-devices/{device.id}/commands/",
            {"action": MobileCommand.ACTION_CAPTURE_SCREEN, "arguments": {}},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(create_response.status_code, 201, create_response.content)
        command_id = self.mobile_payload(create_response)["id"]
        self.assertEqual(self.mobile_payload(create_response)["risk_level"], MobileCommand.RISK_MEDIUM)

        poll_response = self.client.post(
            f"/api/v1/mobile-devices/{device.id}/device/commands/next/",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )
        self.assertEqual(poll_response.status_code, 200, poll_response.content)
        self.assertEqual(self.mobile_payload(poll_response)["action"], MobileCommand.ACTION_CAPTURE_SCREEN)

        complete_response = self.client.post(
            f"/api/v1/mobile-commands/{command_id}/device/result/",
            {
                "status": MobileCommand.STATUS_SUCCEEDED,
                "result": {
                    "screenshot_base64": base64.b64encode(png).decode("ascii"),
                    "content_type": "image/png",
                    "width": 1,
                    "height": 1,
                },
            },
            format="json",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )
        self.assertEqual(complete_response.status_code, 200, complete_response.content)
        command_payload = self.mobile_payload(complete_response)
        self.assertEqual(command_payload["result"]["captured"], True)
        self.assertNotIn("screenshot_base64", command_payload["result"])
        self.assertNotIn(base64.b64encode(png).decode("ascii"), complete_response.content.decode("utf-8"))

        detail_response = self.client.get(
            f"/api/v1/mobile-devices/{device.id}/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertTrue(self.mobile_payload(detail_response)["screenshot_available"])
        screenshot_response = self.client.get(
            f"/api/v1/mobile-devices/{device.id}/screenshot/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
            HTTP_ACCEPT="*/*",
        )
        self.assertEqual(screenshot_response.status_code, 200, screenshot_response.content)
        self.assertEqual(screenshot_response.content, png)
        self.assertEqual(screenshot_response["Content-Type"], "image/png")
        self.assertIn("no-store", screenshot_response["Cache-Control"])
        self.assertEqual(screenshot_response["X-Content-Type-Options"], "nosniff")

        # Knowing a device ID or a device transport token is not caller authority.
        self.assert_other_mobile_caller_denied(device, png)
        token_only = APIClient().get(
            f"/api/v1/mobile-devices/{device.id}/screenshot/",
            HTTP_X_NEXUS_MOBILE_TOKEN=token, HTTP_ACCEPT="*/*",
        )
        self.assertIn(token_only.status_code, (401, 403))
        self.assertNotIn(png, token_only.content)

        device.refresh_from_db()
        device.last_screenshot_captured_at = timezone.now() - timedelta(seconds=31)
        device.save(update_fields=["last_screenshot_captured_at", "updated_at"])
        list_response = self.client.get(
            "/api/v1/mobile-devices/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(list_response.status_code, 200, list_response.content)
        self.assertFalse(self.mobile_payload(list_response)[0]["screenshot_available"])
        device.refresh_from_db()
        self.assertIsNone(device.last_screenshot)
        expired = self.client.get(
            f"/api/v1/mobile-devices/{device.id}/screenshot/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id), HTTP_ACCEPT="*/*",
        )
        self.assertEqual(expired.status_code, 404)

    def test_invalid_screen_capture_payload_fails_without_storing_image(self) -> None:
        device, token = self._create_device(approval_mode=MobileDevice.APPROVAL_AUTO)
        self._mark_device_ready(device, token)
        create_response = self.client.post(
            f"/api/v1/mobile-devices/{device.id}/commands/",
            {"action": MobileCommand.ACTION_CAPTURE_SCREEN, "arguments": {}},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        command_id = self.mobile_payload(create_response)["id"]
        self.client.post(
            f"/api/v1/mobile-devices/{device.id}/device/commands/next/",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )

        complete_response = self.client.post(
            f"/api/v1/mobile-commands/{command_id}/device/result/",
            {
                "status": MobileCommand.STATUS_SUCCEEDED,
                "result": {"screenshot_base64": "not-base64", "content_type": "image/webp"},
            },
            format="json",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )

        self.assertEqual(complete_response.status_code, 200, complete_response.content)
        self.assertEqual(self.mobile_payload(complete_response)["status"], MobileCommand.STATUS_FAILED)
        self.assertIn("SCREEN_CAPTURE_INVALID", self.mobile_payload(complete_response)["error"])
        device.refresh_from_db()
        self.assertIsNone(device.last_screenshot)

    def _create_device(self, approval_mode=MobileDevice.APPROVAL_CONFIRM_HIGH_RISK):
        response = self.client.post(
            "/api/v1/mobile-devices/",
            {"name": f"device-{MobileDevice.objects.count()}", "approval_mode": approval_mode},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(response.status_code, 201, response.content)
        payload = self.mobile_payload(response)
        return MobileDevice.objects.get(id=payload["id"]), payload["pairing_token"]

    def _mark_device_ready(self, device: MobileDevice, token: str) -> None:
        response = self.client.post(
            f"/api/v1/mobile-devices/{device.id}/device/heartbeat/",
            {
                "online_status": MobileDevice.ONLINE_ONLINE,
                "capabilities": {"accessibility": True, "screen_observation": True},
            },
            format="json",
            HTTP_X_NEXUS_MOBILE_TOKEN=token,
        )
        self.assertEqual(response.status_code, 200, response.content)
