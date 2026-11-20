import copy
import json
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import patch
from django.test import SimpleTestCase
from apps.routers.decision_budget import fit, input_limit, documented_input_limit
from apps.routers import decision_harness as h
from apps.routers.inputs import DecisionConfigSerializer
from apps.gateway.provider_adapters import ProviderClientError


class DynamicDecisionTests(SimpleTestCase):
    def payload(self, text):
        return {'model': 'custom', 'state': h.state({'messages': [{'role': 'user', 'content': text}]}),
                'questions': {'route': {'type': 'choice', 'instructions': h.instructions({}),
                                       'criteria': {'a': 'capable and cheap', 'b': 'stronger and expensive'}}}}

    def test_large_capacity_keeps_full_text_over_old_character_and_byte_caps(self):
        text = 'Document context. ' * 5000 + 'Must preserve the middle and end.'
        payload = self.payload(text)
        raw, report = fit(payload, 65536)
        self.assertEqual(json.loads(raw)['state']['task'], text)
        self.assertGreater(len(raw), 65536)
        self.assertFalse(report['task_truncated'])

    def test_middle_requirements_survive_small_capacity_and_original_is_unchanged(self):
        text = 'BEGIN\n' + 'ordinary source material.\n' * 3000 + '\n必须保留审计日志，禁止修改数据库。\n' + 'background examples.\n' * 3000 + '\nEND'
        payload = self.payload(text); original = copy.deepcopy(payload)
        raw, report = fit(payload, 2048)
        task = json.loads(raw)['state']['task']
        for part in ['BEGIN', '必须保留审计日志，禁止修改数据库。', 'END']:
            self.assertIn(part, task)
        self.assertLessEqual(report['estimated_input_tokens'], report['input_budget_tokens'])
        self.assertEqual(payload, original)
        self.assertEqual(json.loads(raw)['questions'], original['questions'])
        self.assertEqual(report['selection'], 'requirements_and_distributed_excerpts')

    def test_small_unknown_unicode_and_transport_bounds(self):
        # Distinct requirements, emoji, escaped JSON and one long unbroken line.
        text = '\n'.join(f'Constraint {i}: 必须保留{ i }，🙂 "quoted" \\ code' for i in range(1500))
        for limit in (None, 2048, 8192, 32768):
            raw, report = fit(self.payload(text), limit)
            self.assertLessEqual(report['estimated_input_tokens'], report['input_budget_tokens'])
            self.assertLessEqual(len(raw), report['transport_limit_bytes'])
            self.assertEqual(report['capacity_known'], limit is not None)
        raw, report = fit(self.payload('longline' * 40000), 1000000)
        self.assertLessEqual(len(raw), report['transport_limit_bytes'])
        self.assertTrue(report['task_truncated'])

    def test_capacity_changes_retention_not_routing_objective(self):
        text = '\n'.join(f'Paragraph {i} has different examples of code and data.' for i in range(1500))
        sizes = [len(json.loads(fit(self.payload(text), limit)[0])['state']['task']) for limit in (2048, 8192, 32768)]
        self.assertLess(sizes[0], sizes[1]); self.assertLess(sizes[1], sizes[2])
        self.assertIn('lowest estimated-cost', h.instructions({'routing_preference': 'cost'}))
        self.assertIn('quality over price', h.instructions({'routing_preference': 'performance'}))

    def test_dense_text_keeps_margin_for_unknown_upstream_tokenizer(self):
        import hashlib
        text = '\n'.join(f'Must retain item {i}: ' + hashlib.sha256(str(i).encode()).hexdigest()
                         for i in range(1800))
        for limit in (None, 2048, 8192):
            _, report = fit(self.payload(text), limit)
            reference = report['estimated_input_tokens']
            # Real-provider regression: dense hash-like text was undercounted
            # by 34%, although the ordinary prose counts were nearly exact.
            self.assertLessEqual(reference * 1.34 + 128, report['input_budget_tokens'])
            self.assertLessEqual(report['guarded_input_tokens'], report['input_budget_tokens'])
            self.assertEqual(report['tokenizer_safety_factor'], 1.5)

    def test_no_room_for_candidate_metadata_fails_before_send(self):
        payload = self.payload('Must keep this requirement')
        payload['questions']['route']['criteria']['a'] = 'metadata ' * 2000
        with self.assertRaises(ProviderClientError) as caught:
            fit(payload, 1024)
        self.assertEqual(caught.exception.error_code, 'DECISION_CONTEXT_TOO_SMALL')

    def test_model_capacity_uses_smallest_known_bound(self):
        from unittest.mock import Mock
        runtime = Mock()
        with patch('apps.gateway.model_info.provider_fields', return_value={'context_window': {'value': 8192}, 'max_input_tokens': {'value': 6000}}), patch('apps.gateway.models_dev.snapshot', return_value={}):
            self.assertEqual(input_limit(runtime, 'decision'), 6000)

    def test_documented_capacity_only_for_exact_vendor_endpoint_and_model(self):
        runtime = NS(runtime_type='direct_api', internal_api_url='https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1')
        self.assertEqual(documented_input_limit(runtime, 'decision-model-preview'), 65536)
        self.assertIsNone(documented_input_limit(runtime, 'my-custom-decision-model'))
        for url in ('https://custom.example/v1',
                    'https://workspace.cn-beijing.maas.aliyuncs.com.evil.example/compatible-mode/v1',
                    'http://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1',
                    'https://workspace.cn-beijing.maas.aliyuncs.com:444/compatible-mode/v1',
                    'https://workspace.cn-beijing.maas.aliyuncs.com/another-api/v1'):
            runtime.internal_api_url = url
            self.assertIsNone(documented_input_limit(runtime, 'decision-model-preview'))

    def test_explicit_metadata_precedes_documented_fallback_including_unlisted_models(self):
        from unittest.mock import Mock
        runtime = NS(runtime_type='direct_api', internal_api_url='https://workspace.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/', model_offers=Mock())
        with patch('apps.gateway.model_info.provider_fields', return_value={'max_input_tokens': {'value': 8192}}), patch('apps.gateway.models_dev.snapshot', return_value={}):
            self.assertEqual(input_limit(runtime, 'decision-model-preview'), 8192)
        with patch('apps.gateway.model_info.provider_fields', return_value={}), patch('apps.gateway.models_dev.snapshot', return_value={}):
            self.assertEqual(input_limit(runtime, 'decision-model-preview'), 65536)
        runtime.model_offers.filter.return_value.first.return_value = None
        self.assertEqual(input_limit(runtime, 'decision-model-preview'), 65536)

    def test_known_large_capacity_retains_substantially_more_dense_requirements(self):
        import hashlib
        text = '\n'.join(f'Must preserve requirement {i}: ' + hashlib.sha256(str(i).encode()).hexdigest()
                         for i in range(1800))
        payload = self.payload(text); original = copy.deepcopy(payload)
        _, small = fit(payload, None)
        raw, large = fit(payload, 65536)
        self.assertGreater(large['estimated_input_tokens'], 16000)
        self.assertGreater(large['selected_segments'], small['selected_segments'] * 8)
        self.assertLessEqual(large['guarded_input_tokens'], large['input_budget_tokens'])
        self.assertLessEqual(len(raw), large['transport_limit_bytes'])
        self.assertEqual(payload, original)

    def test_price_is_not_speed_unknown_is_not_free_and_output_tariff_matters(self):
        tokens = NS(estimate=lambda s: {'input_tokens': 1000, 'unknown_input_costs': []})
        cheap = NS(pricing_rate=Decimal('.1'), last_latency_ms=1000)
        fast = NS(pricing_rate=Decimal('4'), last_latency_ms=1)
        self.assertLess(h.cost_estimate([cheap], {}, tokens), h.cost_estimate([fast], {}, tokens))
        self.assertIsNone(h.cost_estimate([NS(last_latency_ms=0)], {}, tokens))
        self.assertIsNone(h.cost_estimate([NS(pricing_rate=float('nan'))], {}, tokens))
        rich = NS(pricing_rate=Decimal('1'), runtime_model_offer=NS(input_price_per_1k_tokens=Decimal('.01'), output_price_per_1k_tokens=Decimal('10')))
        self.assertEqual(h.cost_estimate([rich], {'max_tokens': 1000}, tokens), Decimal('10.01'))
        self.assertEqual(h.cost_estimate([cheap, fast], {}, tokens), h.cost_estimate([fast], {}, tokens))

    def test_validated_objective_roundtrip_and_invalid_rejected(self):
        data = {'provider_connection_id': '00000000-0000-0000-0000-000000000001', 'model': 'custom'}
        for mode in ('cost', 'performance'):
            serializer = DecisionConfigSerializer(data={**data, 'routing_preference': mode})
            self.assertTrue(serializer.is_valid(), serializer.errors)
            self.assertEqual(serializer.validated_data['routing_preference'], mode)
        self.assertFalse(DecisionConfigSerializer(data={**data, 'routing_preference': 'fast-is-cheap'}).is_valid())

    def test_actual_native_wire_is_bounded_and_carries_middle_requirement(self):
        from apps.gateway.system_one import decide
        from io import BytesIO
        diagnostics = {}
        text = 'Source examples.\n' * 2000 + '\nMust preserve the audit trail.\n' + 'Other examples.\n' * 2000
        with patch('apps.gateway.system_one.open_provider_url', return_value=BytesIO(b'{"answers":{"route":{"type":"choice","choice":"a"}}}')) as send:
            chosen = decide(base_url='https://fixture.invalid', key='test', model='custom', state=h.state({'messages':[{'role':'user','content':text}]}),
                            choices={'a':'cheap', 'b':'strong'}, instructions=h.instructions({'routing_preference':'performance'}), max_input_tokens=2048, diagnostics=diagnostics)
        body = send.call_args.kwargs['body']
        self.assertEqual(chosen, 'a')
        self.assertIn('Must preserve the audit trail.', json.loads(body)['state']['task'])
        self.assertIn('quality over price', json.loads(body)['questions']['route']['instructions'])
        self.assertLessEqual(diagnostics['estimated_input_tokens'], diagnostics['input_budget_tokens'])


    def test_pool_routing_uses_objective_on_success_and_fallback(self):
        from unittest.mock import Mock
        from apps.routers.decision_routing import select_pools
        router = NS(decision_config={'model':'custom', 'routing_preference':'cost'}, model_group_bindings=Mock())
        router.model_group_bindings.filter.return_value.order_by.return_value = [NS(model_group_id='strong'), NS(model_group_id='cheap')]
        pools = [{'id': name, 'group': NS(display_name=name, name=name), 'binding': NS(routing_hint='', priority=i),
                  'deployments': [NS(pricing_rate=Decimal(price))]} for i,(name,price) in enumerate([('strong','5'),('cheap','.1')])]
        payload={'messages':[{'role':'user','content':'Hello'}]}
        with patch('apps.routers.decision_routing.resolve_provider', side_effect=ValueError('offline')):
            ordered, trace=select_pools(router=router, pools=pools, payload=payload)
            self.assertEqual(ordered[0]['id'], 'cheap')
            self.assertEqual(trace['status'], 'fallback')
            router.decision_config['routing_preference']='performance'
            ordered, _=select_pools(router=router, pools=pools, payload=payload)
            self.assertEqual(ordered[0]['id'], 'strong')
        with patch('apps.routers.decision_routing.resolve_provider',return_value=(NS(encrypted_key='test'),NS(internal_api_url='https://fixture.invalid'))), patch('apps.common.crypto.decrypt_secret',return_value='test'), patch('apps.gateway.models_dev.snapshot',return_value={}), patch('apps.gateway.model_info.source_fields',return_value={}), patch('apps.routers.decision_budget.input_limit',return_value=4096), patch('apps.routers.decision_routing.decide',return_value='pool_0') as decide:
            ordered, trace=select_pools(router=router, pools=pools, payload=payload)
            self.assertEqual(ordered[0]['id'],'strong')
            self.assertIn('quality over price', decide.call_args.kwargs['instructions'])
            self.assertEqual(trace['reason'],'decision_model')
