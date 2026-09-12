"""Real HTTP/options/permission checks; remote file I/O is in the SDK bridge."""
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import exceptions
from apps.workspaces.models import ComputerRuntimeCommand
from nexus_personal.models import PersonalRouterCredential
from nexus_personal import tool_setup
from .provider_http_fixture import ProviderHTTPFixture
from . import test_tool_profiles as profiles


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalToolInspectionTests(ProviderHTTPFixture, TestCase):
    source = profiles.PersonalToolProfileTests.source
    runtime = profiles.PersonalToolProfileTests.runtime
    post = profiles.PersonalToolProfileTests.post
    setUp = profiles.PersonalToolProfileTests.setUp

    def base(self):
        return f"/api/v1/workspace-terminal-sessions/{self.session.pk}/tool-config/"

    def test_options_show_actual_router_without_issuing_or_dispatching(self):
        result = self.client.get(self.base() + "options/")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['routers'][0]['id'], str(self.router.pk))
        self.assertTrue(result.data['routers'][0]['available'])
        self.assertIn(self.model, result.data['routers'][0]['models'])
        self.assertEqual(result.data['routers'][0]['model'], self.model)
        self.assertEqual(result.data['routers'][0]['router_type'], self.router.router_type)
        self.assertEqual(result.data['routers'][0]['strategy'], self.router.strategy)
        self.assertEqual(result.data['runtimes'], [])
        self.assertFalse(result.data['write_availability']['available'])
        self.assertTrue(result.data['agents_available'])
        self.assertEqual(result.data['agents'],[])
        self.assertFalse(PersonalRouterCredential.objects.exists())
        self.assertFalse(ComputerRuntimeCommand.objects.exists())

    def test_unimplemented_mutation_rejected_without_remote_write_or_new_key(self):
        for suffix, data, code, message in [
            ('rollback/', {}, tool_setup.PENDING_CODE, tool_setup.PENDING_MESSAGE),
            ('preview/', {'section': 'unsupported'}, 'VALIDATION_ERROR', 'section'),
        ]:
            result = self.post(self.base() + suffix, data)
            self.assertEqual(result.status_code, 400, result.data)
            self.assertIs(result.data['ok'], False)
            self.assertEqual(result.data['error']['code'], code)
            if suffix == 'rollback/':
                self.assertEqual(result.data['error']['message'], message)
            else:
                self.assertIn(message, result.data['error']['message'])
        self.assertFalse(PersonalRouterCredential.objects.exists())
        self.assertFalse(ComputerRuntimeCommand.objects.exists())

    def test_legacy_runtime_preview_requires_explicit_cas_capability(self):
        self.device.last_seen_at = timezone.now()
        self.device.capabilities = {'tool_setup.v1': 1}
        self.device.save()
        result = self.post(self.base() + 'preview/', {'section':'api', 'action':'set_router', 'router_id':str(self.router.pk)})
        self.assertEqual(result.status_code, 409, result.data)
        self.assertIn('COMPUTER_CAPABILITY_UNAVAILABLE', str(result.data))
        self.assertFalse(ComputerRuntimeCommand.objects.exists())

    def test_foreign_caller_cannot_inspect_even_before_computer_dispatch(self):
        with self.assertRaises(exceptions.APIException):
            tool_setup.get_workspace_tool_config(request=self.request(self.other), session_id=self.session.pk)
        self.assertFalse(ComputerRuntimeCommand.objects.exists())

    def test_url_preview_never_returns_credentials_in_unknown_path_or_query(self):
        self.assertEqual(tool_setup._public_url('https://user:pass@example.test:9443/private-secret?key=secret#secret'),
                         'https://example.test:9443')
        self.assertEqual(tool_setup._public_url('file:///private'), '')
        self.assertEqual(tool_setup._public_url('https://[::1]:broken/path'), '')
