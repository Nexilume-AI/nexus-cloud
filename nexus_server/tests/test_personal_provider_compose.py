"""Render real Compose merges without starting a daemon or touching host state."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('docker'), 'Docker CLI with Compose required for configuration rendering')
class ProviderComposeTests(unittest.TestCase):
    def config(self, overlay=False):
        root = Path(__file__).resolve().parents[2]
        args = ['docker', 'compose', '-f', 'deploy/community/compose.yaml']
        if overlay:
            args.extend(['-f', 'deploy/community/compose.providers.yaml'])
        result = subprocess.run([*args, 'config', '--format', 'json'], cwd=root, capture_output=True,
            timeout=20, env={**os.environ, 'NEXUS_PROVIDER_DATA': '/tmp/nexus-provider-test-data',
                'NEXUS_PROVIDER_RELEASES': '/tmp/nexus-provider-test-releases'})
        self.assertEqual(result.returncode, 0, 'Compose must render without starting services')
        return json.loads(result.stdout)

    def test_default_cloud_never_receives_docker_control(self):
        config = self.config()
        self.assertNotIn('provider-controller', config['services'])
        for service in config['services'].values():
            self.assertFalse(any(item['target'] == '/var/run/docker.sock' for item in service.get('volumes', [])))

    def test_opt_in_merge_keeps_secrets_volumes_and_isolates_docker_control(self):
        config = self.config(overlay=True)
        socket_holders = []
        for name, service in config['services'].items():
            mounts = {item['target']: item for item in service.get('volumes', [])}
            if '/var/run/docker.sock' in mounts:
                socket_holders.append(name)
            if name in {'prepare', 'initialize', 'provider-setup', 'provider-controller', 'web', 'worker', 'agent-worker', 'beat', 'relay'}:
                self.assertIn('/var/lib/nexus', mounts, name)
                self.assertTrue(mounts['/opt/nexus/provider-releases']['read_only'], name)
            if name in {'web', 'worker', 'agent-worker', 'beat'}:
                self.assertEqual(service['depends_on']['provider-controller']['condition'], 'service_healthy')
                self.assertIn('initialize', service['depends_on'])
                self.assertIn('NEXUS_ORIGIN', service['environment'])
                self.assertTrue(service['read_only'])
        self.assertEqual(sorted(socket_holders), ['provider-controller', 'provider-setup'])
        self.assertEqual(config['services']['provider-setup']['depends_on']['initialize']['condition'], 'service_completed_successfully')
        for service in ('provider-setup', 'provider-controller'):
            environment = config['services'][service]['environment']
            self.assertEqual(environment['NEXUS_PROVIDER_COMPOSE_PROJECT'], config['name'])
            self.assertEqual(environment['NEXUS_PROVIDER_COMPOSE_SERVICE'], service)

    def test_controller_build_never_sends_repository_files_as_context(self):
        root = Path(__file__).resolve().parents[2]
        patterns = (root / 'deploy/community/Dockerfile.provider.dockerignore').read_text().splitlines()
        self.assertEqual([line for line in patterns if line and not line.startswith('#')], ['**'])
