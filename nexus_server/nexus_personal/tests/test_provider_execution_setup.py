from unittest.mock import patch
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.providers.models import ProviderRuntimeAccount
from .provider_connection_fixture import PersonalProviderConnectionFixture


class PersonalProviderExecutionSetupTests(PersonalProviderConnectionFixture, TestCase):
    @override_settings(NEXUS_PROVIDER_RUNTIME_RUNNER='controller', NEXUS_PROVIDER_RUNTIME_CONTROLLER_TOKEN='')
    def test_setup_endpoint_is_owner_only_and_has_no_sensitive_details(self):
        path = '/api/v1/provider-connections/execution-setup/'
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertTrue(response.json()['engines']['direct_api']['available'])
        self.assertEqual(response.json()['engines']['codex_proxy']['code'], 'PROVIDER_CONTROLLER_UNCONFIGURED')
        for engine in response.json()['engines'].values():
            self.assertEqual(set(engine), {'available', 'code', 'message'})
        self.assertIn(APIClient().get(path).status_code, (401, 403))
        self.assertIn(self.client.get(path, HTTP_X_NEXUS_TENANT='foreign').status_code, (400, 403, 404))

    def test_unconfigured_start_is_rejected_before_changing_runtime_state(self):
        connection = self.create_connection(engine='codex_proxy', upstream='openai')
        runtime = ProviderRuntimeAccount.objects.get(source_provider_account_id=connection['id'])
        previous = runtime.status
        with override_settings(NEXUS_PRODUCTION=True, NEXUS_PROVIDER_RUNTIME_RUNNER='controller', NEXUS_PROVIDER_RUNTIME_CONTROLLER_TOKEN=''), \
                patch('apps.providers.runtime_services.get_provider_runtime_runner') as runner:
            response = self.request('post', f"/api/v1/provider-connections/{connection['id']}/start/")
        self.assertEqual(response.status_code, 503, response.content)
        self.assertIn('PROVIDER_CONTROLLER_UNCONFIGURED', response.content.decode())
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, previous)
        runner.assert_not_called()
