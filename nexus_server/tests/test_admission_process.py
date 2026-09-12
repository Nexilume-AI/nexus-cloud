"""Real verifier-process contracts; no Docker daemon or application database."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location("admission_process",
    Path(__file__).resolve().parents[1] / "apps/agents/admission_process.py")
admission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admission)


class AdmissionProcessTests(unittest.TestCase):
    def command(self, source, *args):
        return [sys.executable, "-I", "-c", source, *args]

    def test_success_failure_and_arguments_are_not_shell_interpreted(self):
        argument = "a path with spaces & ; $(not-a-command) 中文"
        for code in (0, 7):
            actual = admission.verifier_exit_code(self.command(
                "import sys; assert sys.argv[1] == sys.argv[2]; sys.exit(int(sys.argv[3]))",
                argument, argument, str(code)), timeout=15)
            self.assertEqual(actual, code)

    def test_no_input_or_scanner_output_is_retained(self):
        source = """
import os, sys
assert sys.stdin.buffer.read() == b''
for _ in range(256):
    os.write(1, b'x' * 65536)
    os.write(2, b'y' * 65536)
sys.exit(0)
"""
        # 32 MiB of actual scanner output is discarded at the OS handle, not
        # collected and subsequently truncated by the controller.
        with patch.object(admission.subprocess, "Popen", wraps=subprocess.Popen) as launch:
            self.assertEqual(admission.verifier_exit_code(self.command(source), timeout=15), 0)
        self.assertEqual(launch.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertNotIn("shell", launch.call_args.kwargs)

    def test_missing_verifier_has_no_sensitive_exception_text(self):
        with self.assertRaises(OSError) as error:
            admission.verifier_exit_code(["nonexistent-private-verifier-credential"], timeout=15)
        self.assertNotIn("credential", str(error.exception))

    def _child_cleanup(self, *, parent_exits):
        with tempfile.TemporaryDirectory(prefix="nexus-verifier-owned-") as directory:
            root = Path(directory)
            child = "import time; from pathlib import Path; Path('ready').touch(); time.sleep(4); Path('escaped').touch(); time.sleep(30)"
            parent = """
import os, subprocess, sys, time
from pathlib import Path
os.chdir(sys.argv[1])
subprocess.Popen([sys.executable, '-I', '-c', sys.argv[2]])
deadline = time.monotonic() + 10
while not Path('ready').exists():
    if time.monotonic() > deadline:
        sys.exit(8)
    time.sleep(.01)
if sys.argv[3] == 'sleep':
    time.sleep(30)
"""
            peer = subprocess.Popen(self.command("import time; time.sleep(30)"),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                command = self.command(parent, directory, child, "exit" if parent_exits else "sleep")
                if parent_exits:
                    self.assertEqual(admission.verifier_exit_code(command, timeout=15), 0)
                else:
                    with self.assertRaises(subprocess.TimeoutExpired) as error:
                        admission.verifier_exit_code(command, timeout=2)
                    self.assertEqual(error.exception.cmd, "image-admission-verifier")
                    self.assertIsNone(error.exception.output)
                    self.assertIsNone(error.exception.stderr)
                self.assertTrue((root / "ready").exists(), "The real descendant must have started")
                self.assertIsNone(peer.poll(), "Unrelated processes must remain alive")
                (root / "ready").unlink()
                # Windows proves the child released its cwd; both platforms
                # verify it cannot wake up and write into the same path later.
                root.rmdir()
                root.mkdir()
                time.sleep(4)
                self.assertFalse((root / "escaped").exists())
            finally:
                peer.kill()
                peer.wait(timeout=10)

    def test_timeout_cleans_exact_owned_process_tree(self):
        self._child_cleanup(parent_exits=False)

    def test_success_also_cleans_lingering_descendants(self):
        self._child_cleanup(parent_exits=True)

    def test_owner_death_cleans_descendants_without_process_name_matching(self):
        with tempfile.TemporaryDirectory(prefix="nexus-verifier-owner-") as directory:
            root = Path(directory)
            source = """
