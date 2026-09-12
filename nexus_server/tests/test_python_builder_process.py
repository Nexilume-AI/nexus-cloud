"""Real build-command I/O regressions; no Docker daemon or application database."""
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


from apps.agents import python_builder as builder


class PythonBuilderProcessTests(unittest.TestCase):
    def run_command(self, source, **kwargs):
        with patch.object(builder, 'settings', SimpleNamespace(NEXUS_AGENT_PYTHON_DOCKER=sys.executable)):
            return builder.docker(['-I', '-c', source], **kwargs)

    def test_nonreading_child_cannot_block_input_before_deadline(self):
        # A real pipe-sized write used to block before the timeout even started.
        # The child eventually reads so the regression also terminates pre-fix.
        started = time.monotonic()
        with self.assertRaises(builder.BuildFailure) as caught:
            self.run_command(
                'import sys,time; time.sleep(2); sys.stdin.buffer.read(); print("done")',
                input_bytes=b'x' * (1024 * 1024), timeout=0.3,
            )
        self.assertEqual(caught.exception.code, 'BUILD_TIMEOUT')
        self.assertLess(time.monotonic() - started, 1.8)

    def test_binary_input_is_complete_and_eof_is_delivered(self):
        payload = bytes(range(256)) * 256
        result = self.run_command(
            'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())',
            input_bytes=payload, timeout=10,
        )
        self.assertEqual(result, payload)

    def test_absent_and_empty_input_are_eof(self):
        for payload in (None, b''):
            with self.subTest(payload=payload):
                self.assertEqual(self.run_command(
                    'import sys; assert sys.stdin.buffer.read() == b""; print("eof")',
                    input_bytes=payload, timeout=10,
                ).strip(), b'eof')

    def test_child_may_exit_without_reading_input(self):
        self.assertEqual(self.run_command('print("ready")',
            input_bytes=b'x' * (1024 * 1024), timeout=10).strip(), b'ready')

    def test_errors_do_not_expose_child_output(self):
        with self.assertRaises(builder.BuildFailure) as caught:
            self.run_command('import sys; print("private-input", file=sys.stderr); sys.exit(2)',
                input_bytes=b'private-input', timeout=10)
        self.assertEqual(caught.exception.code, 'BUILD_STAGE_FAILED')
        self.assertNotIn('private-input', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
