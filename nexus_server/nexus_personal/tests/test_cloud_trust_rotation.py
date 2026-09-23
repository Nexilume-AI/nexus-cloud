"""Community-host regression coverage for deployment Cloud trust changes."""
from datetime import timedelta
from unittest.mock import patch
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import APIException
from apps.agents import docker_lifecycle as lifecycle
from apps.agents.runtime_runner import FakeAgentRuntimeRunner
from nexus_personal.tests import test_docker_lifecycle as personal


class PersonalCloudTrustRotationTests(personal.PersonalDockerLifecycleTests):

    @override_settings(NEXUS_PUBLIC_BASE_URL="https://cloud.example", NEXUS_AGENT_DISPLAY_EVENTS_BASE_URL="", NEXUS_AGENT_WORKSPACE_API_BASE_URL="")
    def test_real_ca_rotation_at_same_origin_updates_deployment_policy(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        from pathlib import Path
        from tempfile import TemporaryDirectory
        def certificate():
            key = ec.generate_private_key(ec.SECP256R1())
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Rotation test CA")])
            return (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(timezone.now() - timedelta(minutes=1))
                .not_valid_after(timezone.now() + timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ca.pem"
            path.write_bytes(certificate())
            with override_settings(NEXUS_AGENT_RUNTIME_CLOUD_CA_FILE=str(path)):
                self.assertEqual(self.deploy().status_code, 201)
                previous = self.runtime()
                path.write_bytes(certificate())
                with patch.object(FakeAgentRuntimeRunner, "start", wraps=FakeAgentRuntimeRunner().start) as start:
                    self.assertEqual(lifecycle.reconcile(), 1)
                supplied = start.call_args.kwargs["display_context"].cloud_trust
                self.assertEqual(supplied["ca_pem"], path.read_text())
                self.assertNotEqual(self.runtime().container_id, previous.container_id)
                self.assertEqual(lifecycle.reconcile(), 0)

    @override_settings(NEXUS_AGENT_DISPLAY_EVENTS_BASE_URL="", NEXUS_AGENT_WORKSPACE_API_BASE_URL="")
    def test_rotation_fences_uncommitted_first_deployment_container(self):
        self.assertEqual(self.deploy().status_code, 201)
        runtime = self.runtime()
        runtime.container_id = ""
        runtime.status = "deploying"
        runtime.docker_lifecycle["target_cloud_trust_fingerprint"] = runtime.docker_lifecycle.pop("cloud_trust_fingerprint")
        runtime.save()
        old_name = lifecycle.container_name(runtime)
        with override_settings(NEXUS_PUBLIC_BASE_URL="https://rotated.example"):
            claimed = lifecycle._claim(runtime.pk, desired="running", recovery=True)
        self.assertNotEqual(claimed.docker_lifecycle["generation"], runtime.docker_lifecycle["generation"])
        self.assertEqual(claimed.docker_lifecycle["retiring"][0]["container_id"], old_name)

    @override_settings(NEXUS_AGENT_DISPLAY_EVENTS_BASE_URL="", NEXUS_AGENT_WORKSPACE_API_BASE_URL="")
    def test_cloud_trust_rotation_replaces_healthy_container_without_replaying_calls(self):
        with override_settings(NEXUS_PUBLIC_BASE_URL="https://old.example", NEXUS_AGENT_RUNTIME_CLOUD_CA_FILE=""):
            self.assertEqual(self.deploy().status_code, 201)
        previous = self.runtime()
        with override_settings(NEXUS_PUBLIC_BASE_URL="https://new.example", NEXUS_AGENT_RUNTIME_CLOUD_CA_FILE=""), \
             patch.object(FakeAgentRuntimeRunner, "call_mcp", wraps=FakeAgentRuntimeRunner().call_mcp) as calls:
            self.assertEqual(lifecycle.reconcile(), 1)
            current = self.runtime()
            self.assertEqual(current.pk, previous.pk)
            self.assertNotEqual(current.container_id, previous.container_id)
            self.assertNotEqual(current.docker_lifecycle["generation"], previous.docker_lifecycle["generation"])
            self.assertNotEqual(current.docker_lifecycle["cloud_trust_fingerprint"], previous.docker_lifecycle["cloud_trust_fingerprint"])
            self.assertEqual(lifecycle.reconcile(), 0)
        for call in calls.call_args_list:
            self.assertNotIn(b'"tools/call"', call.kwargs.get("body", b""))

    def test_legacy_deployment_without_trust_fingerprint_is_refreshed_once(self):
        self.assertEqual(self.deploy().status_code, 201)
        runtime = self.runtime()
        runtime.docker_lifecycle.pop("cloud_trust_fingerprint", None)
        runtime.save()
        self.assertEqual(lifecycle.reconcile(), 1)
        self.assertNotEqual(self.runtime().container_id, runtime.container_id)
        self.assertEqual(lifecycle.reconcile(), 0)

    @override_settings(NEXUS_AGENT_DISPLAY_EVENTS_BASE_URL="", NEXUS_AGENT_WORKSPACE_API_BASE_URL="")
    def test_trust_refresh_failure_preserves_container_and_backs_off(self):
        self.assertEqual(self.deploy().status_code, 201)
        previous = self.runtime()
        with override_settings(NEXUS_PUBLIC_BASE_URL="https://rotated.example"), \
             patch.object(FakeAgentRuntimeRunner, "start", side_effect=APIException("cannot start")) as start:
            lifecycle.reconcile()
            current = self.runtime()
            self.assertEqual(current.container_id, previous.container_id)
            self.assertEqual(current.docker_lifecycle["cloud_trust_fingerprint"], previous.docker_lifecycle["cloud_trust_fingerprint"])
            self.assertTrue(lifecycle._future(current.docker_lifecycle.get("retry_at")))
            lifecycle.reconcile()
            self.assertEqual(start.call_count, 1)

    @override_settings(NEXUS_AGENT_DISPLAY_EVENTS_BASE_URL="", NEXUS_AGENT_WORKSPACE_API_BASE_URL="")
    def test_trust_refresh_claim_reuses_generation_after_worker_loss(self):
        self.assertEqual(self.deploy().status_code, 201)
        with override_settings(NEXUS_PUBLIC_BASE_URL="https://rotated.example"):
            first = lifecycle._claim(self.runtime().pk, desired="running", recovery=True)
            first.docker_lifecycle.pop("lease_until", None)
            first.save()
            second = lifecycle._claim(first.pk, desired="running", recovery=True)
            self.assertEqual(first.docker_lifecycle["generation"], second.docker_lifecycle["generation"])

    def test_invalid_trust_never_replaces_existing_container(self):
        self.assertEqual(self.deploy().status_code, 201)
        previous = self.runtime().container_id
        with override_settings(NEXUS_AGENT_RUNTIME_CLOUD_CA_FILE="missing-ca.pem"), \
             patch.object(FakeAgentRuntimeRunner, "start") as start:
            lifecycle.reconcile()
        start.assert_not_called()
        self.assertEqual(self.runtime().container_id, previous)

    @override_settings(NEXUS_AGENT_DISPLAY_EVENTS_BASE_URL="", NEXUS_AGENT_WORKSPACE_API_BASE_URL="")
    def test_trust_rotation_drains_inflight_task_before_retiring_exact_container(self):
        from apps.agents.models import AgentDisplayRun, AgentExecutionTask
        self.assertEqual(self.deploy().status_code, 201)
        previous = self.runtime()
        run = AgentDisplayRun.objects.create(tenant=self.tenant, agent=self.agent, runtime=previous)
        task = AgentExecutionTask.objects.create(run=run, agent=self.agent, runtime=previous,
            caller_subject_hash="caller", tool_name="echo", status="working",
            expires_at=timezone.now() + timedelta(hours=1))
        with override_settings(NEXUS_PUBLIC_BASE_URL="https://rotated.example"), \
             patch.object(FakeAgentRuntimeRunner, "stop") as stop:
            self.assertEqual(lifecycle.reconcile(), 1)
            stop.assert_not_called()
            self.assertEqual(self.runtime().docker_lifecycle["retiring"][0]["container_id"], previous.container_id)
            task.status = "completed"
            task.save()
            lifecycle.retire(runtime_id=previous.pk)
            self.assertEqual(stop.call_args.kwargs["deployment"].container_id, previous.container_id)
            self.assertEqual(self.runtime().docker_lifecycle["retiring"], [])
