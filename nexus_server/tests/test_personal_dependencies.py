"""Community dependency locks plus opt-in verification of a clean installation.

No network, package installation or writes occur in this test module. The live
check deliberately refuses the developer's global Python/site-packages.
"""
from importlib import metadata
import os
from pathlib import Path
import platform
import re
import site
import sys
import unittest

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


ROOT = Path(__file__).resolve().parents[1] / 'nexus_personal'


def read_lock(text):
    """Strict subset emitted by our resolver: exact pins plus SHA-256 hashes."""
    result = {}
    joined = text.replace('\\\n', '')
    for line in joined.splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        match = re.fullmatch(r'([a-z0-9][a-z0-9.-]*)==([0-9][a-zA-Z0-9.!+-]*)'
                             r'((?:\s+--hash=sha256:[a-f0-9]{64})+)\s*', line)
        if not match:
            raise ValueError('DEPENDENCY_LOCK_INVALID')
        name = canonicalize_name(match[1])
        if name in result:
            raise ValueError('DEPENDENCY_LOCK_DUPLICATE')
        result[name] = match[2]
    if not result:
        raise ValueError('DEPENDENCY_LOCK_EMPTY')
    return result


def pins(include_tests=False, target='win'):
    if target not in {'win', 'linux'}:
        raise ValueError('DEPENDENCY_PLATFORM_UNSUPPORTED')
    build = read_lock((ROOT / f'build-requirements-{target}-py314.txt').read_text(encoding='utf-8'))
    runtime = read_lock((ROOT / f'requirements-{target}-py314.txt').read_text(encoding='utf-8'))
    if any(name in runtime and runtime[name] != version for name, version in build.items()):
        raise ValueError('DEPENDENCY_BUILD_RUNTIME_CONFLICT')
    result = {**build, **runtime}
    if include_tests:
        testing = read_lock((ROOT / f'test-requirements-{target}-py314.txt').read_text(encoding='utf-8'))
        if any(name in result and result[name] != version for name, version in testing.items()):
            raise ValueError('DEPENDENCY_TEST_RUNTIME_CONFLICT')
        result.update(testing)
    return result


