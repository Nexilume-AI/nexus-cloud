"""Portable signed Computer protocol/terminal guards, without financial admission fixtures."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import tempfile
from urllib.parse import parse_qs, urlsplit

from asgiref.sync import async_to_sync
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID
from django.core.files.storage import default_storage
from django.test import override_settings
from apps.workspaces.computer_runtime import (
    ComputerRuntimeError, apply_runtime_frame, cancel_runtime_command, claim_cancel_frames,
    claim_pending_commands, consume_runtime_upload, consume_connect_ticket,
    enqueue_runtime_command, wait_for_runtime_command,
)
from apps.workspaces.computer_runtime_asgi import RuntimeTerminalStreamChannel, _RuntimeSocketState
from apps.workspaces.models import ComputerRuntimeCommand, ComputerRuntimeDevice, WorkspaceConnection


def _test_ca_pem() -> bytes:
    key = Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Nexus Computer Runtime Test CA")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, algorithm=None)
    )
    return certificate.public_bytes(serialization.Encoding.PEM)


class ComputerRuntimeProtocolGuards:
    def pair(self):
        pairing = self.runtime_request("post",
            "/api/v1/computers/pairing-codes/",
            {"name": "Caller Laptop", "workspace_root": "~/.nexus"},
            format="json",
            **self.headers,
        )
        self.assertEqual(pairing.status_code, 201, pairing.content)
        pairing_data = self.runtime_payload(pairing)
        code = parse_qs(urlsplit(pairing_data["pairing_url"]).query)["code"][0]
        private_key = Ed25519PrivateKey.generate()
        public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
        enrolled = self.runtime_request("post",
            "/api/v1/computer-runtime/v1/enroll/",
            {
                "pairing_code": code,
                "public_key_pem": public_pem,
                "name": "Caller Laptop",
                "platform": "linux",
                "protocol_version": 1,
                "capabilities": {
                    "workspace.v1": 1,
                    "terminal.v1": 1,
                    "browser.v1": 1,
                    "tool_setup.v1": 1,
                },
                "facts": {"browser_available": True, "browser_name": "Chromium"},
            },
            format="json",
        )
        self.assertEqual(enrolled.status_code, 201, enrolled.content)
        return pairing_data, self.runtime_payload(enrolled), code, private_key

    def session(self, device_id: str, private_key: Ed25519PrivateKey):
        challenge_response = self.runtime_request("post",
            "/api/v1/computer-runtime/v1/sessions/",
            {"device_id": device_id},
            format="json",
        )
        self.assertEqual(challenge_response.status_code, 200, challenge_response.content)
        challenge = self.runtime_payload(challenge_response)["challenge"]
        signature = private_key.sign(
            f"nexus-computer-session-v1\n{device_id}\n{challenge}".encode("utf-8")
        )
        response = self.runtime_request("post",
            "/api/v1/computer-runtime/v1/sessions/",
            {
                "device_id": device_id,
                "challenge": challenge,
                "signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        return self.runtime_payload(response)

    def test_pairing_is_single_use_and_session_ticket_is_signed_and_single_use(self) -> None:
        pairing, enrolled, pairing_code, private_key = self.pair()
        self.assertNotIn(pairing_code, str(WorkspaceConnection.objects.get(id=pairing["connection_id"]).metadata))

        replay = self.runtime_request("post",
            "/api/v1/computer-runtime/v1/enroll/",
            {
                "pairing_code": pairing_code,
                "public_key_pem": private_key.public_key().public_bytes(
                    serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                ).decode("ascii"),
                "platform": "linux",
                "capabilities": {"workspace.v1": 1},
            },
            format="json",
        )
        self.assertEqual(replay.status_code, 400, replay.content)

        session = self.session(enrolled["device_id"], private_key)
        self.assertEqual(session["websocket_url"], "wss://cloud.example.test/ws/computer-runtime/v1/connect/")
        device = consume_connect_ticket(token=session["ticket"])
        self.assertEqual(device.generation, 1)
        with self.assertRaises(ComputerRuntimeError):
            consume_connect_ticket(token=session["ticket"])

    def test_pairing_delivers_private_cloud_ca_without_user_download(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ca_pem = _test_ca_pem()
            ca_path = Path(directory) / "cloud-ca.pem"
            ca_path.write_bytes(ca_pem)
            with override_settings(NEXUS_COMPUTER_RUNTIME_CA_FILE=str(ca_path)):
                response = self.runtime_request("post",
                    "/api/v1/computers/pairing-codes/",
                    {"name": "Automatic Trust Computer", "workspace_root": "~/.nexus"},
                    format="json",
                    **self.headers,
                )

        self.assertEqual(response.status_code, 201, response.content)
        data = self.runtime_payload(response)
        query = parse_qs(urlsplit(data["pairing_url"]).query)
        self.assertEqual(query["trust"], ["pinned-pem"])
        delivered = base64.urlsafe_b64decode(query["ca"][0] + "=" * (-len(query["ca"][0]) % 4))
        self.assertEqual(delivered, ca_pem)
        self.assertEqual(query["ca_sha256"], [hashlib.sha256(ca_pem).hexdigest()])
        self.assertEqual(data["setup_command"], f'nexus-computer setup "{data["pairing_url"]}"')
        self.assertNotIn("--ca-file", data["setup_command"])

    def test_durable_command_is_claimed_acknowledged_and_completed(self) -> None:
        _pairing, enrolled, _code, private_key = self.pair()
        session = self.session(enrolled["device_id"], private_key)
        device = consume_connect_ticket(token=session["ticket"])
        apply_runtime_frame(
            device_id=str(device.id),
            generation=device.generation,
            frame={
                "version": 1,
                "sequence": 1,
                "type": "hello",
                "capabilities": {"workspace.v1": 1, "terminal.v1": 1},
                "facts": {"os": "Linux"},
            },
        )
        command = enqueue_runtime_command(
            connection=device.connection,
            operation="workspace.test",
            required_scope="connection.list",
            payload={"path": "/home/caller/private.txt", "workspace_root": "/home/caller"},
            timeout_seconds=10,
            idempotency_key="runtime-test-1",
        )
        self.assertNotIn("caller", str(command.payload_summary))
        self.assertEqual(command.payload_summary["path_kind"], "absolute")
        frames = claim_pending_commands(device_id=str(device.id))
        self.assertEqual([frame["command_id"] for frame in frames], [str(command.id)])
        upload = self.runtime_request("put",
            frames[0]["upload"]["endpoint"],
            data=b"runtime-image-bytes",
            content_type="image/jpeg",
            HTTP_AUTHORIZATION=f"Bearer {frames[0]['upload']['token']}",
        )
        self.assertEqual(upload.status_code, 201, upload.content)
        upload_reference = {
            "command_id": str(command.id),
            "upload_id": self.runtime_payload(upload)["upload_id"],
        }
        apply_runtime_frame(
            device_id=str(device.id),
            generation=device.generation,
            frame={"version": 1, "sequence": 2, "type": "command_ack", "command_id": str(command.id)},
        )
        apply_runtime_frame(
            device_id=str(device.id),
            generation=device.generation,
            frame={
                "version": 1,
                "sequence": 3,
                "type": "result",
                "command_id": str(command.id),
                "result": {"facts": {"os": "Linux"}},
            },
        )
        command.refresh_from_db()
        self.assertNotIn("Linux", str(command.result))
        self.assertNotIn("Linux", command.encrypted_result)
        self.assertEqual(wait_for_runtime_command(command, timeout_seconds=1)["facts"]["os"], "Linux")
        uploaded, content_type = consume_runtime_upload(
            reference=upload_reference,
            connection=device.connection,
            maximum_bytes=1024,
        )
        self.assertEqual(uploaded, b"runtime-image-bytes")
        self.assertEqual(content_type, "image/jpeg")
        self.assertEqual(
            ComputerRuntimeCommand.objects.get(id=command.id).status,
            ComputerRuntimeCommand.STATUS_SUCCEEDED,
        )

    def test_terminal_stream_frames_bypass_durable_command_queue(self) -> None:
        async def scenario() -> None:
            state = _RuntimeSocketState(
                device_id="00000000-0000-0000-0000-000000000001",
                generation=1,
                loop=asyncio.get_running_loop(),
            )
            stream = state.create_stream()
            frame = {
                "version": 1,
                "sequence": 1,
                "type": "terminal_stream_output",
                "stream_id": stream.stream_id,
                "data": "nexus-stream-ok",
            }
            self.assertTrue(state.accept_sequence(frame))
            state.deliver(frame)
            self.assertEqual(stream.messages.get_nowait()["data"], "nexus-stream-ok")
            self.assertFalse(state.accept_sequence(frame))

        import asyncio

        async_to_sync(scenario)()
        self.assertEqual(ComputerRuntimeCommand.objects.count(), 0)

    def test_runtime_terminal_session_creation_does_not_wait_for_tool_discovery(self) -> None:
        pairing, _enrolled, _code, _private_key = self.pair()

        response = self.runtime_request("post",
            "/api/v1/workspace-terminal-sessions/",
            {
                "connection_id": pairing["connection_id"],
                "shell": "auto",
                "cols": 120,
                "rows": 34,
            },
            format="json",
            **self.headers,
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(ComputerRuntimeCommand.objects.count(), 0)
        self.assertNotIn("tool_status", self.runtime_payload(response)["metadata"])

    def test_terminal_stream_channel_moves_input_and_output_without_database_commands(self) -> None:
        async def scenario() -> None:
            state = _RuntimeSocketState(
                device_id="00000000-0000-0000-0000-000000000001",
                generation=1,
                loop=asyncio.get_running_loop(),
            )
            stream = state.create_stream()
            channel = RuntimeTerminalStreamChannel(socket=state, stream=stream)

            write = asyncio.create_task(asyncio.to_thread(channel.write, "ls\n"))
            input_frame = await asyncio.wait_for(state.outgoing.get(), timeout=1)
            await write
            self.assertEqual(input_frame["type"], "terminal_stream_input")
            self.assertEqual(input_frame["data"], "ls\n")

            state.deliver({
                "type": "terminal_stream_output",
                "stream_id": stream.stream_id,
                "data": "file.txt\r\n",
            })
            self.assertEqual(await asyncio.to_thread(channel.read, 0.1), "file.txt\r\n")

        import asyncio

        async_to_sync(scenario)()
        self.assertEqual(ComputerRuntimeCommand.objects.count(), 0)

    def test_revoke_cancels_commands_and_discards_unconsumed_uploads(self) -> None:
        pairing, enrolled, _code, private_key = self.pair()
        session = self.session(enrolled["device_id"], private_key)
        device = consume_connect_ticket(token=session["ticket"])
        apply_runtime_frame(
            device_id=str(device.id),
            generation=device.generation,
            frame={"version": 1, "sequence": 1, "type": "hello", "capabilities": {"browser.v1": 1}},
        )
        command = enqueue_runtime_command(
            connection=device.connection,
            operation="browser.observe",
            required_scope="browser.control",
            payload={"browser_session_id": "browser-revoke-test"},
            timeout_seconds=30,
        )
        frame = claim_pending_commands(device_id=str(device.id))[0]
        upload = self.runtime_request("put",
            frame["upload"]["endpoint"],
            data=b"temporary-runtime-upload",
            content_type="image/jpeg",
            HTTP_AUTHORIZATION=f"Bearer {frame['upload']['token']}",
        )
        self.assertEqual(upload.status_code, 201, upload.content)
        command.refresh_from_db()
        storage_name = next(iter(command.result["uploads"].values()))["storage_name"]
        self.assertTrue(default_storage.exists(storage_name))

        revoke = self.runtime_request("post",
            f"/api/v1/computers/{pairing['connection_id']}/revoke/",
            format="json",
            **self.headers,
        )
        self.assertEqual(revoke.status_code, 200, revoke.content)
        command.refresh_from_db()
        self.assertEqual(command.status, ComputerRuntimeCommand.STATUS_CANCELED)
        self.assertEqual(command.result.get("uploads"), {})
        self.assertFalse(default_storage.exists(storage_name))

    def test_repeatedly_paired_revoked_computer_can_be_deleted(self) -> None:
        for _attempt in range(2):
            pairing, _enrolled, _code, _private_key = self.pair()
            revoked = self.runtime_request("post",
                f"/api/v1/computers/{pairing['connection_id']}/revoke/",
                format="json",
                **self.headers,
            )
            self.assertEqual(revoked.status_code, 200, revoked.content)
            deleted = self.runtime_request("delete",
                f"/api/v1/workspace-connections/{pairing['connection_id']}/",
                format="json",
                **self.headers,
            )
            self.assertEqual(deleted.status_code, 200, deleted.content)

        self.assertEqual(
            WorkspaceConnection.objects.filter(name="Caller Laptop", status="deleted").count(),
            2,
        )

    def test_device_signed_unpair_revokes_runtime(self) -> None:
        _pairing, enrolled, _code, private_key = self.pair()
        session = self.session(enrolled["device_id"], private_key)
        response = self.runtime_request("post",
            "/api/v1/computer-runtime/v1/unpair/",
            {"ticket": session["ticket"]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        device = ComputerRuntimeDevice.objects.get(id=enrolled["device_id"])
        self.assertIsNotNone(device.revoked_at)
        challenge = self.runtime_request("post",
            "/api/v1/computer-runtime/v1/sessions/",
            {"device_id": enrolled["device_id"]},
            format="json",
        )
        self.assertEqual(challenge.status_code, 404, challenge.content)

    def test_canceled_command_is_delivered_and_acknowledged(self) -> None:
        _pairing, enrolled, _code, private_key = self.pair()
        session = self.session(enrolled["device_id"], private_key)
        device = consume_connect_ticket(token=session["ticket"])
        apply_runtime_frame(
            device_id=str(device.id),
            generation=device.generation,
            frame={
                "version": 1,
                "sequence": 1,
                "type": "hello",
                "capabilities": {"terminal.v1": 1},
                "facts": {"os": "Linux"},
            },
        )
        command = enqueue_runtime_command(
            connection=device.connection,
            operation="command.execute",
            required_scope="command.execute",
            payload={"command": "sleep 30"},
            timeout_seconds=60,
        )
        self.assertEqual(len(claim_pending_commands(device_id=str(device.id))), 1)
        apply_runtime_frame(
            device_id=str(device.id),
            generation=device.generation,
            frame={"version": 1, "sequence": 2, "type": "command_ack", "command_id": str(command.id)},
        )
        cancel_runtime_command(command=command)
        frames = claim_cancel_frames(device_id=str(device.id))
        self.assertEqual(frames[0]["type"], "cancel")
        self.assertEqual(frames[0]["command_id"], str(command.id))
        apply_runtime_frame(
            device_id=str(device.id),
            generation=device.generation,
            frame={"version": 1, "sequence": 3, "type": "cancel_ack", "command_id": str(command.id)},
        )
        command.refresh_from_db()
        self.assertTrue(command.result["cancel_control"]["acknowledged"])
        self.assertEqual(claim_cancel_frames(device_id=str(device.id)), [])
        apply_runtime_frame(
            device_id=str(device.id),
            generation=device.generation,
            frame={"version": 1, "sequence": 4, "type": "result", "command_id": str(command.id), "result": {"late": True}},
        )
        command.refresh_from_db()
        self.assertEqual(command.status, ComputerRuntimeCommand.STATUS_CANCELED)
