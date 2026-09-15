"""Real Relay TLS startup plus installation isolation/restart tests; no database."""
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
import uuid

from nexus_personal.relay import prepare, settings_for
from nexus_personal import relay as relay_module
from nexus_personal.install import _private_directory


class RelayBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "installation"
        _private_directory(self.root, create=True)
        _private_directory(self.root / 'keys', create=True)
        self.host = dict(state_dir=self.root, instance_id=str(uuid.uuid4()))

    def tearDown(self):
        self.temp.cleanup()

    def test_repeat_preserves_credentials_and_changed_identity_refused(self):
        first = prepare(self.host)
        hashes = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in first['files']}
        self.assertEqual(first, prepare(self.host))
        self.assertEqual(hashes, {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in first['files']})
        with self.assertRaisesRegex(ValueError, 'IDENTITY_CHANGED'):
            prepare(self.host, '192.0.2.1')
        with self.assertRaisesRegex(ValueError, 'MISMATCH'):
            settings_for({**self.host, 'instance_id': str(uuid.uuid4())})

    def test_missing_material_is_not_silently_replaced(self):
        saved = prepare(self.host)
        key = Path(saved['settings']['NEXUS_RELAY_CLIENT_KEY_FILE'])
        key.unlink()
        with self.assertRaises(Exception):
            prepare(self.host)
        self.assertFalse(key.exists())

    def test_native_directory_ipv4_address_and_legacy_receipt(self):
        saved = prepare(self.host, '192.0.2.10')
        endpoint = saved['settings']['NEXUS_RELAY_ENDPOINTS']['relay-local']
        self.assertEqual(endpoint['connect_ipv4'], '192.0.2.10')
        del endpoint['connect_ipv4']
        receipt = self.root / 'relay' / 'settings.json'
        receipt.write_bytes(relay_module._json(saved))
        before = receipt.read_bytes()
        restored = settings_for(self.host)['NEXUS_RELAY_ENDPOINTS']['relay-local']
        self.assertEqual(restored['connect_ipv4'], '192.0.2.10')
        self.assertEqual(receipt.read_bytes(), before)

    def test_partial_directory_refused(self):
        directory = self.root / 'relay'
        _private_directory(directory, create=True)
        (directory / 'unfinished').write_text('test')
        with self.assertRaisesRegex(ValueError, 'INCOMPLETE'):
            prepare(self.host)

    @unittest.skipUnless(shutil.which('node'), 'Node required for real TLS test')
    def test_bundled_runtime_starts_and_enforces_tls_and_auth(self):
        saved = prepare(self.host)
        settings = saved['settings']
        config_path = self.root / 'relay' / 'relay.config.json'
        config = json.loads(config_path.read_text())
        # Dedicated ephemeral ports keep tests away from an existing Relay.
        sockets = [socket.socket(), socket.socket()]
        for sock in sockets:
            sock.bind(('127.0.0.1', 0))
        config['port'] = sockets[0].getsockname()[1]
        config['cloudIngress']['port'] = port = sockets[1].getsockname()[1]
        for sock in sockets:
            sock.close()
        config_path.write_text(json.dumps(config))
        script = Path(relay_module.__file__).parent / 'relay_runtime' / 'nexus-relayd.js'
        child = subprocess.Popen([shutil.which('node'), str(script), '--config', str(config_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 15
            while True:
                if child.poll() is not None:
                    self.fail(child.stderr.read().decode())
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        self.fail('Relay start timeout')
                    time.sleep(.1)
            context = ssl.create_default_context(cafile=settings['NEXUS_RELAY_CA_FILE'])
            connection = http.client.HTTPSConnection('127.0.0.1', port, context=context, timeout=3)
            with self.assertRaises((ssl.SSLError, ConnectionError, OSError)):
                connection.request('GET', '/')
                connection.getresponse()
            connection.close()
            context.load_cert_chain(settings['NEXUS_RELAY_CLIENT_CERT_FILE'], settings['NEXUS_RELAY_CLIENT_KEY_FILE'])
            connection = http.client.HTTPSConnection('127.0.0.1', port, context=context, timeout=3)
            connection.request('POST', '/cloud/invoke/v1', body=b'{}',
                headers={'Content-Type': 'application/vnd.nexus.agent-envelope+json',
                    'x-nexus-source-router': 'nexus-cloud', 'x-nexus-target-router': 'router-test',
                    'x-nexus-target-agent': 'agent://tenant/test', 'x-nexus-source-agent': 'service://nexus-server',
                    'x-nexus-tenant': 'tenant', 'x-nexus-intent': 'test.echo', 'x-nexus-task-id': 'test-task',
                    'x-nexus-forwarding-assertion': 'test', 'authorization': 'Bearer invalid'})
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
            connection.close()
        finally:
            child.terminate()
            child.communicate(timeout=10)
