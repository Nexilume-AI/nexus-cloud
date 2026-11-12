import json
from unittest.mock import patch
from django.test import SimpleTestCase
from apps.routers import decision_harness as harness
from apps.routers.inputs import DecisionConfigSerializer


class DecisionHarnessTests(SimpleTestCase):
    def test_profiles_match_desktop(self):
        for n, expected in [(2, ['deep_reasoning','fast']), (3, ['deep_reasoning','balanced','fast']),
                            (4, ['deep_reasoning','advanced','balanced','fast']),
                            (5, ['deep_reasoning','advanced','balanced','efficient','fast'])]:
            self.assertEqual([harness.role_at(i,n) for i in range(n)], expected)

    def test_known_limits_and_efforts_are_preserved_without_defaults(self):
        values={'context_window':10000,'max_input_tokens':8000,'max_output_tokens':2000,
                'reasoning_supported':True,'reasoning_efforts':['low','high']}
        with patch('apps.gateway.model_info.source_fields',return_value={k:{'value':v,'source':'source_override','stale':False,'upstream_confirmed':False} for k,v in values.items()}):
            row=json.loads(harness.description(name='a',index=1,count=2,sources=[object()],catalog={},payload={'max_tokens':1000}))
        self.assertEqual(row['inputBudget'],8000)
        self.assertIsNone(row['reasoningEffort'])
        self.assertEqual(row['capabilities']['reasoningEfforts'],['low','high'])
        self.assertEqual(row['capabilities']['outputBudget'],1000)

    def test_unknown_metadata_is_not_fabricated(self):
        with patch('apps.gateway.model_info.source_fields',return_value={}):
            row=json.loads(harness.description(name='a',index=0,count=2,sources=[object()],catalog={},payload={}))
        self.assertIsNone(row['inputBudget'])
        self.assertIsNone(row['capabilities']['reasoningSupported'])
        self.assertIsNone(row['capabilities']['reasoningEfforts'])

    def test_only_bounded_latest_user_text_sent(self):
        result=harness.state({'messages':[{'role':'system','content':'system-private'}, {'role':'user','content':'first'},
            {'role':'user','content':[{'type':'text','text':'secret-decision-key sk-hidden Bearer confidential '+ 'x'*5000},
                                      {'type':'image_url','image_url':{'url':'private-image'}}]},
            {'role':'tool','content':'private-result'}], 'tools':[{'private':'schema'}]},key='secret-decision-key')
        self.assertEqual(result['evaluation'],'new_task')
        self.assertEqual(len(result['task']),4000)
        for text in ['secret-decision-key','sk-hidden','confidential','private','first']:
            self.assertNotIn(text,json.dumps(result))

    def test_order_rejects_duplicates_and_nonidentities(self):
        base={'provider_connection_id':'00000000-0000-0000-0000-000000000001','model':'decision'}
        for order in [['not-a-uuid'], [base['provider_connection_id']]*2]:
            self.assertFalse(DecisionConfigSerializer(data={**base,'model_order':order}).is_valid())
        serializer=DecisionConfigSerializer(data={**base,'model_order':[base['provider_connection_id']]})
        self.assertTrue(serializer.is_valid(),serializer.errors)
        self.assertEqual(serializer.validated_data['model_order'],[base['provider_connection_id']])
