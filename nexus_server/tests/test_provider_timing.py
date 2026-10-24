import io
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from apps.gateway.provider_adapters import OpenAICompatibleAdapter


class ProviderTimingTests(SimpleTestCase):
    @override_settings(NEXUS_GATEWAY_MOCK_HOSTS=set(), NEXUS_GATEWAY_FAIL_HOSTS=set())
    def test_first_text_and_total_are_numeric_without_content(self):
        deployment=SimpleNamespace(provider_account=SimpleNamespace(encrypted_key=''),endpoint='https://provider.example.test/v1',provider_runtime=None,model='test')
        body=b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\ndata: {"choices":[{"delta":{"content":"private-text"}}]}\n\ndata: [DONE]\n\n'
        with patch('apps.gateway.provider_adapters.open_provider_url') as opener:
            opener.return_value.__enter__.return_value=io.BytesIO(body)
            list(OpenAICompatibleAdapter().stream_chat_completions(deployment=deployment,payload={'messages':[]}))
        timing=deployment._nexus_latency
        self.assertLessEqual(timing['first_event_ms'],timing['first_text_ms'])
        self.assertLessEqual(timing['last_text_ms'],timing['provider_total_ms'])
        self.assertTrue(all(type(v) in (int,float) for v in timing.values()))
        self.assertNotIn('private-text',str(timing))
