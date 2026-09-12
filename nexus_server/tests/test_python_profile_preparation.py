import json
from pathlib import Path
import runpy
import subprocess
import sys
from unittest import TestCase, mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare_agent_python_profile.py"
DIGEST = "sha256:" + "a" * 64


class PythonProfilePreparationTests(TestCase):
    def run_preparation(self, *, probe_error=None, digest=DIGEST):
        # Do not write the real operator profile or invoke Docker in unit tests.
        with mock.patch.object(sys, "argv", [str(SCRIPT), "--use-existing", "--image", "test-profile"]), mock.patch(
            "subprocess.check_output", return_value=json.dumps([{"Id": digest}]).encode()
        ), mock.patch("subprocess.run", side_effect=probe_error) as probe, mock.patch.object(
            Path, "mkdir"
        ), mock.patch.object(Path, "write_text") as write:
            try:
                runpy.run_path(str(SCRIPT), run_name="__main__")
            except (subprocess.CalledProcessError, SystemExit):
                write.assert_not_called()
                raise
            return probe, write

    def test_profile_requires_native_adapter_in_isolated_container(self):
        probe, write = self.run_preparation()
        probe.assert_called_once()
        command = probe.call_args.args[0]
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertIn("--read-only", command)
        self.assertIn("hasattr(NexusAgent, 'as_mcp_server')", command[-1])
        self.assertIn("NexusAgent(runtime='hosted', cloud_publish=True)", command[-1])
        self.assertIn("agent.capability('profile_probe'", command[-1])
        self.assertIn("agent.as_mcp_server()", command[-1])
        write.assert_called_once()
        self.assertEqual(json.loads(write.call_args.args[0])["digest"], DIGEST)

    def test_failed_probe_never_replaces_existing_configuration(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_preparation(probe_error=subprocess.CalledProcessError(1, "probe"))

    def test_mutable_image_reference_never_replaces_configuration(self):
        with self.assertRaises(SystemExit):
            self.run_preparation(digest="mutable-tag")
