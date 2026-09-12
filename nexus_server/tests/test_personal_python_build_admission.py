import json
import subprocess
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from nexus_personal.python_build_admission import verify


HOST = '12345678-1234-4234-9234-123456789abc'
DIGEST = 'sha256:' + 'a' * 64


class PersonalPythonBuildAdmissionTests(TestCase):
    def image(self, **label_changes):
        labels = {'nexus.managed': 'python-build', 'nexus.python.host': HOST,
            'nexus.python.build': 'b' * 32, 'nexus.python.source': 'c' * 64, **label_changes}
        return [{'Id': DIGEST, 'Config': {'Labels': labels, 'User': '65532:65532',
            'WorkingDir': '/opt/nexus-python', 'Entrypoint': ['python', '/opt/nexus-python/nexus_boot.py']}}]

    def result(self, body, code=0):
        return SimpleNamespace(returncode=code, stdout=json.dumps(body).encode())

    @patch('nexus_personal.python_build_admission.subprocess.run')
    def test_accepts_only_exact_recipe_owned_image(self, run):
        run.return_value = self.result(self.image())
        self.assertTrue(verify(HOST, DIGEST))
        self.assertEqual(run.call_args.args[0], ['docker', 'image', 'inspect', DIGEST])
        self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)

    @patch('nexus_personal.python_build_admission.subprocess.run')
    def test_rejects_foreign_labels_recipe_and_unbounded_output(self, run):
        cases = [self.image(**{'nexus.python.host': 'other'}),
            [{**self.image()[0], 'Config': {**self.image()[0]['Config'], 'User': '0'}}], [], {'not': 'a-list'}]
        for body in cases:
            with self.subTest(body_type=type(body).__name__):
                run.return_value = self.result(body)
                self.assertFalse(verify(HOST, DIGEST))
        run.return_value = SimpleNamespace(returncode=0, stdout=b'x' * (1024 * 1024 + 1))
        self.assertFalse(verify(HOST, DIGEST))

    @patch('nexus_personal.python_build_admission.subprocess.run')
    def test_invalid_identity_and_docker_failure_fail_closed(self, run):
        self.assertFalse(verify('bad', DIGEST))
        self.assertFalse(verify(HOST, 'latest'))
        run.assert_not_called()
        run.side_effect = OSError('private docker detail')
        self.assertFalse(verify(HOST, DIGEST))
