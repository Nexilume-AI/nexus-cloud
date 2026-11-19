import asyncio
import io
import json
import threading
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase
from apps.common.streaming import AsyncStreamBridge
from apps.gateway.provider_adapters import ProviderClientError, ProviderStreamEvent, iter_responses_sse, OpenAICompatibleAdapter
from apps.gateway.upstream_errors import diagnostic
from apps.routers.decision_budget import fit
from apps.routers.decision_harness import state


class StreamRecoveryTests(SimpleTestCase):
    async def test_heartbeats_do_not_corrupt_split_events(self):
        release = threading.Event()
        def source():
            release.wait(2)
            yield 'data: {"x":'
            yield '1}\n\n'
        bridge = AsyncStreamBridge(source(), cancel=release.set, heartbeat_seconds=.02)
        iterator = bridge.__aiter__()
        try:
            self.assertEqual(await anext(iterator), ': nexus-keepalive\n\n')
            release.set()
            rest = ''.join([x async for x in iterator])
            self.assertEqual(rest, 'data: {"x":1}\n\n')
        finally:
            await iterator.aclose()

    async def test_cancel_idle_reader_signals_worker(self):
        release, closed = threading.Event(), threading.Event()
        def source():
            try:
                release.wait(2)
                yield 'data: late\n\n'
            finally:
                closed.set()
        bridge = AsyncStreamBridge(source(), cancel=release.set, heartbeat_seconds=.01)
        it = bridge.__aiter__()
        await anext(it)
        await it.aclose()
        self.assertTrue(await asyncio.to_thread(closed.wait, 1))

    def test_responses_failure_reason_is_safe_and_specific(self):
        raw = {'type': 'response.failed', 'response': {'error': {
            'code': 'context_length_exceeded', 'message': 'maximum context length is 1048576 tokens. requested 1052278 tokens. SECRET'}}}
        with self.assertRaises(ProviderClientError) as caught:
            list(iter_responses_sse(io.BytesIO(('data: '+json.dumps(raw)+'\n\n').encode())))
        e = caught.exception
        self.assertEqual(e.error_details['requested_tokens'], 1052278)
        self.assertEqual(e.error_details['reason'], 'context_length_exceeded')
        self.assertNotIn('SECRET', str(e) + str(e.error_details))
        self.assertFalse(e.safe_to_retry)
        for raw in [{'error': {'code': 'secret-key', 'message': 'private prompt'}}, {'error': 'private'}]:
            message, details = diagnostic(raw, stage='test')
            self.assertNotIn('private', message+str(details))
            self.assertNotIn('secret-key', str(details))

    def test_decision_budget_truncates_only_task_and_keeps_tail(self):
        payload = {'model': 'custom', 'state': {'task': 'FIRST '+('复杂上下文 '*10000)+' LAST_INSTRUCTION'},
                   'questions': {'route': {'type': 'choice', 'criteria': {'a': 'fast', 'b': 'strong'}}}}
        before = json.dumps(payload)
        raw, report = fit(payload, 1024)
        result = json.loads(raw)
        self.assertTrue(report['task_truncated'])
        self.assertLessEqual(report['estimated_input_tokens'], report['input_budget_tokens'])
        self.assertEqual(result['questions'], payload['questions'])
        self.assertIn('LAST_INSTRUCTION', result['state']['task'])
        self.assertIn('FIRST', result['state']['task'])
        self.assertEqual(json.dumps(payload), before)
        self.assertIn('LAST_INSTRUCTION', state({'messages': [{'role': 'user', 'content': payload['state']['task']} ]})['task'])
        with self.assertRaises(ProviderClientError) as caught:
            fit({**payload, 'questions': {'route': {'criteria': {'a': 'x '*1000}}}}, 128)
        self.assertEqual(caught.exception.error_code, 'DECISION_CONTEXT_TOO_SMALL')

    def run_stream(self, events, *, cancel_before=False, complete=False):
        from apps.gateway import services as s
        request = SimpleNamespace(api_key=None, _nexus_stream_cancelled=threading.Event())
        dep = SimpleNamespace(provider_account=object())
        names = ['get_tenant_from_request','enforce_operation_policy','apply_single_router_key_default',
                 'enforce_api_key_policy','get_router_for_payload','enforce_api_key_router_policy',
                 'apply_provider_output_limit','reserve_gateway_charge','reserve_provider_capacity_for_deployment',
                 'release_provider_capacity','release_gateway_charge_reservation','count_previous_provider_failures',
                 'write_failure_log','record_failed_pool_usage_for_deployment','mark_provider_capacity_used',
                 'mark_deployment_runtime_success','mark_provider_account_available','calculate_cost',
                 'finalize_gateway_success','attribute_agent_model_usage']
        with ExitStack() as stack:
            mocks = {n: stack.enter_context(patch.object(s,n)) for n in names}
            stack.enter_context(patch.object(s,'gateway_integration',return_value=MagicMock()))
            stack.enter_context(patch.object(s,'resolve_deployment_candidates',return_value=[dep]))
            stack.enter_context(patch.object(s,'resolve_media_references_for_provider',side_effect=lambda **kw: kw['payload']))
            stack.enter_context(patch.object(s,'provider_request_payload',side_effect=lambda p:p))
            stack.enter_context(patch.object(s.OpenAICompatibleClient,'stream_chat_completions',return_value=iter(events)))
            stack.enter_context(patch.object(s,'build_stream_provider_response',return_value=SimpleNamespace(
                total_tokens=10,request_tokens=8,response_tokens=2,cached_input_tokens=4,cache_usage={'cached_tokens':4},reasoning_output_tokens=0)))
            stream = s.stream_chat_completions(request=request,payload={'model':'test','messages':[]})
            if cancel_before:
                request._nexus_stream_cancelled.set()
                with self.assertRaises(GeneratorExit): next(stream)
            else:
                next(stream)
                stream.close()
            if complete:
                mocks['finalize_gateway_success'].assert_called_once()
                mocks['write_failure_log'].assert_not_called()
                self.assertEqual(mocks['calculate_cost'].call_args.kwargs['cache'], {'cached_tokens':4})
            else:
                mocks['write_failure_log'].assert_called_once()
                self.assertEqual(mocks['write_failure_log'].call_args.kwargs['error_code'],'CLIENT_DISCONNECTED')
                mocks['finalize_gateway_success'].assert_not_called()
                mocks['release_gateway_charge_reservation'].assert_called_once()
            return dep

    def test_disconnect_preserves_one_audit_and_releases_reservation(self):
        dep = self.run_stream([ProviderStreamEvent(raw={'choices':[]})])
        self.assertEqual(dep._nexus_upstream_failure['upstream_result'],'unknown')

    def test_cancel_before_dispatch_does_not_leave_missing_receipt(self):
        self.run_stream([],cancel_before=True)

    def test_disconnect_after_usage_settles_once_with_cache(self):
        self.run_stream([ProviderStreamEvent(raw={'choices':[]},finish_reason='stop',usage={'prompt_tokens':8,'completion_tokens':2})],complete=True)
