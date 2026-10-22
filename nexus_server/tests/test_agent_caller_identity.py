from types import SimpleNamespace
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.exceptions import PermissionDenied
from apps.agents.caller_identity import runtime_identity
from apps.agents.credential_services import scope


class CallerIdentityTests(SimpleTestCase):
    def run_for(self, **changes):
        return SimpleNamespace(**(dict(caller_principal_type='api_key',caller_principal_id='key-a',
            caller_subject_hash='legacy-subject',consumer_tenant_id='tenant-a',consumer_project_id=None,agent_id='agent-a')|changes))
    def test_same_key_same_identity_despite_session_or_end_user_label(self):
        a=self.run_for();b=self.run_for(caller_principal_type='api_key_external',caller_subject_hash='untrusted-label')
        self.assertEqual(runtime_identity(a),runtime_identity(b));self.assertEqual(scope(a),scope(b))
        self.assertNotIn('key-a',str(runtime_identity(a)))
    def test_distinct_keys_accounts_projects_and_agents(self):
        baseline=runtime_identity(self.run_for())
        for changes in ({'caller_principal_id':'key-b'},{'caller_principal_type':'user'},
                        {'consumer_tenant_id':'other'},{'consumer_project_id':'other'},{'agent_id':'other'}):
            result=runtime_identity(self.run_for(**changes));self.assertNotEqual(result['owner_scope'],baseline['owner_scope'])
            if 'consumer_project_id' in changes or 'agent_id' in changes:
                self.assertEqual(result['quota_scope'],baseline['quota_scope'])
            else:self.assertNotEqual(result['quota_scope'],baseline['quota_scope'])
    def test_missing_verified_principal_is_rejected(self):
        for changes in ({'caller_principal_type':'anonymous'},{'caller_principal_id':''},{'consumer_tenant_id':None}):
            with self.assertRaises(PermissionDenied):runtime_identity(self.run_for(**changes))
