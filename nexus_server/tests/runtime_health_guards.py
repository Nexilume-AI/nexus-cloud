"""Portable process health and recovery guards for both distributions."""
import json
import sys
import time
from pathlib import Path
import subprocess
import tempfile
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import override_settings
from apps.common.runtime_health import agent_builder_readiness, record_agent_builder_heartbeat


class BuilderHeartbeatGuards:
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.status_file = Path(self.directory.name) / "builder.json"
        self.config = dict(
            NEXUS_PRODUCTION=False, NEXUS_PROCESS_ROLE="agent-builder",
            NEXUS_AGENT_PYTHON_BUILDS_ENABLED=True, NEXUS_AGENT_BUILDER_STALE_SECONDS=30,
            NEXUS_AGENT_BUILDER_STATUS_FILE=str(self.status_file),
            NEXUS_AGENT_BUILDER_STATUS_GENERATION="current-launch",
        )
        override = override_settings(**self.config)
        override.enable()
        self.addCleanup(override.disable)

    def test_real_processes_share_heartbeat_without_shared_default_cache(self):
        # Each interpreter has its own LocMemCache, as in the formal launcher.
        source = '''
import json, sys
from django.conf import settings
settings.configure(**json.loads(sys.argv[1]))
from apps.common.runtime_health import record_agent_builder_heartbeat, agent_builder_readiness
if sys.argv[2] == 'write':
    record_agent_builder_heartbeat()
else:
    print(json.dumps(agent_builder_readiness()))
'''
        config = {**self.config, "CACHES": {"default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "builder-regression",
        }}}
        for operation in ("write", "read"):
            result = subprocess.run([sys.executable, "-c", source, json.dumps(config), operation],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"], result.stdout)

    def test_missing_expired_and_previous_launch_are_not_ready(self):
        self.assertEqual(agent_builder_readiness()["code"], "AGENT_BUILDER_HEARTBEAT_MISSING")
        record_agent_builder_heartbeat()
        self.assertTrue(agent_builder_readiness()["ok"])
        with patch("apps.common.runtime_health.time.time", return_value=time.time() + 31):
            self.assertEqual(agent_builder_readiness()["code"], "AGENT_BUILDER_HEARTBEAT_STALE")
        with override_settings(NEXUS_AGENT_BUILDER_STATUS_GENERATION="next-launch"):
            self.assertEqual(agent_builder_readiness()["code"], "AGENT_BUILDER_HEARTBEAT_MISSING")

    def test_invalid_or_unreadable_snapshot_fails_closed(self):
        invalid = ["{", "[]", "x" * 4097]
        for timestamp in ("NaN", "Infinity", "bad", -1, True, time.time() + 3600):
            invalid.append(json.dumps({"version": 1, "recorded_at": timestamp, "generation": "current-launch", "role": "agent-builder"}))
        for content in invalid:
            with self.subTest(content=content[:60]):
                self.status_file.write_text(content, encoding="utf-8")
                self.assertEqual(agent_builder_readiness()["code"], "AGENT_BUILDER_HEARTBEAT_UNAVAILABLE")
        with patch("pathlib.Path.open", side_effect=PermissionError("private path must not leak")):
            self.assertEqual(agent_builder_readiness(), {"ok": False, "code": "AGENT_BUILDER_HEARTBEAT_UNAVAILABLE"})

    def test_atomic_write_failure_keeps_previous_snapshot_and_cleans_tempfile(self):
        record_agent_builder_heartbeat()
        previous = self.status_file.read_bytes()
        with patch("apps.common.runtime_health.os.replace", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                record_agent_builder_heartbeat()
        self.assertEqual(self.status_file.read_bytes(), previous)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [self.status_file])

    def test_only_builder_writes_and_disabled_builds_do_not_require_heartbeat(self):
        with override_settings(NEXUS_PROCESS_ROLE="web"):
            with self.assertRaises(RuntimeError):
                record_agent_builder_heartbeat()
        with override_settings(NEXUS_AGENT_PYTHON_BUILDS_ENABLED=False):
            self.assertEqual(agent_builder_readiness(), {"ok": True, "required": False})

    def test_production_keeps_shared_cache_even_when_local_path_is_configured(self):
        with override_settings(NEXUS_PRODUCTION=True), patch("apps.common.runtime_health.cache") as shared:
            record_agent_builder_heartbeat()
            shared.set.assert_called_once()
            shared.get.return_value = shared.set.call_args.args[1]
            self.assertTrue(agent_builder_readiness()["ok"])
        self.assertFalse(self.status_file.exists())

    def test_heartbeat_thread_retries_transient_failure_without_logging_details(self):
        from unittest.mock import Mock
        from apps.agents.management.commands.run_agent_python_builds import _heartbeat_loop
        stop = Mock()
        stop.wait.side_effect = [False, False, True]
        stderr = StringIO()
        with patch("apps.agents.management.commands.run_agent_python_builds.record_agent_builder_heartbeat",
                   side_effect=[OSError("sensitive-path"), None]) as record:
            _heartbeat_loop(stop, stderr)
        self.assertEqual(record.call_count, 2)
        self.assertIn("retrying", stderr.getvalue())
        self.assertNotIn("sensitive-path", stderr.getvalue())

class ImageSweeperGuards:
    def test_once_uses_same_task_without_broker(self):
        with patch("apps.gateway.management.commands.run_image_operation_sweeper.expire_image_operations", return_value={"expired": 2}) as recover:
            output = StringIO()
            call_command("run_image_operation_sweeper", once=True, stdout=output)
            recover.assert_called_once_with()
            self.assertIn("expired=2", output.getvalue())

    def test_recovers_after_transient_failure_without_logging_secrets(self):
        output = StringIO()
        with patch("apps.gateway.management.commands.run_image_operation_sweeper.expire_image_operations", side_effect=[RuntimeError("secret must not appear"), {"expired": 0}]) as recover, patch("apps.gateway.management.commands.run_image_operation_sweeper.time.sleep", side_effect=[None, KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                call_command("run_image_operation_sweeper", interval=1, stderr=output, stdout=StringIO())
            self.assertEqual(recover.call_count, 2)
        self.assertNotIn("secret", output.getvalue())
        self.assertIn("will retry", output.getvalue())