class PersonalDependencyLockTests(unittest.TestCase):
    def test_security_floors_and_no_legacy_ssh_dependency(self):
        # Keep known-vulnerable crypto/build versions out of every production
        # and test closure. SSH belongs to the private legacy distribution;
        # Community Computers use the outbound Runtime, not Paramiko.
        for target in ('win', 'linux'):
            with self.subTest(platform=target):
                locked = pins(include_tests=True, target=target)
                self.assertGreaterEqual(Version(locked['cryptography']), Version('50.0.1'))
                self.assertGreaterEqual(Version(locked['setuptools']), Version('83.0.0'))
                self.assertNotIn('paramiko', locked)

    def test_every_direct_requirement_has_an_exact_compatible_hashed_pin(self):
        for target in ('win', 'linux'):
            locked = pins(include_tests=True, target=target)
            self.assertNotIn('fastmcp', pins(target=target))
            for file in ('requirements.in', 'build-requirements.in', 'test-requirements.in'):
                for line in (ROOT / file).read_text(encoding='utf-8').splitlines():
                    if not line.strip() or line.startswith('#'):
                        continue
                    req = Requirement(line)
                    with self.subTest(package=req.name, platform=target):
                        self.assertIsNone(req.url)
                        self.assertIsNone(req.marker)
                        self.assertIn(canonicalize_name(req.name), locked)
                        self.assertIn(locked[canonicalize_name(req.name)], req.specifier)

    def test_unpinned_unhashed_nested_and_remote_requirements_are_rejected(self):
        digest = 'a' * 64
        for content in ('django>=5', 'django==5.2', '-r elsewhere.txt',
                        '--index-url https://example.test', 'django @ https://example.test/a.whl',
                        f'django==5.2 --hash=md5:{digest}',
                        f'django==5.2 --hash=sha256:{digest}\ndjango==5.2 --hash=sha256:{digest}',
                        ''):
            with self.subTest(content=content), self.assertRaises(ValueError):
                read_lock(content)

    @unittest.skipUnless(os.environ.get('NEXUS_PERSONAL_DEPENDENCY_ACCEPTANCE') == '1',
                         'Requires a newly installed, dedicated CPython 3.14 Windows/Linux environment')
    def test_installed_dependency_closure_is_exact_and_environment_is_isolated(self):
        self.assertIn(sys.platform, {'win32', 'linux'})
        self.assertIn(platform.machine().lower(), {'amd64', 'x86_64'})
        self.assertEqual(sys.version_info[:2], (3, 14))
        self.assertNotEqual(sys.prefix, sys.base_prefix)
        self.assertFalse(site.ENABLE_USER_SITE)
        prefix = Path(sys.prefix).resolve()
        include_tests = os.environ.get('NEXUS_PERSONAL_DEPENDENCY_TEST_EXTRAS') == '1'
        locked = pins(include_tests=include_tests, target='win' if sys.platform == 'win32' else 'linux')
        installed = {}
        for dist in metadata.distributions():
            name = canonicalize_name(dist.metadata['Name'])
            self.assertTrue(Path(dist.locate_file('')).resolve().is_relative_to(prefix), name)
            self.assertNotIn(name, installed)
            installed[name] = dist
        self.assertEqual(set(installed), set(locked) | {'pip'})
        for name, version in locked.items():
            self.assertEqual(installed[name].version, version, name)

        # pip check alone does not check optional extras. Walk the requested
        # extras as well, including psycopg[binary] and uvicorn[standard].
        queue = []
        files = ['requirements.in', 'build-requirements.in']
        if include_tests:
            files.append('test-requirements.in')
        for file in files:
            queue.extend(Requirement(line) for line in
                         (ROOT / file).read_text(encoding='utf-8').splitlines()
                         if line.strip() and not line.startswith('#'))
        visited = set()
        while queue:
            req = queue.pop()
            name = canonicalize_name(req.name)
            self.assertIn(name, installed)
            self.assertIn(installed[name].version, req.specifier, name)
            for extra in {''} | req.extras:
                if (name, extra) in visited:
                    continue
                visited.add((name, extra))
                for raw in installed[name].requires or ():
                    dep = Requirement(raw)
                    if dep.marker is None or dep.marker.evaluate({'extra': extra}):
                        queue.append(dep)


class PersonalCryptoCompatibilityTests(unittest.TestCase):
    """Real local crypto, without contacting a push provider or saving secrets."""

    def test_webpush_encrypts_decrypts_and_rejects_tampered_content(self):
        import base64
        import http_ece
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from pywebpush import WebPusher

        receiver = ec.generate_private_key(ec.SECP256R1())
        public = receiver.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        auth = os.urandom(16)
        encoded = lambda value: base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')
        pusher = WebPusher({'endpoint': 'https://push.example.test/delivery',
                           'keys': {'p256dh': encoded(public), 'auth': encoded(auth)}})
        payload = 'Nexus notification: 文件已完成'.encode('utf-8')
        ciphertext = pusher.encode(payload)['body']
        self.assertNotIn(payload, ciphertext)
        self.assertEqual(http_ece.decrypt(ciphertext, private_key=receiver,
                                         auth_secret=auth), payload)
        tampered = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
        with self.assertRaises(http_ece.ECEException):
            http_ece.decrypt(tampered, private_key=receiver, auth_secret=auth)
        with self.assertRaises(http_ece.ECEException):
            http_ece.decrypt(ciphertext, private_key=receiver, auth_secret=os.urandom(16))

    def test_vapid_signing_and_fernet_rotation_remain_compatible(self):
        from cryptography.fernet import Fernet, InvalidToken, MultiFernet
        from py_vapid import Vapid

        vapid = Vapid()
        vapid.generate_keys()
        headers = vapid.sign({'aud': 'https://push.example.test',
                              'sub': 'mailto:notifications@example.test'})
        self.assertTrue(Vapid.verify(headers['Authorization']))
        old, new = Fernet(Fernet.generate_key()), Fernet(Fernet.generate_key())
        payload = 'Computer credential: 测试'.encode('utf-8')
        ring = MultiFernet([new, old])
        original = old.encrypt(payload)
        self.assertEqual(ring.decrypt(original), payload)
        rotated = ring.rotate(original)
        self.assertEqual(new.decrypt(rotated), payload)
        with self.assertRaises(InvalidToken):
            old.decrypt(rotated)
