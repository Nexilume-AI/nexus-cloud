"""Personal Docker sequencing without accessing a daemon or host firewall."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import APIException
from apps.agents import runtime_runner as runner
from apps.agents.network_policy import selected_policy
from nexus_personal.docker_network_policy import BRIDGE_OPTION, PersonalNetworkPolicy
from nexus_personal.host_config import validate_controllers


CONFIG = {'nft_executable': str(Path(sys.executable).resolve()),
          'tcp_endpoints': [{'address': '10.0.0.2', 'port': 8443}], 'dns_servers': []}
LABELS = {'nexus.managed': 'agent', 'nexus.agent.host': 'test-worker', 'nexus.agent.runtime': 'runtime1'}
NETWORK = 'nexus-agent-runtime1-g1-net'
FACTORY = 'nexus_personal.docker_network_policy.from_settings'


class NetworkPolicyValidationTests(SimpleTestCase):
    def backend(self, host='test-worker'):
        return PersonalNetworkPolicy(host_id=host, configuration=CONFIG)

    def test_prefix_covers_future_generations_without_overwriting_other_instances(self):
        first, other = self.backend(), self.backend('other-worker')
        self.assertNotEqual(first.table, other.table)
        self.assertNotEqual(first.prefix, other.prefix)
        for network in (NETWORK, 'nexus-agent-runtime1-g2-net'):
            name = first.bridge_name(network)
            self.assertEqual(len(name), 15)
            self.assertTrue(name.startswith(first.prefix))
        self.assertNotEqual(first.bridge_name(NETWORK), first.bridge_name('nexus-agent-runtime1-g2-net'))
        self.assertIn(first.prefix + '*', first.rules)
        self.assertIn('oifname { "nx*" }', first.rules)
        self.assertNotIn(other.table, first.rules)

    def test_foreign_labels_and_cross_runtime_names_never_apply_rules(self):
        backend = self.backend()
        for labels in ({**LABELS, 'nexus.agent.host': 'other'}, {**LABELS, 'nexus.agent.runtime': 'runtime2'}):
            with self.subTest(labels=labels), patch.object(backend, 'apply') as apply:
                with self.assertRaises(ValueError):
                    backend.prepare(NETWORK, labels)
                apply.assert_not_called()

    def test_unsupported_container_controller_fails_closed(self):
        with self.assertRaisesMessage(ValueError, 'EGRESS_CONTROLLER_TOPOLOGY_UNSUPPORTED'):
            PersonalNetworkPolicy(host_id='test-worker', configuration=CONFIG, controller_container='proxy')

    def test_internal_mode_is_cross_platform_and_requires_internal_bridge(self):
        backend = PersonalNetworkPolicy(host_id='test-worker', configuration={'mode': 'internal'})
        with patch.object(backend, 'apply') as apply:
            self.assertEqual(backend.prepare(NETWORK, LABELS), ['--internal'])
            apply.assert_not_called()
        backend.validate_network({'Name': NETWORK, 'Driver': 'bridge', 'Internal': True,
            'Labels': dict(LABELS)}, NETWORK, LABELS)
        for info in ({'Name': NETWORK, 'Driver': 'bridge', 'Internal': False, 'Labels': LABELS},
                     {'Name': NETWORK, 'Driver': 'bridge', 'Internal': True, 'Labels': {**LABELS, 'nexus.agent.host': 'other'}}):
            with self.subTest(info=info), self.assertRaises(RuntimeError):
                backend.validate_network(info, NETWORK, LABELS)

    def test_container_must_use_exact_managed_network_and_manual_restart(self):
        backend = self.backend()
        valid = {'HostConfig': {'RestartPolicy': {'Name': 'no'}, 'NetworkMode': NETWORK},
                 'NetworkSettings': {'Networks': {NETWORK: {}}}}
        backend.validate_container(valid, NETWORK)
        invalid = [None, {}, {'HostConfig': None},
                   {**valid, 'HostConfig': {'RestartPolicy': {'Name': 'always'}, 'NetworkMode': NETWORK}},
                   {**valid, 'HostConfig': {'RestartPolicy': {'Name': 'no'}, 'NetworkMode': 'host'}},
                   {**valid, 'NetworkSettings': {'Networks': {NETWORK: {}, 'bridge': {}}}},
                   {**valid, 'NetworkSettings': {'Networks': {}}}]
        for info in invalid:
            with self.subTest(info=info), self.assertRaises(RuntimeError):
                backend.validate_container(info, NETWORK)

    @override_settings(NEXUS_AGENT_NETWORK_POLICY_FACTORY='')
    def test_legacy_mode_does_not_load_personal_policy(self):
        with patch('apps.agents.network_policy.import_string') as load:
            self.assertIsNone(selected_policy())
            load.assert_not_called()

    def test_optional_host_configuration_preserves_legacy_and_validates_policy(self):
        with tempfile.TemporaryDirectory() as root:
            host = {'instance_id': 'test-worker', 'state_dir': root, 'secret_key': 'test-only-host',
                    'encryption_key': 'test-only-encryption'}
            fields = {'port': 43101, 'token': 'synthetic-controller-only-0123456789abcdefghABCDEFGH',
                      'image_admission_command': [str(Path(sys.executable).resolve())], 'egress_policy_ready': False}
            self.assertNotIn('network_policy', validate_controllers({'agent': fields}, host)['agent'])
            configured = {**fields, 'network_policy': CONFIG}
            self.assertEqual(validate_controllers({'agent': configured}, host)['agent']['network_policy'], CONFIG)
            internal = {**fields, 'network_policy': {'mode': 'internal'}}
            self.assertEqual(validate_controllers({'agent': internal}, host)['agent']['network_policy'], {'mode': 'internal'})
            for policy in (None, {}, {**CONFIG, 'nft_executable': 'relative-nft'},
                           {**CONFIG, 'tcp_endpoints': [{'address': '169.254.169.254', 'port': 80}]}):
                with self.subTest(policy=policy), self.assertRaises(ImproperlyConfigured):
                    validate_controllers({'agent': {**fields, 'network_policy': policy}}, host)


@override_settings(NEXUS_PRODUCTION=False, NEXUS_AGENT_RUNTIME_HOST_ID='test-worker',
    NEXUS_AGENT_NETWORK_POLICY_FACTORY=FACTORY, NEXUS_PERSONAL_NETWORK_POLICY=CONFIG,
    NEXUS_AGENT_RUNTIME_CONTROLLER_CONTAINER='', NEXUS_AGENT_IMAGE_ADMISSION_COMMAND=[])
class NetworkPolicyLifecycleTests(SimpleTestCase):
    def setUp(self):
        self.events = []
        self.backend = PersonalNetworkPolicy(host_id='test-worker', configuration=CONFIG)
        self.network = None
        self.existing = None
        self.digest = 'sha256:' + 'a' * 64
        self.deployment = SimpleNamespace(id='runtime1', tenant_id='tenant', project_id=None, agent_id='agent',
            docker_lifecycle={'generation': 'g1', 'host_id': 'test-worker'},
            image=SimpleNamespace(version=None, image_ref='example/agent:test', image_digest=self.digest),
            agent=SimpleNamespace(resource_config=None), container_id='container1',
            internal_mcp_url='http://127.0.0.1:49999/mcp')

    def inspect(self, kind, name):
        if kind == 'network':
            return self.network
        if kind == 'container':
            return self.existing
        return None

    def docker(self, args, **kwargs):
        if args[1:3] == ['network', 'create']:
            self.events.append('network-create')
            self.assertIn(BRIDGE_OPTION + '=' + self.backend.bridge_name(NETWORK), args)
            self.network = {'Name': NETWORK, 'Driver': 'bridge', 'Options': {
                BRIDGE_OPTION: self.backend.bridge_name(NETWORK)}, 'Labels': dict(LABELS)}
            return 'network-id'
        if args[1] == 'run':
            self.events.append('container-run')
            self.assertEqual(args[args.index('--restart') + 1], 'no')
            self.existing = self.container_info()
        if args[1] == 'start':
            self.events.append('container-start')
        return 'container1'

    def container_info(self):
        return {'Id': 'container1', 'Image': self.digest, 'State': {'Running': True},
            'Config': {'Labels': LABELS},
            'HostConfig': {'RestartPolicy': {'Name': 'no'}, 'NetworkMode': NETWORK},
            'NetworkSettings': {'Networks': {NETWORK: {}},
                'Ports': {'8000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '49999'}]}}}

    def start(self, *, fail_policy=False):
        def apply(_):
            self.events.append('policy-applied')
            if fail_policy:
                raise RuntimeError('private-policy-detail')
        with patch.object(PersonalNetworkPolicy, 'apply', apply), \
             patch.object(runner, '_docker_inspect', side_effect=self.inspect), \
             patch.object(runner, '_run_docker', side_effect=self.docker), \
             patch.object(runner, '_docker_host_port', return_value=49999), \
             patch.object(runner, '_wait_for_mcp', return_value=True):
            return runner.DockerAgentRuntimeRunner().start(deployment=self.deployment)

    def test_policy_precedes_network_and_agent_creation(self):
        result = self.start()
        self.assertEqual(result.container_id, 'container1')
        self.assertEqual(self.events, ['policy-applied', 'network-create', 'container-run'])

    def test_failed_policy_prevents_any_docker_mutation_and_hides_details(self):
        with self.assertRaises(APIException) as error:
            self.start(fail_policy=True)
        self.assertNotIn('private-policy-detail', str(error.exception))
        self.assertEqual(self.events, ['policy-applied'])

    def test_recovery_reinstalls_policy_before_starting_stopped_generation(self):
        self.start()
        self.events.clear()
        self.existing = self.container_info()
        self.existing['State']['Running'] = False
        self.start()
        self.assertEqual(self.events, ['policy-applied', 'container-start'])

    def test_old_auto_restart_container_is_not_adopted(self):
        self.existing = {'Id': 'container1', 'Image': self.digest, 'State': {'Running': False},
            'Config': {'Labels': LABELS}, 'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'}}}
        with self.assertRaises(APIException):
            self.start()
        self.assertEqual(self.events, ['policy-applied'])

    def test_existing_wrong_bridge_requires_redeployment(self):
        self.network = {'Name': NETWORK, 'Driver': 'bridge', 'Options': {}, 'Labels': LABELS}
        with self.assertRaises(APIException):
            self.start()
        self.assertEqual(self.events, ['policy-applied'])

    def test_bad_new_bridge_is_cleaned_without_running_workload(self):
        original = self.docker
        def create_wrong_bridge(args, **kwargs):
            result = original(args, **kwargs)
            if args[1:3] == ['network', 'create']:
                self.network['Options'] = {}
            return result
        with patch.object(self, 'docker', side_effect=create_wrong_bridge), \
             patch.object(runner, '_docker_remove_network') as remove:
            with self.assertRaises(APIException):
                self.start()
        remove.assert_called_once_with(NETWORK, LABELS)
        self.assertNotIn('container-run', self.events)

    def test_wrong_container_network_after_creation_is_stopped_and_cleaned(self):
        original = self.docker
        def attach_extra_network(args, **kwargs):
            result = original(args, **kwargs)
            if args[1] == 'run':
                self.existing['NetworkSettings']['Networks']['bridge'] = {}
            return result
        with patch.object(self, 'docker', side_effect=attach_extra_network), \
             patch.object(runner, '_docker_stop') as stop, \
             patch.object(runner, '_docker_remove_network') as remove, \
             patch.object(runner, '_docker_runtime_diagnostics', return_value={}):
            with self.assertRaises(APIException):
                self.start()
        stop.assert_called_once_with('container1', expected=LABELS)
        remove.assert_called_once_with(NETWORK, LABELS)

    def test_health_reinstalls_policy_and_stop_does_not_require_it(self):
        self.start()
        self.events.clear()
        self.existing = self.container_info()
        with patch.object(PersonalNetworkPolicy, 'apply') as apply, \
             patch.object(runner, '_docker_inspect', side_effect=self.inspect), \
             patch.object(runner, '_wait_for_mcp', return_value=True):
            self.assertTrue(runner.DockerAgentRuntimeRunner().health_check(deployment=self.deployment))
            apply.assert_called_once()
        with patch.object(PersonalNetworkPolicy, 'apply', side_effect=RuntimeError('unavailable')) as apply, \
             patch.object(runner, '_docker_stop') as stop:
            runner.DockerAgentRuntimeRunner().stop(deployment=self.deployment)
            stop.assert_called_once()
            apply.assert_not_called()
