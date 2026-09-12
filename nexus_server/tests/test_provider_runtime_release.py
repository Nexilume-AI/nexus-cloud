import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import APIException
import tempfile

from apps.providers import runtime_release as releases
from apps.providers.runtime_runner import CodexProxyRuntimeAdapter, DockerProviderRuntimeRunner


@override_settings(NEXUS_PRODUCTION=False, NEXUS_PROVIDER_RUNTIME_REQUIRE_VERIFIED_RELEASE=False,
                   NEXUS_PROVIDER_RUNTIME_RELEASE_DIR="")
class ProviderReleaseTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.image_id = "sha256:" + "a" * 64
        self.receipt = {
            "schema": 1, "provider": "codex_proxy", "status": "verified", "recipe": releases.RECIPE,
            "image_id": self.image_id, "image_reference": self.image_id,
            "upstream_commit": "b" * 40, "patched_tree": "c" * 40, "patchset_sha256": "d" * 64,
            "gates": sorted(releases.GATES["codex_proxy"]),
            "labels": {"io.nexilume.provider": "codex_proxy", "io.nexilume.recipe": releases.RECIPE,
                       "io.nexilume.patchset": "d" * 64, "io.nexilume.upstream": "b" * 40,
                       "org.opencontainers.image.revision": "e" * 40, "io.nexilume.release": "1.2.3-nexus.1"},
        }

    def write_receipt(self):
        (Path(self.temp.name) / "codex_proxy.json").write_text(json.dumps(self.receipt))

    def test_development_compatibility_and_production_fail_closed(self):
        self.assertIsNone(releases.approved_release("codex_proxy"))
        with override_settings(NEXUS_PRODUCTION=True):
            with self.assertRaisesRegex(APIException, "required"):
                releases.approved_release("codex_proxy")

    def test_valid_receipt(self):
        self.write_receipt()
        with override_settings(NEXUS_PROVIDER_RUNTIME_RELEASE_DIR=self.temp.name):
            self.assertEqual(releases.approved_release("codex_proxy")["image_id"], self.image_id)

    def test_incomplete_foreign_mutable_or_failed_receipt_rejected(self):
        cases = [("gates", []), ("status", "failed"), ("provider", "cliproxyapi"),
                 ("image_id", "upstream:latest"), ("image_reference", "upstream:latest"),
                 ("labels", {}), ("patchset_sha256", "wrong")]
        for key, value in cases:
            with self.subTest(key=key):
                old = self.receipt[key]
                self.receipt[key] = value
                self.write_receipt()
                with override_settings(NEXUS_PROVIDER_RUNTIME_RELEASE_DIR=self.temp.name):
                    with self.assertRaisesRegex(APIException, "invalid"):
                        releases.approved_release("codex_proxy")
                self.receipt[key] = old

    def test_image_identity_and_labels_must_match(self):
        image = {"Id": self.image_id, "Config": {"Labels": self.receipt["labels"].copy()}}
        with patch.object(releases, "_inspect", return_value=image):
            releases.verify_image(self.receipt)
            image["Config"]["Labels"]["io.nexilume.patchset"] = "foreign"
            with self.assertRaisesRegex(APIException, "does not match"):
                releases.verify_image(self.receipt)

    def test_recovery_must_not_implicitly_upgrade(self):
        with patch.object(releases, "_inspect", return_value={"Image": "sha256:" + "f" * 64}):
            with self.assertRaisesRegex(APIException, "Explicitly roll out"):
                releases.verify_existing_container(self.receipt, "container")

    def test_docker_diagnostics_do_not_leak(self):
        result = SimpleNamespace(returncode=1, stdout="secret", stderr="secret")
        with patch.object(releases.subprocess, "run", return_value=result):
            with self.assertRaises(APIException) as caught:
                releases.verify_image(self.receipt)
        self.assertNotIn("secret", str(caught.exception))

    def test_rejected_start_cannot_write_storage_build_or_remove_runtime(self):
        runtime = SimpleNamespace(runtime_type="codex_proxy")
        target = "apps.providers.runtime_runner."
        with patch(target + "approved_release", return_value=self.receipt), \
             patch(target + "verify_image", side_effect=APIException("unapproved")), \
             patch.object(CodexProxyRuntimeAdapter, "prepare_storage") as prepare, \
             patch(target + "build_image") as build, \
             patch(target + "_remove_provider_runtime_containers") as remove:
            with self.assertRaises(APIException):
                DockerProviderRuntimeRunner().start(runtime=runtime, proxy_api_key="fixture")
            prepare.assert_not_called()
            build.assert_not_called()
            remove.assert_not_called()

    def test_rejected_restore_cannot_start_or_restart(self):
        runtime = SimpleNamespace(runtime_type="codex_proxy")
        target = "apps.providers.runtime_runner."
        with patch(target + "approved_release", return_value=self.receipt), \
             patch(target + "verify_image"), \
             patch(target + "_provider_runtime_container_id", return_value="container"), \
             patch(target + "verify_existing_container", side_effect=APIException("mismatch")), \
             patch(target + "_run_docker") as docker:
            with self.assertRaises(APIException):
                DockerProviderRuntimeRunner().restore(runtime=runtime, proxy_api_key="fixture")
            docker.assert_not_called()

    def test_managed_self_update_guard_injected(self):
        self.assertEqual(CodexProxyRuntimeAdapter().docker_env(proxy_api_key="fixture")["NEXUS_MANAGED_RELEASE"], "1")

    def test_approved_start_uses_immutable_id_and_never_builds(self):
        runtime = SimpleNamespace(runtime_type="codex_proxy")
        target = "apps.providers.runtime_runner."
        with patch(target + "approved_release", return_value=self.receipt), \
             patch(target + "verify_image"), \
             patch.object(CodexProxyRuntimeAdapter, "prepare_storage"), \
             patch(target + "build_image") as build, \
             patch(target + "_remove_provider_runtime_containers"), \
             patch(target + "docker_run_command", return_value=["docker", "run", self.image_id]) as command, \
             patch(target + "_run_docker", return_value="container"), \
             patch(target + "uses_container_network_endpoints", return_value=True), \
             patch(target + "provider_runtime_endpoint", return_value=("runtime", 8080)), \
             patch(target + "wait_for_runtime_port"):
            result = DockerProviderRuntimeRunner().start(runtime=runtime, proxy_api_key="fixture")
        build.assert_not_called()
        self.assertEqual(command.call_args.kwargs["image"], self.image_id)
        self.assertEqual(result.container_id, "container")
