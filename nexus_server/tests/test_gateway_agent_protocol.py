"""Database-free protocol regressions; never presented as live provider evidence."""
from types import SimpleNamespace
from django.test import SimpleTestCase, override_settings
from unittest.mock import patch
from datetime import timedelta
from django.utils import timezone
from apps.gateway.serializers import ChatCompletionRequestSerializer
from apps.gateway.provider_adapters import OpenAICompatibleAdapter


class AgentProtocolTests(SimpleTestCase):
    def test_database_outage_is_retryable_without_leaking_connection_details(self):
        from django.db import OperationalError
        from apps.gateway.openai_compat import OpenAIChatCompletionsView, OpenAIResponsesView, OpenAIModelsView
        for view_type in (OpenAIChatCompletionsView, OpenAIResponsesView, OpenAIModelsView):
            response=view_type().handle_exception(OperationalError('sensitive connection detail'))
            self.assertEqual(response.status_code,503)
            self.assertEqual(response.data['error']['code'],'DATABASE_UNAVAILABLE')
            self.assertEqual(response['Retry-After'],'2')
            self.assertNotIn('sensitive',str(response.data))

    def test_validation_errors_remain_nonretryable(self):
        from rest_framework.exceptions import ValidationError
        from apps.gateway.openai_compat import OpenAIChatCompletionsView
        response=OpenAIChatCompletionsView().handle_exception(ValidationError('Invalid tool message'))
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.data['error']['type'],'invalid_request_error')

    def test_inconclusive_probe_preserves_recent_real_model_success(self):
        from apps.providers.runtime_services import _apply_model_probe
        now=timezone.now()
        offer=SimpleNamespace(pk='exact-offer',metadata={},health_status='healthy',last_health_check_at=now-timedelta(minutes=10))
        with patch('apps.providers.runtime_services._recent_model_success_at',return_value=now-timedelta(seconds=20)):
            _apply_model_probe(offer,(None,'Probe timed out.'),now)
        self.assertEqual(offer.health_status,'degraded')
        self.assertIn('recent successful request',offer.health_reason)

    def test_long_idle_preserves_degraded_state_without_claiming_fresh_health(self):
        from apps.providers.runtime_services import _apply_model_probe
        now=timezone.now()
        verified=now-timedelta(days=30)
        offer=SimpleNamespace(pk='exact-offer',metadata={'health_probe':{'last_definitive_status':'healthy','last_definitive_at':verified.isoformat()}},health_status='unknown',last_health_check_at=now-timedelta(minutes=10))
        with patch('apps.providers.runtime_services._recent_model_success_at',return_value=None):
            _apply_model_probe(offer,(None,'Probe timed out.'),now)
        self.assertEqual(offer.health_status,'degraded')
        self.assertEqual(offer.metadata['health_probe']['last_definitive_at'],verified.isoformat())
        self.assertEqual(offer.metadata['health_probe']['state'],'pending')

    def test_never_verified_offer_is_not_promoted_after_inconclusive_probe(self):
        from apps.providers.runtime_services import _apply_model_probe
        now=timezone.now()
        offer=SimpleNamespace(pk='new-offer',metadata={},health_status='unknown',last_health_check_at=None)
        with patch('apps.providers.runtime_services._recent_model_success_at',return_value=None):
            _apply_model_probe(offer,(None,'Probe timed out.'),now)
        self.assertEqual(offer.health_status,'unknown')

    def test_definitive_failure_is_not_overridden_by_success_history(self):
        from apps.providers.runtime_services import _apply_model_probe
        now=timezone.now()
        offer=SimpleNamespace(pk='exact-offer',metadata={},health_status='healthy',last_health_check_at=now)
        with patch('apps.providers.runtime_services._recent_model_success_at') as lookup:
            _apply_model_probe(offer,(False,'HTTP 401'),now)
        lookup.assert_not_called()
        self.assertEqual(offer.health_status,'unhealthy')
        # A later inconclusive probe cannot use a success older than the known
        # failure to re-enable the model.
        with patch('apps.providers.runtime_services._recent_model_success_at',return_value=now-timedelta(seconds=20)):
            _apply_model_probe(offer,(None,'Probe timed out.'),now+timedelta(seconds=1))
        self.assertEqual(offer.health_status,'unknown')
        self.assertEqual(offer.metadata['health_probe']['last_definitive_status'],'unhealthy')

    @override_settings(NEXUS_PROVIDER_MODEL_PROBE_ALLOWLIST={'api.siliconflow.cn':['Qwen/Qwen3-8B']})
    def test_catalog_probe_does_not_call_unselected_paid_model(self):
        from apps.providers.runtime_services import probe_runtime_model
        with patch('apps.providers.runtime_services.runtime_api_key') as key, patch('apps.providers.runtime_services.urlopen') as post:
            healthy,reason=probe_runtime_model(runtime=SimpleNamespace(internal_api_url='https://api.siliconflow.cn/v1'),upstream_model_id='paid-model')
        self.assertIsNone(healthy)
        self.assertIn('unverified',reason)
        key.assert_not_called();post.assert_not_called()

    def payload(self):
        return {
            'model':'paper-pool', 'enable_thinking':False, 'seed':17,
            'tools':[{'type':'function','function':{'name':'lookup','parameters':{'type':'object','properties':{'id':{'type':'string'}}}}}],
            'tool_choice':'auto',
            'messages':[
                {'role':'user','content':'Look up 123'},
                {'role':'assistant','content':None,'tool_calls':[{'id':'call_1','type':'function','function':{'name':'lookup','arguments':'{"id":"123"}'}}]},
                {'role':'tool','tool_call_id':'call_1','content':'{"found":true}'},
            ],
        }

    def test_round_trip_reaches_provider_without_losing_tool_protocol(self):
        data=self.payload();s=ChatCompletionRequestSerializer(data=data);self.assertTrue(s.is_valid(),s.errors)
        upstream=OpenAICompatibleAdapter().chat_payload(deployment=SimpleNamespace(model='Qwen/Qwen3-8B'),payload=s.validated_data)
        for field in ['messages','tools','tool_choice','enable_thinking','seed']:
            self.assertEqual(upstream[field],data[field])
        self.assertEqual(upstream['model'],'Qwen/Qwen3-8B')

    def test_empty_tool_call_content_is_accepted(self):
        data=self.payload();data['messages'][1]['content']=''
        s=ChatCompletionRequestSerializer(data=data);self.assertTrue(s.is_valid(),s.errors)

    def test_bounded_reasoning_reaches_provider(self):
        data=self.payload();data.update(enable_thinking=True,thinking_budget=1024)
        serializer=ChatCompletionRequestSerializer(data=data)
        self.assertTrue(serializer.is_valid(),serializer.errors)
        upstream=OpenAICompatibleAdapter().chat_payload(deployment=SimpleNamespace(model='Qwen/Qwen3-8B'),payload=serializer.validated_data)
        self.assertEqual(upstream['thinking_budget'],1024)
        for invalid in [0,32769]:
            data['thinking_budget']=invalid
            self.assertFalse(ChatCompletionRequestSerializer(data=data).is_valid())

    def test_ordinary_assistant_message_accepts_absent_tool_calls(self):
        # The live retail runner serializes ordinary assistant messages this way.
        for calls in (None, []):
            with self.subTest(tool_calls=calls):
                data=self.payload()
                data['messages']=[
                    {'role':'system','content':'Assist the retail customer.'},
                    {'role':'assistant','content':'How can I help you?','tool_calls':calls},
                    {'role':'user','content':'Please check my order.'},
                ]
                s=ChatCompletionRequestSerializer(data=data)
                self.assertTrue(s.is_valid(),s.errors)
                upstream=OpenAICompatibleAdapter().chat_payload(deployment=SimpleNamespace(model='Qwen/Qwen3-8B'),payload=s.validated_data)
                self.assertEqual(upstream['messages'],data['messages'])

    def test_invalid_tool_result_rejected(self):
        data=self.payload();del data['messages'][2]['tool_call_id']
        self.assertFalse(ChatCompletionRequestSerializer(data=data).is_valid())

    def test_null_user_content_rejected(self):
        data=self.payload();data['messages'][0]['content']=None
        self.assertFalse(ChatCompletionRequestSerializer(data=data).is_valid())

    def test_nonassistant_tool_call_rejected(self):
        data=self.payload();data['messages'][1]['role']='user'
        self.assertFalse(ChatCompletionRequestSerializer(data=data).is_valid())

    def test_invalid_tool_choice_rejected(self):
        data=self.payload();data['tool_choice']={'function':'lookup'}
        self.assertFalse(ChatCompletionRequestSerializer(data=data).is_valid())
