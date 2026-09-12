"""Real Ed25519 and verifier subprocesses; no Docker or production signing keys."""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from rest_framework.exceptions import ValidationError

from nexus_personal import image_admission as admission
from nexus_personal.install import _private_directory


IMAGE = 'sha256:' + 'a' * 64
HOST = 'test-worker'


def b64(value):
    return base64.b64encode(value).decode('ascii')


class SignedEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.public = self.key.public_key().public_bytes(serialization.Encoding.Raw,
                                                       serialization.PublicFormat.Raw)
        self.payload = {'purpose': admission.PURPOSE, 'image_id': IMAGE, 'host_id': HOST,
                        'issued_at': 100, 'expires_at': 200}

    def envelope(self, payload=None, *, raw=None):
        content = raw if raw is not None else json.dumps(self.payload if payload is None else payload).encode()
        return json.dumps({'payload': b64(content), 'signature': b64(self.key.sign(content))}).encode()

    def verify(self, raw, **kwargs):
        admission.verify_envelope(raw, **{'public_key': self.public, 'image_id': IMAGE,
                                         'host_id': HOST, 'now': 150, **kwargs})

    def test_real_signature_accepts_exact_bytes_without_json_reencoding(self):
        self.verify(self.envelope())
        self.verify(self.envelope(raw=json.dumps(self.payload, indent=2).encode() + b'\n'))

    def test_wrong_key_signature_and_payload_tampering_rejected(self):
        other = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
        with self.assertRaises(admission.AdmissionRejected):
            self.verify(self.envelope(), public_key=other)
        for field, value in [('payload', b64(b'{}')), ('signature', b64(b'0' * 64))]:
            envelope = json.loads(self.envelope())
            envelope[field] = value
            with self.subTest(field=field), self.assertRaises(admission.AdmissionRejected):
                self.verify(json.dumps(envelope).encode())

    def test_signed_wrong_scope_or_extra_fields_rejected(self):
        for changes in ({'image_id': 'sha256:' + 'b' * 64}, {'host_id': 'other-worker'},
                        {'purpose': 'other-protocol'}, {'wildcard': True}, {'host_id': '*'}):
            with self.subTest(changes=changes), self.assertRaises(admission.AdmissionRejected):
                self.verify(self.envelope({**self.payload, **changes}))

    def test_expiry_future_dates_types_and_maximum_lifetime(self):
        for changes in ({'issued_at': 151}, {'expires_at': 150}, {'issued_at': -1},
                        {'issued_at': True}, {'expires_at': 200.0},
                        {'expires_at': 101 + admission.MAX_LIFETIME}):
            with self.subTest(changes=changes), self.assertRaises(admission.AdmissionRejected):
                self.verify(self.envelope({**self.payload, **changes}))
        self.verify(self.envelope(), now=100)
        self.verify(self.envelope(), now=199)

    def test_malformed_envelopes_and_bounds(self):
        envelope = json.loads(self.envelope())
        for raw in (b'', b'[]', b'x' * 8193, b'\xff',
                    json.dumps({**envelope, 'public_key': b64(self.public)}).encode(),
                    json.dumps({**envelope, 'signature': 'not-base64'}).encode(),
                    json.dumps({**envelope, 'signature': b64(b'0' * 63)}).encode(),
                    json.dumps({**envelope, 'payload': b64(b'0' * 4097)}).encode(),
                    self.envelope()[:-1] + b',"signature":"duplicate"}'):
            with self.subTest(length=len(raw)), self.assertRaises(admission.AdmissionRejected):
                self.verify(raw)

    def test_signed_duplicate_keys_and_non_json_values_rejected(self):
        for raw in (json.dumps(self.payload).encode()[:-1] + b',"host_id":"test-worker"}',
                    b'{"issued_at":NaN}', b'[]', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(admission.AdmissionRejected):
                self.verify(self.envelope(raw=raw))

    def test_tags_paths_and_invalid_worker_not_admissible(self):
        for kwargs in ({'image_id': '../../file'}, {'image_id': 'image:latest'},
                       {'image_id': IMAGE.upper()}, {'host_id': '../worker'}, {'host_id': ''}):
            with self.subTest(kwargs=kwargs), self.assertRaises(admission.AdmissionRejected):
                self.verify(self.envelope(), **kwargs)


class SignedApprovalFilesTests(unittest.TestCase):
    envelope = SignedEnvelopeTests.envelope

    def setUp(self):
        SignedEnvelopeTests.setUp(self)
        self.temporary = tempfile.TemporaryDirectory(prefix='nexus-signed-approval-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'owned'
        _private_directory(self.root, create=True)
        self.approvals = self.root / 'approvals'
        self.approvals.mkdir(mode=0o700)
        self.trust = self.root / 'trust.json'
        self.policy = {'schema_version': 1, 'host_id': HOST, 'public_key': b64(self.public),
                       'approvals_dir': str(self.approvals), 'revoked_image_ids': []}
        self.save_policy()
        current = int(time.time())
        self.payload.update(issued_at=current - 10, expires_at=current + 300)
        self.receipt = self.approvals / (IMAGE[7:] + '.json')
        self.receipt.write_bytes(self.envelope())

    def save_policy(self):
        self.trust.write_text(json.dumps(self.policy), encoding='utf-8')
        self.trust.chmod(0o600)

    def verify_file(self):
        admission.verify_approval(trust_config=self.trust, image_id=IMAGE, host_id=HOST)

    def test_protected_policy_real_signature_revocation_and_key_rotation(self):
        self.verify_file()
        self.policy['revoked_image_ids'] = [IMAGE]
        self.save_policy()
        with self.assertRaises(admission.AdmissionRejected):
            self.verify_file()
        self.policy['revoked_image_ids'] = []
        self.policy['public_key'] = b64(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
        self.save_policy()
        with self.assertRaises(admission.AdmissionRejected):
            self.verify_file()

    def test_missing_oversized_and_directory_receipts_are_rejected(self):
        self.receipt.unlink()
        with self.assertRaises(admission.AdmissionRejected):
            self.verify_file()
        self.receipt.write_bytes(b'x' * 8193)
        with self.assertRaises(admission.AdmissionRejected):
            self.verify_file()
        self.receipt.unlink()
        self.receipt.mkdir()
        with self.assertRaises(admission.AdmissionRejected):
            self.verify_file()

    def test_receipt_links_and_relative_policy_paths_are_rejected(self):
        # Windows without link privilege still covers the policy branch using
        # the actual path predicates; POSIX exercises a real filesystem link.
        with patch.object(Path, 'is_symlink', return_value=True):
            with self.assertRaises(admission.AdmissionRejected):
                self.verify_file()
        with self.assertRaises(admission.AdmissionRejected):
            admission.verify_approval(trust_config='relative-trust.json', image_id=IMAGE, host_id=HOST)
        if os.name != 'nt':
            target = self.root / 'other.json'
            target.write_bytes(self.receipt.read_bytes())
            self.receipt.unlink()
            self.receipt.symlink_to(target)
            with self.assertRaises(admission.AdmissionRejected):
                self.verify_file()

    def test_cli_failure_is_constant_and_does_not_write_signing_material(self):
        import contextlib
        import io
        before = {str(p.relative_to(self.root)) for p in self.root.rglob('*')}
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = admission.main(['--trust-config', str(self.root / 'private-path-sentinel'),
                                   '--host-id', HOST, IMAGE])
        self.assertEqual(code, 1)
        self.assertEqual(stderr.getvalue(), 'AGENT_SIGNED_APPROVAL_REJECTED\n')
        self.assertEqual(before, {str(p.relative_to(self.root)) for p in self.root.rglob('*')})

    def test_acl_failure_and_policy_errors_never_echo_input(self):
        for field, value in [('host_id', 'wrong-host-private-sentinel'),
                             ('revoked_image_ids', ['not-a-digest']), ('public_key', 'private-sentinel')]:
            original = self.policy[field]
            self.policy[field] = value
            self.save_policy()
            with self.assertRaisesRegex(admission.AdmissionRejected, '^AGENT_SIGNED_APPROVAL_REJECTED$'):
                self.verify_file()
            self.policy[field] = original
        with patch.object(admission, 'read_protected_json', side_effect=PermissionError('private-path')):
            with self.assertRaisesRegex(admission.AdmissionRejected, '^AGENT_SIGNED_APPROVAL_REJECTED$'):
                self.verify_file()

    def test_existing_docker_gate_runs_real_verifier_and_refuses_revoked_image(self):
        from apps.agents import docker_policy
        server = str(Path(admission.__file__).resolve().parents[1])
        command = [sys.executable, '-I', '-c',
                   'import sys; sys.path.insert(0,sys.argv.pop(1)); '
                   'from nexus_personal.image_admission import main; raise SystemExit(main())',
                   server, '--trust-config', str(self.trust), '--host-id', HOST]
        with patch.object(docker_policy, 'settings', SimpleNamespace(
                NEXUS_AGENT_IMAGE_ADMISSION_COMMAND=command, NEXUS_PRODUCTION=True)):
            docker_policy.verify_admission(IMAGE)
            self.policy['revoked_image_ids'] = [IMAGE]
            self.save_policy()
            with self.assertRaisesRegex(ValidationError, 'AGENT_IMAGE_ADMISSION_REJECTED'):
                docker_policy.verify_admission(IMAGE)


if __name__ == '__main__':
    unittest.main()
