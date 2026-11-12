from types import SimpleNamespace as NS
from unittest.mock import patch, Mock
from django.test import SimpleTestCase
from django.core.cache import cache
from apps.routers import aggregate_auto as auto


class AggregateAutoTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.router=NS(pk='router',tenant_id='org',project_id=None,updated_at='v1',strategy='task_type',router_type='aggregation')
        self.request=NS(headers={},user=NS(pk='owner'),api_key=NS(pk='key'))
        self.payload={'messages':[{'role':'user','content':'Say hello'}]}
        self.rows=[(NS(last_latency_ms=1000),NS(exposed_model_name='strong',priority=1),{}),
                   (NS(last_latency_ms=100),NS(exposed_model_name='fast',priority=2),{})]
        self.rows[0][1].pk='strong-binding';self.rows[1][1].pk='fast-binding'
        self.router.child_bindings = Mock()
        self.router.child_bindings.filter.return_value.order_by.return_value = [r[1] for r in self.rows]
        self.catalog=patch('apps.gateway.models_dev.snapshot',return_value={});self.catalog.start();self.addCleanup(self.catalog.stop)
        self.compat=patch.object(auto,'compatible',return_value=True);self.compat.start();self.addCleanup(self.compat.stop)
        self.fields=patch('apps.gateway.model_info.source_fields',return_value={});self.fields.start();self.addCleanup(self.fields.stop)
        self.contract=patch('apps.gateway.capabilities.deployment_contract',return_value={'operations':['chat.completions']});self.contract.start();self.addCleanup(self.contract.stop)

    def rank(self):return auto.rank(self.request,self.router,self.payload,self.rows)

    def test_rules_fast_by_default_complex_uses_quality(self):
        rows,trace=self.rank();self.assertEqual(rows[0][1].exposed_model_name,'fast')
        self.payload['messages'][0]['content']='Prove the invariant and debug a deadlock'
        rows,trace=self.rank();self.assertEqual(rows[0][1].exposed_model_name,'strong')

    def test_only_success_pins_tool_continuation_and_new_task_reassesses(self):
        rows,_=self.rank();auto.remember(rows[0][0])
        self.rows[0][0].last_latency_ms=1
        self.payload['messages'].append({'role':'tool','content':'result'})
        rows,trace=self.rank();self.assertEqual(trace['reason'],'tool_continuation');self.assertEqual(rows[0][1].exposed_model_name,'fast')
        self.payload['messages'].append({'role':'user','content':'Hello again'})
        rows,trace=self.rank();self.assertEqual(rows[0][1].exposed_model_name,'strong')

    def test_revoked_candidate_and_different_key_cannot_inherit_pin(self):
        rows,_=self.rank();auto.remember(rows[0][0]);self.payload['messages'].append({'role':'tool','content':'result'})
        self.rows=self.rows[:1];rows,trace=self.rank();self.assertNotEqual(trace['reason'],'tool_continuation')
        first=auto.continuity_key(self.request,self.router,self.payload)
        self.request.api_key.pk='other';self.assertNotEqual(first,auto.continuity_key(self.request,self.router,self.payload))

    def test_native_choice_and_failure_fallback(self):
        from apps.gateway.provider_adapters import ProviderClientError
        self.router.strategy='system_one';self.router.decision_config={'model':'decision'}
        with patch('apps.routers.decision_routing.resolve_provider',return_value=(NS(encrypted_key='encrypted'),NS(internal_api_url='https://fixture.invalid'))),patch('apps.common.crypto.decrypt_secret',return_value='fixture'),patch('apps.gateway.system_one.decide',return_value='model_1') as decide:
            rows,trace=self.rank();self.assertEqual(rows[0][1].exposed_model_name,'fast');self.assertEqual(trace['reason'],'decision_model')
            self.assertIn('fastest capable',decide.call_args.kwargs['instructions'])
            import json
            choices=decide.call_args.kwargs['choices']
            self.assertEqual(list(choices),['model_1','model_0'])
            self.assertEqual(json.loads(choices['model_1'])['role'],'fast')
            self.assertEqual(json.loads(choices['model_0'])['role'],'deep_reasoning')
            decide.side_effect=ProviderClientError('unavailable')
            rows,trace=self.rank();self.assertEqual(trace['status'],'fallback');self.assertEqual(rows[0][1].exposed_model_name,'fast')

    def test_harness_order_is_independent_of_latency_and_filters(self):
        from apps.routers.decision_harness import aggregate_order
        self.router.decision_config={'model_order':['fast-binding','strong-binding']}
        self.assertEqual(aggregate_order(self.router,self.rows),['fast','strong'])
        self.assertEqual(aggregate_order(self.router,self.rows[:1]),['fast','strong'])

    def test_personal_keys_do_not_share_task_continuity(self):
        self.request.api_key = None
        self.request._nexus_personal_router_credential_id = 'first'
        first = auto.continuity_key(self.request, self.router, self.payload)
        self.request._nexus_personal_router_credential_id = 'second'
        self.assertNotEqual(first, auto.continuity_key(self.request, self.router, self.payload))

    def test_metadata_filters_capacity_and_effort(self):
        self.compat.stop()
        with patch('apps.gateway.capabilities.supports_request',return_value=True),patch('apps.gateway.model_info.source_fields',return_value={'context_window':{'value':1000},'max_output_tokens':{'value':20},'reasoning_efforts':{'value':['low']}}):
            self.assertTrue(auto.compatible(NS(),{**self.payload,'max_tokens':10,'reasoning_effort':'low'},{}))
            self.assertFalse(auto.compatible(NS(),{**self.payload,'max_tokens':21},{}))
            self.assertFalse(auto.compatible(NS(),{**self.payload,'max_tokens':10,'reasoning_effort':'high'},{}))
