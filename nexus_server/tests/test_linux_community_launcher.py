"""Linux process ownership regressions using real, test-owned processes."""
import importlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == 'linux', 'Linux /proc and flock required')
class LinuxCommunityLauncherTests(unittest.TestCase):
    def setUp(self):
        self.launcher = importlib.import_module('nexus_personal.linux_launcher')

    def child(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], start_new_session=True)
        def cleanup():
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=10)
        self.addCleanup(cleanup)
        return child

    def test_graceful_stop_owned_process(self):
        child = self.child()
        self.launcher.stop([{'pid': child.pid, 'identity': self.launcher.identity(child.pid)}])
        self.assertEqual(child.wait(timeout=5), -signal.SIGTERM)

    def test_reused_pid_rejects_entire_stop_before_signalling(self):
        first, other = self.child(), self.child()
        entries = [dict(pid=first.pid, identity=self.launcher.identity(first.pid)),
                   dict(pid=other.pid, identity=['different-boot', '0'])]
        with self.assertRaisesRegex(RuntimeError, 'PROCESS_IDENTITY_MISMATCH'):
            self.launcher.stop(entries)
        self.assertIsNone(first.poll())
        self.assertIsNone(other.poll())

    def test_stopped_pid_is_idempotent(self):
        child = self.child()
        entry = dict(pid=child.pid, identity=self.launcher.identity(child.pid))
        child.terminate()
        child.wait(timeout=5)
        self.launcher.stop([entry])
        self.assertIsNone(self.launcher.identity(child.pid))

    def test_port_check_rejects_active_listener_but_allows_time_wait(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
            listener.listen()
            with self.assertRaises(OSError):
                self.launcher.check_port(port)
            with socket.create_connection(('127.0.0.1', port)) as client:
                connection, _ = listener.accept()
                connection.close()
                self.assertEqual(client.recv(1), b'')
        self.launcher.check_port(port)

    @unittest.skipIf(hasattr(os, 'getuid') and os.getuid() == 0, 'Run installation checks as non-root')
    def test_state_symlink_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'run').mkdir(mode=0o700)
            target = root / 'unrelated.json'
            target.write_text('{}')
            (root / 'run/community-processes.json').symlink_to(target)
            with self.assertRaisesRegex(Exception, 'INSTALL_LINK_REJECTED'):
                self.launcher.main(['stop', '--installation', str(root)])
            self.assertEqual(target.read_text(), '{}')

    @unittest.skipIf(hasattr(os, 'getuid') and os.getuid() == 0, 'Run installation checks as non-root')
    def test_foreign_installation_state_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'run').mkdir(mode=0o700)
            (root / 'run/community-processes.json').write_text(json.dumps({
                'platform': 'linux', 'installation_root': '/another-installation', 'processes': []}))
            with self.assertRaisesRegex(RuntimeError, 'PROCESS_STATE_INVALID'):
                self.launcher.main(['stop', '--installation', str(root)])

    @unittest.skipIf(hasattr(os, 'getuid') and os.getuid() == 0, 'Run installation checks as non-root')
    def test_concurrent_lifecycle_command_is_refused(self):
        import fcntl
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'run').mkdir(mode=0o700)
            with (root / 'run/community-linux.lock').open('w') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    self.launcher.main(['stop', '--installation', str(root)])


if __name__ == '__main__':
    unittest.main()
