from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import APIException

from apps.providers import execution_setup as setup
from apps.providers.provider_controller import ProviderRuntimeControllerError, dispatch_provider_runtime_command


class ProviderExecutionSetupTests(SimpleTestCase):
    def test_missing_cli_does_not_inspect_images(self):
        with patch.object(setup.shutil, 'which', return_value=None), patch.object(setup, 'approved_release') as approval:
            values = setup.inspect_execution()
        self.assertTrue(all(value['code'] == 'PROVIDER_DOCKER_CLI_MISSING' for value in values.values()))
        approval.assert_not_called()

    def test_daemon_errors_are_bounded_and_redacted(self):
        with patch.object(setup.shutil, 'which', return_value='docker'), patch.object(setup.subprocess, 'run',
                return_value=SimpleNamespace(returncode=1, stdout=b'', stderr=b'secret host path')) as run:
            values = setup.inspect_execution()
        self.assertNotIn('secret', str(values))
        self.assertEqual(values['codex_proxy']['code'], 'PROVIDER_DOCKER_UNAVAILABLE')
        self.assertEqual(run.call_args.kwargs['timeout'], 3)
        self.assertEqual(run.call_args.args[0][1], 'version')

    def test_engine_releases_are_checked_independently_without_pull_or_build(self):
        with patch.object(setup.shutil, 'which', return_value='docker'), patch.object(setup.subprocess, 'run',
                return_value=SimpleNamespace(returncode=0, stdout=b'linux\n')), \
                patch.object(setup, 'approved_release', side_effect=[{'image_id': 'approved'}, APIException('secret')]) as approval, \
                patch.object(setup, 'verify_image') as verify:
            values = setup.inspect_execution(release_dir='/operator/releases', required=True)
        self.assertTrue(values['codex_proxy']['available'])
        self.assertEqual(values['cliproxyapi']['code'], 'PROVIDER_RELEASE_REQUIRED')
        approval.assert_any_call('codex_proxy', directory='/operator/releases', required=True)
        verify.assert_called_once_with({'image_id': 'approved'}, timeout=2)

    def test_wrong_image_is_unavailable_not_a_successful_ping(self):
        with patch.object(setup.shutil, 'which', return_value='docker'), patch.object(setup.subprocess, 'run',
                return_value=SimpleNamespace(returncode=0, stdout=b'linux')), \
                patch.object(setup, 'approved_release', return_value={"image_id": "wrong"}), \
                patch.object(setup, 'verify_image', side_effect=APIException('sensitive diagnostic')):
            values = setup.inspect_execution()
        self.assertEqual(values['codex_proxy']['code'], 'PROVIDER_IMAGE_UNAVAILABLE')
        self.assertNotIn('sensitive', str(values))

    @override_settings(NEXUS_PRODUCTION=True, NEXUS_PROVIDER_RUNTIME_RUNNER='controller', NEXUS_PROVIDER_RUNTIME_CONTROLLER_TOKEN='')
    def test_direct_api_remains_available_without_docker_or_controller(self):
        with patch.object(setup, 'inspect_execution', side_effect=AssertionError('Must not probe local Docker')):
            values = setup.execution_setup()['engines']
            setup.require_execution('direct_api')
            with self.assertRaises(APIException) as caught:
                setup.require_execution('codex_proxy')
        self.assertEqual(caught.exception.status_code, 503)
        self.assertTrue(values['direct_api']['available'])
        self.assertEqual(values['codex_proxy']['code'], 'PROVIDER_CONTROLLER_UNCONFIGURED')

    @override_settings(NEXUS_PRODUCTION=True, NEXUS_PROVIDER_RUNTIME_RUNNER='controller', NEXUS_PROVIDER_RUNTIME_CONTROLLER_TOKEN='fixture', NEXUS_PROVIDER_RUNTIME_CONTROLLER_SOCKET='tcp://127.0.0.1:43102')
    def test_controller_failure_and_unrecognized_response_fail_closed(self):
        for response in (None, {'engines': []}, {'engines': {engine: {'code': 'private path'} for engine in setup.ENGINES}}):
            with self.subTest(response=response), patch('apps.providers.provider_controller.ProviderRuntimeControllerClient.request', return_value=response) as request:
                values = setup.execution_setup()['engines']
                self.assertEqual(values['codex_proxy']['code'], 'PROVIDER_CONTROLLER_UNAVAILABLE')
                request.assert_called_once_with(action='execution_setup', timeout=8)
        with patch('apps.providers.provider_controller.ProviderRuntimeControllerClient.request', side_effect=ProviderRuntimeControllerError('token-private')):
            self.assertNotIn('token-private', str(setup.execution_setup()))

    def test_controller_dispatches_read_only_preflight_without_runtime_lookup(self):
        with patch.object(setup, 'inspect_execution', return_value={'codex_proxy': setup.state()}) as inspect:
            result = dispatch_provider_runtime_command({'action': 'execution_setup'})
        self.assertTrue(result['engines']['codex_proxy']['available'])
        inspect.assert_called_once_with()
