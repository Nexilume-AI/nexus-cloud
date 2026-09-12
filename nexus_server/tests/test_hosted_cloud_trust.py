import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import ValidationError

from apps.agents.cloud_trust import hosted_cloud_trust
from apps.agents import runtime_runner as runner


@override_settings(NEXUS_PUBLIC_BASE_URL="https://cloud.test:443/",
                   NEXUS_AGENT_DISPLAY_EVENTS_BASE_URL="https://cloud.test",
                   NEXUS_AGENT_WORKSPACE_API_BASE_URL="https://cloud.test",
                   NEXUS_AGENT_RUNTIME_CLOUD_CA_FILE="")
class HostedCloudTrustTests(SimpleTestCase):
    def test_system_ca_is_normalized_and_not_exported(self):
        self.assertEqual(hosted_cloud_trust(), {"schema_version": 1, "mode": "system", "origins": ["https://cloud.test"]})

    def test_valid_ca_and_digest_with_no_private_key(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        from datetime import datetime, timedelta, timezone
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(1).not_valid_before(datetime.now(timezone.utc)).not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
        pem = cert.public_bytes(serialization.Encoding.PEM)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ca.pem"
            path.write_bytes(pem)
            with override_settings(NEXUS_AGENT_RUNTIME_CLOUD_CA_FILE=str(path)):
                policy = hosted_cloud_trust()
                self.assertEqual(policy["sha256"], hashlib.sha256(pem).hexdigest())
                self.assertEqual(policy["ca_pem"], pem.decode())
                for bad in (b"private material", pem + b"-----BEGIN PRIVATE KEY-----", b" " * 16385):
                    path.write_bytes(bad)
                    with self.assertRaises(ValidationError) as error:
                        hosted_cloud_trust()
                    self.assertNotIn("private material", str(error.exception))

    def test_missing_ca_invalid_origin_and_mixed_http_fail_closed(self):
        for options in ({"NEXUS_AGENT_RUNTIME_CLOUD_CA_FILE": "missing.pem"},
                        {"NEXUS_PUBLIC_BASE_URL": "https://user:password@cloud.test"},
                        {"NEXUS_AGENT_RUNTIME_CLOUD_CA_FILE": "missing.pem", "NEXUS_PUBLIC_BASE_URL": "http://cloud.test"}):
            with override_settings(**options), self.assertRaises(ValidationError):
                hosted_cloud_trust()

    def test_ca_is_passed_by_environment_not_command_line_and_survives_controller_frame(self):
        from dataclasses import asdict
        policy = hosted_cloud_trust()
        context = runner.RuntimeDisplayContext(run_id="deployment", events_url="https://cloud.test/events", write_token="test-token", cloud_trust=policy)
        restored = runner.RuntimeDisplayContext(**json.loads(json.dumps(asdict(context))))
        deployment = SimpleNamespace(id="runtime1", tenant_id="tenant", project_id=None, agent_id="agent",
            image=SimpleNamespace(version=None, image_ref="example/agent:tag", image_digest="sha256:" + "a" * 64),
            agent=SimpleNamespace(resource_config=None))
        with patch.object(runner, "_docker_inspect", return_value=None), patch.object(runner, "_run_docker", return_value="container") as docker, \
             patch.object(runner, "_docker_host_port", return_value=49001), patch.object(runner, "_wait_for_mcp", return_value=True):
            runner.DockerAgentRuntimeRunner().start(deployment=deployment, display_context=restored)
        call = next(c for c in docker.call_args_list if c.args[0][1] == "run")
        self.assertIn("NEXUS_HOSTED_CLOUD_TRUST", call.args[0])
        self.assertNotIn("cloud.test", " ".join(call.args[0]))
        self.assertEqual(json.loads(call.kwargs["environment"]["NEXUS_HOSTED_CLOUD_TRUST"]), policy)
