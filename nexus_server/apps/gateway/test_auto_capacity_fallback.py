import copy
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from django.core.cache import cache
from django.test import SimpleTestCase
from apps.routers import aggregate_auto as auto
from apps.routers.auto_eligibility import AutoCandidatesUnavailable


class AutoCapacityFallbackTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.router = NS(pk='router', tenant_id='org', project_id=None, updated_at='v1',
                         strategy='task_type', router_type='aggregation', decision_config={})
        self.request = NS(headers={}, user=NS(pk='owner'), api_key=NS(pk='key'))
        self.payload = {'messages': [{'role': 'user', 'content': 'hello ' * 6000}], 'max_tokens': 128}
        self.rows = [(NS(pk=name, upstream_model_id=name, last_latency_ms=latency),
                      NS(pk=name, exposed_model_name=name, priority=i), {})
                     for i, (name, latency) in enumerate([('fast', 10), ('large', 500)])]
        self.router.child_bindings = Mock()
        self.router.child_bindings.filter.return_value.order_by.return_value = [r[1] for r in self.rows]
        self.metadata = {'fast': {'context_window': 1024, 'max_output_tokens': 4096},
                         'large': {'context_window': 4096, 'max_output_tokens': 4096}}
        def fields(source, catalog):
            return {k: {'value': v} for k, v in self.metadata[source.pk].items()}
        for target, kwargs in [
            ('apps.gateway.models_dev.snapshot', {'return_value': {}}),
            ('apps.routers.auto_eligibility.source_fields', {'side_effect': fields}),
            ('apps.routers.auto_eligibility.deployment_contract', {'return_value': {
                'operations': ['chat.completions', 'responses'], 'input_modalities': ['text'], 'output_modalities': ['text']}}),
        ]:
            patcher = patch(target, **kwargs); patcher.start(); self.addCleanup(patcher.stop)

    def rank(self):
        return auto.rank(self.request, self.router, self.payload, self.rows)

    def test_uncertain_capacity_tries_only_largest_without_rewriting_input(self):
        before = copy.deepcopy(self.payload)
        rows, trace = self.rank()
        self.assertEqual(rows, [self.rows[1]])
        self.assertEqual(trace['reason'], 'capacity_estimate_fallback')
        self.assertTrue(trace['capacity_fallback']['upstream_validation_required'])
        self.assertTrue(trace['candidate_assessments'][1]['admitted_by_capacity_fallback'])
        self.assertFalse(trace['candidate_assessments'][1]['eligible'])  # estimate retained
        self.assertIn('context_window_exceeded', str(trace))
        self.assertEqual(self.payload, before)

    def test_headroom_respects_both_input_and_total_context_limits(self):
        self.metadata['large']['max_input_tokens'] = 512
        rows, _ = self.rank()
        self.assertEqual(rows, [self.rows[0]])

    def test_normal_or_unknown_capacity_candidate_prevents_soft_fallback(self):
        for context in (16000, None):
            with self.subTest(context=context):
                self.metadata['fast']['context_window'] = context
                rows, trace = self.rank()
                self.assertEqual(rows, [self.rows[0]])
                self.assertNotIn('capacity_fallback', trace)

    def test_capability_and_output_constraints_are_never_relaxed(self):
        for hard_fields, request_fields in [
            ({'max_output_tokens': 64}, {}),
            ({'supports_function_calling': False}, {'tools': [{'type': 'function', 'function': {'name': 'f'}}]}),
            ({'reasoning_efforts': ['low']}, {'reasoning_effort': 'high'}),
        ]:
            with self.subTest(fields=hard_fields):
                for name in self.metadata:self.metadata[name] = {'context_window': 1024, **hard_fields}
                self.payload = {**self.payload, **request_fields}
                with self.assertRaises(AutoCandidatesUnavailable):self.rank()
        self.payload = {'messages': [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'https://fixture.invalid/image'}}]}], 'max_tokens': 128}
        with self.assertRaises(AutoCandidatesUnavailable):self.rank()

    def test_output_exceeding_entire_context_is_not_an_uncertain_input_count(self):
        self.payload['max_tokens'] = 8192
        for fields in self.metadata.values():fields['max_output_tokens'] = 16384
        with self.assertRaises(AutoCandidatesUnavailable):self.rank()

    def test_hard_ineligible_larger_candidate_cannot_be_reintroduced(self):
        self.metadata['large']['reasoning_efforts'] = ['low']
        self.payload['reasoning_effort'] = 'high'
        rows, _ = self.rank()
        self.assertEqual(rows, [self.rows[0]])
        # Rows already passed permission resolution. Missing/revoked rows stay absent.
        self.rows = []
        with self.assertRaises(AutoCandidatesUnavailable):self.rank()

    def test_fallback_overrides_smaller_pin_and_skips_decision_inference(self):
        self.router.strategy = 'system_one'
        self.payload['messages'].append({'role': 'tool', 'content': 'more context'})
        cache.set(auto.continuity_key(self.request, self.router, self.payload), 'fast')
        with patch('apps.gateway.system_one.decide') as decide:
            rows, trace = self.rank()
        decide.assert_not_called()
        self.assertEqual(rows, [self.rows[1]])
        self.assertEqual(trace['reason'], 'capacity_estimate_fallback')
