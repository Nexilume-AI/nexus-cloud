"""Real signed enrollment/HTTP and durable queue, NOT an attached hardware E2E.

The test submits Runtime protocol frames; no fake runner executes user commands.
Actual WSS/SDK/local process execution remains a separate release requirement.
"""
import base64
from datetime import timedelta
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from apps.common.crypto import decrypt_secret
from apps.common.resource_limits import capability_state
from apps.tenancy.models import Project
from apps.workspaces import computer_runtime as runtime
from apps.workspaces.models import ComputerRuntimeDevice, ComputerRuntimeCommand, WorkspaceConnection
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


@override_settings(NEXUS_PUBLIC_BASE_URL="https://personal.example")
class PersonalComputerRuntimeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="computer-owner@example.test", password=PASSWORD)
        cls.owner_token = Token.objects.create(user=cls.row.owner)

    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.owner_token.key)
        self.peer = APIClient()
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        storage = override_settings(MEDIA_ROOT=self.folder.name)
        storage.enable()
        self.addCleanup(storage.disable)

    def pair(self):
        response = self.client.post("/api/v1/computers/pairing-codes/", {"name": "Local Computer"}, format="json")
        self.assertEqual(response.status_code, 201)
        params = parse_qs(urlsplit(response.data["pairing_url"]).query)
        self.assertEqual(params["cloud"], ["https://personal.example"])
        key = Ed25519PrivateKey.generate()
        body = {"pairing_code": params["code"][0], "platform": "linux",
            "capabilities": {"workspace.v1": 1, "terminal.v1": 1, "browser.v1": 1},
            "public_key_pem": key.public_key().public_bytes(serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo).decode()}
        enrolled = self.peer.post("/api/v1/computer-runtime/v1/enroll/", body, format="json")
        self.assertEqual(enrolled.status_code, 201, enrolled.data)
        self.assertEqual(self.peer.post("/api/v1/computer-runtime/v1/enroll/", body, format="json").status_code, 400)
        device = ComputerRuntimeDevice.objects.select_related("connection").get(pk=enrolled.data["device_id"])
        self.assertEqual(device.connection.project_id, self.row.project_id)
        self.assertEqual(device.connection.encrypted_password, "")
        self.assertEqual(device.connection.encrypted_private_key, "")
        return device, key

    def ticket(self, device, key):
        response = self.peer.post("/api/v1/computer-runtime/v1/sessions/", {"device_id": str(device.pk)}, format="json")
        self.assertEqual(response.status_code, 200)
        challenge = response.data["challenge"]
        signature = base64.urlsafe_b64encode(key.sign(f"nexus-computer-session-v1\n{device.pk}\n{challenge}".encode())).decode()
        body = {"device_id": str(device.pk), "challenge": challenge, "signature": signature}
        response = self.peer.post("/api/v1/computer-runtime/v1/sessions/", body, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["websocket_url"], "wss://personal.example/ws/computer-runtime/v1/connect/")
        self.assertEqual(self.peer.post("/api/v1/computer-runtime/v1/sessions/", body, format="json").status_code, 400)
        return response.data["ticket"]

    def connect(self, device, key):
        ticket = self.ticket(device, key)
        current = runtime.consume_connect_ticket(token=ticket)
        with self.assertRaises(runtime.ComputerRuntimeError):
            runtime.consume_connect_ticket(token=ticket)
        runtime.apply_runtime_frame(device_id=str(current.pk), generation=current.generation,
            frame={"type": "hello", "sequence": 1, "capabilities": current.capabilities})
        current.refresh_from_db()
        return current

    def command(self, device, **overrides):
        return runtime.enqueue_runtime_command(connection=device.connection,
            operation="workspace.read", required_scope="files.read", payload={"path": "private.txt"},
            **overrides)

    def test_signed_pairing_presence_queue_encryption_and_sequence_deduplication(self):
        device, key = self.pair()
        device = self.connect(device, key)
        workspace_response = self.client.get("/api/v1/workspace-connections/")
        self.assertEqual(workspace_response.status_code, 200, workspace_response.data)
        self.assertEqual([row["id"] for row in workspace_response.data], [str(device.connection_id)])
        response = self.client.get("/api/v1/computers/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data[0]["online"])
        for name in ("public_key_pem", "connect_ticket_hash", "auth_challenge_hash", "encrypted_private_key"):
            self.assertNotIn(name, workspace_response.data[0])
            self.assertNotIn(name, response.data[0])
        command = self.command(device, idempotency_key="read-1")
        self.assertNotIn("private.txt", command.encrypted_payload)
        self.assertNotIn("private.txt", str(command.payload_summary))
        self.assertIn("private.txt", decrypt_secret(command.encrypted_payload))
        self.assertEqual(self.command(device, idempotency_key="read-1").pk, command.pk)
        wire = runtime.claim_pending_commands(device_id=str(device.pk))
        self.assertEqual(len(wire), 1)
        self.assertEqual(wire[0]["payload"], {"path": "private.txt"})
        self.assertEqual(runtime.claim_pending_commands(device_id=str(device.pk)), [])
        for sequence, kind, values in ((2, "command_ack", {}), (3, "result", {"result": {"content": "真实结果"}})):
            frame = {"type": kind, "sequence": sequence, "command_id": str(command.pk), **values}
            runtime.apply_runtime_frame(device_id=str(device.pk), generation=device.generation, frame=frame)
        self.assertEqual(runtime.wait_for_runtime_command(command, timeout_seconds=1), {"content": "真实结果"})
        duplicate = runtime.apply_runtime_frame(device_id=str(device.pk), generation=device.generation, frame=frame)
        self.assertTrue(duplicate["duplicate"])
        command.refresh_from_db()
        self.assertEqual(command.status, "succeeded")
        self.assertNotIn("真实结果", str(command.result))

    def test_wrong_signature_generation_and_revocation_reject_access(self):
        device, key = self.pair()
        challenge = self.peer.post("/api/v1/computer-runtime/v1/sessions/", {"device_id": str(device.pk)}, format="json").data["challenge"]
        response = self.peer.post("/api/v1/computer-runtime/v1/sessions/", {"device_id": str(device.pk),
            "challenge": challenge, "signature": base64.urlsafe_b64encode(b"wrong" * 13).decode()}, format="json")
        self.assertEqual(response.status_code, 400)
        previous = self.connect(device, key)
        current = self.connect(device, key)
        self.assertGreater(current.generation, previous.generation)
        with self.assertRaises(runtime.ComputerRuntimeError):
            runtime.apply_runtime_frame(device_id=str(device.pk), generation=previous.generation,
                frame={"type": "heartbeat", "sequence": 2})
        command = self.command(current)
        response = self.client.post(f"/api/v1/computers/{device.connection_id}/revoke/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        command.refresh_from_db()
        self.assertEqual(command.status, "canceled")
        self.assertFalse(runtime.runtime_generation_is_current(device_id=str(device.pk), generation=current.generation))
        self.assertEqual(self.peer.post("/api/v1/computer-runtime/v1/sessions/", {"device_id": str(device.pk)}, format="json").status_code, 404)

    @override_settings(NEXUS_PERSONAL_AGENT_LIMITS={"agents.computers": 1})
    def test_pairing_capacity_scope_and_revoke_release_real_count(self):
        other = Project.objects.create(tenant=self.row.tenant, name="Other")
        response = self.client.post("/api/v1/computers/pairing-codes/", {"project_id": str(other.pk)}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(WorkspaceConnection.objects.count(), 0)
        device, key = self.pair()
        self.assertEqual(capability_state(tenant=self.row.tenant, code="agents.computers")["used"], 1)
        self.assertEqual(self.client.post("/api/v1/computers/pairing-codes/", {}, format="json").status_code, 409)
        self.assertEqual(self.client.post(f"/api/v1/computers/{device.connection_id}/revoke/", {}, format="json").status_code, 200)
        self.assertEqual(capability_state(tenant=self.row.tenant, code="agents.computers")["used"], 0)
        self.pair()

    def test_context_change_invalidates_device_and_rejects_another_owner(self):
        device, key = self.pair()
        device = self.connect(device, key)
        command = self.command(device)
        wire = runtime.claim_pending_commands(device_id=str(device.pk))[0]
        other = get_user_model().objects.create_user(username="unrelated")
        WorkspaceConnection.objects.filter(pk=device.connection_id).update(created_by=other)
        self.assertEqual(self.client.get("/api/v1/computers/").data, [])
        from apps.workspaces.connection_core import list_workspace_connections
        request = SimpleNamespace(user=self.row.owner, META={}, headers={}, query_params={})
        self.assertEqual(list(list_workspace_connections(request=request)), [])
        self.assertFalse(runtime.runtime_generation_is_current(device_id=str(device.pk), generation=device.generation))
        with self.assertRaises(runtime.ComputerRuntimeError):
            runtime.apply_runtime_frame(device_id=str(device.pk), generation=device.generation, frame={"type": "heartbeat", "sequence": 2})
        with self.assertRaises(runtime.ComputerRuntimeOffline):
            self.command(device)
        self.assertEqual(runtime.claim_pending_commands(device_id=str(device.pk)), [])
        self.peer.credentials(HTTP_AUTHORIZATION="Bearer " + wire["upload"]["token"])
        response = self.peer.put(wire["upload"]["endpoint"], b"denied", content_type="application/octet-stream")
        self.assertEqual(response.status_code, 400, response.data)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + Token.objects.create(user=other).key)
        self.assertIn(self.client.get("/api/v1/computers/").status_code, (401, 403))

    def test_command_bound_upload_actual_bytes_and_deadline_cleanup(self):
        device, key = self.pair()
        device = self.connect(device, key)
        first = self.command(device)
        second = self.command(device)
        wire = runtime.claim_pending_commands(device_id=str(device.pk))
        upload = wire[0]["upload"]
        self.peer.credentials(HTTP_AUTHORIZATION="Bearer " + upload["token"])
        response = self.peer.put(upload["endpoint"], b"file-content", content_type="application/octet-stream")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["size_bytes"], 12)
        wrong = f"/api/v1/computer-runtime/v1/commands/{second.pk}/upload/"
        self.assertEqual(self.peer.put(wrong, b"wrong", content_type="application/octet-stream").status_code, 400)
        ComputerRuntimeCommand.objects.filter(pk=first.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        runtime.claim_pending_commands(device_id=str(device.pk))
        first.refresh_from_db()
        self.assertEqual(first.status, "expired")
        self.assertEqual(first.result.get("uploads", {}), {})
        self.assertEqual(self.peer.put(upload["endpoint"], b"late", content_type="application/octet-stream").status_code, 400)

    @override_settings(NEXUS_PERSONAL_AGENT_LIMITS={"agents.computers": 64})
    def test_maximum_length_pairing_names_preserve_unique_suffixes(self):
        # Exercise real HTTP and DB lookups, bounded even when the old loop repeats.
        for requested in ("x" * 128, "计" * 128):
            names = []
            for ordinal in range(1, 11):
                probes = []

                def bounded_name_probes(execute, sql, params, many, context):
                    if 'FROM "workspaces_workspaceconnection"' in sql and "LIMIT 1" in sql:
                        probes.append(params)
                        if len(probes) == 25:
                            raise AssertionError("Pairing name resolution exceeded its bounded probe budget")
                    return execute(sql, params, many, context)

                with connection.execute_wrapper(bounded_name_probes):
                    response = self.client.post("/api/v1/computers/pairing-codes/", {"name": requested}, format="json")
                self.assertEqual(response.status_code, 201, "Pairing must terminate with a distinct bounded name")
                created = WorkspaceConnection.objects.get(pk=response.data["connection_id"])
                suffix = f" ({ordinal})" if ordinal > 1 else ""
                expected = requested[:128 - len(suffix)] + suffix
                self.assertEqual(created.name, expected)
                self.assertLessEqual(len(created.name), 128)
                self.assertNotIn(created.name, names)
                names.append(created.name)

    def test_oversized_pairing_name_is_rejected_without_creating_a_record(self):
        response = self.client.post("/api/v1/computers/pairing-codes/", {"name": "x" * 129}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WorkspaceConnection.objects.count(), 0)
