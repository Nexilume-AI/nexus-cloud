import copy
import json
from types import SimpleNamespace as NS
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from apps.gateway.request_tokens import RequestTokens, encoding, request_options
from apps.routers.auto_eligibility import inspect_candidate, AutoCandidatesUnavailable


class AutoCapacityTests(SimpleTestCase):
    def setUp(self):
        self.source = NS(upstream_model_id='deepseek-v4-pro')
        self.payload = {'model':'auto','messages':[{'role':'user','content':'hello '*183334}], 'max_tokens':1024}
        self.fields = {'context_window':{'value':1048576}, 'max_output_tokens':{'value':393216}}
        self.contract = {'operations':['chat.completions','responses'], 'input_modalities':['text','image'], 'output_modalities':['text']}
        for target,value in [('source_fields',self.fields),('deployment_contract',self.contract)]:
            patcher=patch('apps.routers.auto_eligibility.'+target,return_value=value)
            patcher.start();self.addCleanup(patcher.stop)

    def test_megabyte_input_is_tokens_not_bytes_and_tokenizers_are_offline(self):
        with patch('socket.create_connection',side_effect=AssertionError('No network')):
            encoding.cache_clear()
            for name in ('o200k_base','cl100k_base'):
                self.assertGreater(len(encoding(name).encode_ordinary('中文 and English')),0)
            result=inspect_candidate(self.source,self.payload,{})
        self.assertTrue(result['eligible'])
        self.assertGreater(len(json.dumps(self.payload).encode()),1048576)
        self.assertLess(result['capacity']['input_tokens'],200000)
        self.assertEqual(result['capacity']['tokenizer_match'],'reference')
        tokens=RequestTokens(self.payload)
        with patch('apps.gateway.request_tokens.encoding',wraps=encoding) as load:
            tokens.estimate(self.source);tokens.estimate(self.source)
            self.assertEqual(load.call_count,1)

    def test_limits_and_output_budget_have_precise_diagnostics(self):
        self.fields.update(context_window={'value':1000},max_input_tokens={'value':900},max_output_tokens={'value':512})
        result=inspect_candidate(self.source,self.payload,{})
        self.assertFalse(result['eligible'])
        self.assertEqual([r['code'] for r in result['reasons']],['context_window_exceeded','max_input_tokens_exceeded','max_output_tokens_exceeded'])
        self.assertEqual(result['reasons'][-1],{'code':'max_output_tokens_exceeded','required_tokens':1024,'limit_tokens':512})
        self.fields.clear()  # Unknown capacities do not become zero.
        self.assertTrue(inspect_candidate(self.source,self.payload,{})['eligible'])

    def test_chinese_tools_native_responses_and_reasoning_are_included(self):
        from apps.gateway.responses_native import gateway_payload
        native={'model':'auto','input':[{'type':'function_call_output','call_id':'c','output':'中文结果 '*1000}],
                'instructions':'Analyze results','tools':[{'type':'function','name':'large','parameters':{'description':'large schema '*2000}}],
                'max_output_tokens':128,'reasoning':{'effort':'max'}}
        payload=gateway_payload(native);before=copy.deepcopy(payload)
        self.fields.update(supports_function_calling={'value':False},reasoning_efforts={'value':['low']})
        result=inspect_candidate(self.source,payload,{})
        self.assertGreater(result['capacity']['input_tokens'],5000)
        self.assertEqual(result['capacity']['output_tokens'],128)
        self.assertEqual([r['code'] for r in result['reasons']],['tools_unsupported','reasoning_effort_unsupported'])
        self.assertEqual(before,payload)

    def test_media_not_counted_as_base64_and_contract_is_still_enforced(self):
        payload={'messages':[{'role':'user','content':[{'type':'text','text':'Explain <|endoftext|>.'},
                 {'type':'image_url','image_url':{'url':'data:image/png;base64,'+'A'*2000000}}]}]}
        result=inspect_candidate(self.source,payload,{})
        self.assertLess(result['capacity']['input_tokens'],100)
        self.assertEqual(result['capacity']['unknown_input_costs'],['image_url'])
        self.contract['input_modalities']=['text']
        self.assertEqual(inspect_candidate(self.source,payload,{})['reasons'],[{'code':'image_input_unsupported'}])

    @override_settings(NEXUS_GATEWAY_DEFAULT_MAX_OUTPUT_TOKENS=4096)
    def test_default_budget_and_primary_api_transports_preserve_numeric_details(self):
        self.assertEqual(request_options({})['output_tokens'],4096)
        self.fields['context_window']={'value':1024}
        assessment=inspect_candidate(self.source,self.payload,{})
        exc=AutoCandidatesUnavailable([{'model':'public-alias',**assessment}])
        from apps.gateway.openai_compat import openai_exception_response
        from apps.common.exceptions import unified_exception_handler
        for response in [openai_exception_response(exc),unified_exception_handler(exc,{})]:
            self.assertEqual(response.status_code,400)
            self.assertEqual(response.data['error']['details'],exc.error_details)
        self.assertNotIn('hello',str(exc))
        from apps.gateway.views import openai_stream_chunks
        from apps.gateway.responses_native import stream_chunks
        with patch('apps.gateway.views.stream_chat_completions',side_effect=exc):
            text=''.join(openai_stream_chunks(request=NS(request_id=''),payload={}))
            self.assertIn('excluded_candidates',text)
            self.assertIn('context_window_exceeded',text)
        with patch('apps.gateway.services.stream_chat_completions',return_value=(x for x in ())),patch('apps.gateway.integration.gateway_integration') as integration:
            def fail():
                raise exc
                yield
            integration.return_value.response_events.return_value=fail()
            text=''.join(stream_chunks(request=NS(),payload={}))
            self.assertIn('excluded_candidates',text)