import os, time
from pathlib import Path
os.chdir(__import__('sys').argv[1])
Path('ready').touch()
time.sleep(4)
Path('escaped').touch()
"""
            owner_source = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location('owned_admission', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.verifier_exit_code([sys.executable, '-I', '-c', sys.argv[2], sys.argv[3]], timeout=25)
"""
            owner = subprocess.Popen([sys._base_executable, "-I", "-c", owner_source,
                str(Path(admission.__file__).resolve()), source, directory],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 10
                while not (root / "ready").exists() and owner.poll() is None:
                    if time.monotonic() > deadline:
                        self.fail("Owned verifier did not start")
                    time.sleep(.02)
                self.assertTrue((root / "ready").exists())
                self.assertIsNone(owner.poll())
            finally:
                owner.kill()
                owner.wait(timeout=10)
            time.sleep(4.2)
            self.assertFalse((root / "escaped").exists(), "Verifier must not survive its controller")

    @unittest.skipUnless(os.name == "nt", "Windows job assignment gate")
    def test_failed_job_assignment_does_not_launch_verifier(self):
        with tempfile.TemporaryDirectory(prefix="nexus-verifier-gate-") as directory:
            marker = Path(directory) / "launched"
            with patch.object(admission._WindowsJob, "assign", side_effect=OSError("assignment failed")):
                with self.assertRaises(OSError):
                    admission.verifier_exit_code(self.command(
                        "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)), timeout=15)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "nt", "Windows job drain contract")
    def test_job_close_waits_for_all_owned_processes_before_releasing_handle(self):
        job = admission._WindowsJob.__new__(admission._WindowsJob)
        job.handle = 123
        job.api = Mock()
        job._process_handles = lambda: []
        observations = []
        def active():
            self.assertFalse(job.api.CloseHandle.called)
            observations.append(True)
            return (2, 1, 0)[len(observations) - 1]
        with patch.object(job, "_active_processes", side_effect=active, create=True), \
                patch.object(admission.time, "sleep"):
            job.close()
        self.assertEqual(len(observations), 3)
        job.api.TerminateJobObject.assert_called_once_with(123, 1)
        job.api.CloseHandle.assert_called_once_with(123)

    @unittest.skipUnless(os.name == "nt", "Windows job drain contract")
    def test_job_drain_deadline_rejects_instead_of_reporting_success(self):
        job = admission._WindowsJob.__new__(admission._WindowsJob)
        job.handle = 123
        job.api = Mock()
        job._process_handles = lambda: []
        with patch.object(job, "_active_processes", return_value=1, create=True), \
                patch.object(admission.time, "monotonic", side_effect=[0, 16]):
            with self.assertRaisesRegex(OSError, "Verifier job cleanup timed out"):
                job.close()
        job.api.CloseHandle.assert_called_once_with(123)

    @unittest.skipUnless(os.name == "nt", "Windows job drain contract")
    def test_job_query_failure_still_closes_the_kill_on_close_handle(self):
        job = admission._WindowsJob.__new__(admission._WindowsJob)
        job.handle = 123
        job.api = Mock()
        job._process_handles = lambda: []
        with patch.object(job, "_active_processes", side_effect=OSError("Cannot query verifier job"), create=True):
            with self.assertRaisesRegex(OSError, "Cannot query verifier job"):
                job.close()
        job.api.CloseHandle.assert_called_once_with(123)

    @unittest.skipUnless(os.name == "nt", "Windows job process handle contract")
    def test_zero_active_count_still_waits_for_process_handle_signals(self):
        job = admission._WindowsJob.__new__(admission._WindowsJob)
        job.handle = 123
        job.api = Mock()
        job._process_handles = lambda: [456, 789]
        job._active_processes = lambda: 0
        def signaled(handle, timeout):
            self.assertTrue(job.api.TerminateJobObject.called)
            self.assertFalse(job.api.CloseHandle.called)
            self.assertIn(handle, (456, 789))
            self.assertLessEqual(timeout, 15000)
            return 0
        job.api.WaitForSingleObject.side_effect = signaled
        job.close()
        self.assertEqual(job.api.WaitForSingleObject.call_count, 2)
        self.assertEqual([call.args[0] for call in job.api.CloseHandle.call_args_list], [456, 789, 123])

    @unittest.skipUnless(os.name == "nt", "Windows job process handle contract")
    def test_process_wait_failure_closes_all_handles_and_never_returns_success(self):
        for result, message in ((258, "Verifier job cleanup timed out"), (0xffffffff, "Cannot wait for verifier process")):
            with self.subTest(result=result):
                job = admission._WindowsJob.__new__(admission._WindowsJob)
                job.handle = 123
                job.api = Mock()
                job.api.WaitForSingleObject.return_value = result
                job._process_handles = lambda: [456, 789]
                job._active_processes = lambda: 0
                with self.assertRaisesRegex(OSError, message):
                    job.close()
                self.assertEqual([call.args[0] for call in job.api.CloseHandle.call_args_list], [456, 789, 123])


if __name__ == "__main__":
    unittest.main()
