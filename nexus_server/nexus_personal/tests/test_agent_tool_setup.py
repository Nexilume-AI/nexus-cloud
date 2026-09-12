"""Journal transactions plus actual SDK MCP HTTP; Computer writes use receipt fixtures here."""
import json
import tomllib
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.utils import timezone
from rest_framework import exceptions
from apps.common.crypto import decrypt_secret
from apps.workspaces.models import ComputerRuntimeCommand
from apps.workspaces.connection_core import WorkspaceConfigConflict
from apps.workspaces.tool_codec import tool_config_revision,_mcp_token
from nexus_personal import tool_apply,agent_tool_setup,tool_recovery
from nexus_personal.models import PersonalAgentCredential,PersonalToolConfigOperation
from .mcp_http_fixture import SDKMCPFixture
from . import test_agent_credentials as creds


@override_settings(ROOT_URLCONF='nexus_personal.urls',NEXUS_AGENT_RUNTIME_RUNNER='docker')
class PersonalAgentToolSetupTests(SDKMCPFixture,TestCase):
    setUpTestData=classmethod(creds.PersonalAgentCredentialTests.setUpTestData.__func__)
    request=creds.PersonalAgentCredentialTests.request
    mcp=creds.PersonalAgentCredentialTests.mcp

    def setUp(self):
        creds.PersonalAgentCredentialTests.setUp(self)
        self.credential.delete()
        self.before='model="keep-model"\n[model_providers.external]\nexperimental_bearer_token="external-api-secret"\n[mcp_servers.external]\nurl="https://external.test/mcp"\n'

    def req(self,key):
        request=self.request()
        request.headers={'Idempotency-Key':key}
        return request

    def prepare(self,content=None,action='add_agent',index=0,key='first'):
        content=self.before if content is None else content
        self.profile.refresh_from_db()
        data={'section':'agents','action':action,'agent_id':str(self.agents[index].pk),
            'mcp_server_name':'nexus-agent-'+self.agents[index].pk.hex,'expected_revision':tool_config_revision(content)}
        request=self.req(key)
        request_key,digest=tool_apply.request_identity(request,data)
        return tool_apply.prepare(request=request,session=self.session,data=data,content=content,
            parsed=tomllib.loads(content),prior_profile=self.profile,key=request_key,digest=digest)

    def target(self,op):
        return json.loads(decrypt_secret(op.command.encrypted_payload))['content']

    def finish(self,op):
        content=self.target(op)
        ComputerRuntimeCommand.objects.filter(pk=op.command_id).update(status='succeeded')
        tool_apply.finish(request=self.request(),session_id=self.session.pk,op_id=op.pk,
            content=content,parsed=tomllib.loads(content))
        return content

    def config_token(self,content,index=0):
        return _mcp_token(tomllib.loads(content)['mcp_servers']['nexus-agent-'+self.agents[index].pk.hex])

    def test_incremental_two_agents_reuse_key_preserve_api_and_external_and_restrict_removed_agent(self):
        first=self.prepare()
        token=self.config_token(self.target(first))
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+token)
        self.assertEqual(self.mcp().status_code,401)
        content=self.finish(first)
        self.assertEqual(self.mcp(method='tools/call',params={'name':'echo','arguments':{'value':'first'}}).status_code,200)
        second=self.prepare(content,index=1,key='second')
        self.assertFalse(second.credential_created)
        self.assertEqual(second.agent_credential_id,first.agent_credential_id)
        self.assertEqual(self.mcp(agent=self.agents[1]).status_code,404)
        content=self.finish(second)
        self.assertEqual(self.config_token(content,1),token)
        self.assertEqual(self.mcp(agent=self.agents[1],method='tools/call',params={'name':'echo','arguments':{'value':'second'}}).status_code,200)
        parsed=tomllib.loads(content)
        original=tomllib.loads(self.before)
        self.assertEqual(parsed['model_providers'],original['model_providers'])
        self.assertEqual(parsed['mcp_servers']['external'],original['mcp_servers']['external'])
        removed=self.prepare(content,action='remove_agent',key='remove-first')
        content=self.finish(removed)
        self.assertEqual(self.mcp().status_code,404)
        self.assertEqual(self.mcp(agent=self.agents[1]).status_code,200)
        last=self.prepare(content,action='remove_agent',index=1,key='remove-last')
        content=self.finish(last)
        self.assertEqual(self.mcp(agent=self.agents[1]).status_code,401)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.agent_credential_id)
        self.assertEqual(tomllib.loads(content),original)
        self.assertEqual(PersonalAgentCredential.objects.count(),1)

    def test_noop_and_rotation_are_idempotent_and_secrets_stay_out_of_journal(self):
        first=self.prepare()
        self.assertEqual(self.prepare().pk,first.pk)
        content=self.finish(first)
        noop=self.prepare(content,key='noop')
        self.finish(noop)
        self.assertEqual(PersonalAgentCredential.objects.count(),1)
        rotate=self.prepare(content,action='rotate_agent_credential',key='rotate')
        old_token=self.config_token(content)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+old_token)
        self.assertEqual(self.mcp().status_code,200)
        content=self.finish(rotate)
        new_token=self.config_token(content)
        self.assertNotEqual(new_token,old_token)
        self.assertEqual(self.mcp().status_code,401)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+new_token)
        self.assertEqual(self.mcp().status_code,200)
        self.assertNotIn(new_token,str(list(PersonalToolConfigOperation.objects.values())))
        self.assertNotIn(new_token,str(rotate.command.payload_summary))

    def test_drawer_contract_uses_actual_managed_health_and_redacted_external_rows(self):
        from nexus_personal import tool_setup
        content = self.finish(self.prepare())
        self.profile.refresh_from_db()
        parsed = tomllib.loads(content)
        with patch.object(tool_setup, '_read', return_value=({'exists': True}, content, parsed)):
            result = tool_setup.get_workspace_tool_config(request=self.request(), session_id=self.session.pk)
        self.assertEqual(result['agents']['credential_status'], 'ready')
        managed = result['agents']['managed'][0]
        self.assertTrue(managed['available'])
        self.assertEqual(managed['agent_id'], str(self.agents[0].pk))
        self.assertEqual(managed['message'], '')
        self.assertEqual(result['agents']['external'], [
            {'server_name': 'external', 'url': 'https://external.test', 'type': 'http'}])
        self.assertNotIn('content', result)
        serialized = json.dumps(result, default=str)
        self.assertNotIn(self.config_token(content), serialized)
        self.assertNotIn('external-api-secret', serialized)
        self.assertIn('launch_command', result['technical'])
        self.agents[0].status = 'suspended'
        self.agents[0].save(update_fields=['status'])
        summary = agent_tool_setup.summary(self.request(), self.profile, parsed)
        self.assertFalse(summary['managed'][0]['available'])
        self.assertEqual(summary['managed'][0]['code'], 'AGENT_RUNTIME_UNAVAILABLE')

    def test_agent_preview_has_renderable_diff_without_secrets(self):
        parsed = tomllib.loads(self.before)
        result = agent_tool_setup.preview(self.request(), self.profile, parsed,
            {'section': 'agents', 'action': 'add_agent', 'agent_id': str(self.agents[0].pk)},
            tool_config_revision(self.before), {'available': True})
        for change in result['changes']:
            self.assertIsInstance(change['before'], str)
            self.assertIsInstance(change['after'], str)
        self.assertEqual(result['changes'][-1]['after'], 'Connected')
        self.assertNotIn('external-api-secret', str(result))

    def test_external_name_conflict_and_changed_managed_endpoint_are_not_overwritten(self):
        name='nexus-agent-'+self.agents[0].pk.hex
        with self.assertRaises(WorkspaceConfigConflict):
            self.prepare(self.before+f'\n[mcp_servers.{name}]\nurl="https://external.test/other"\n')
        self.assertFalse(PersonalAgentCredential.objects.exists())
        content=self.finish(self.prepare())
        changed=content.replace('http://testserver/api/','https://external.test/api/')
        with self.assertRaises(WorkspaceConfigConflict):
            self.prepare(changed,action='rotate_agent_credential',key='changed')
        self.assertEqual(PersonalAgentCredential.objects.count(),1)

    def test_failed_rotation_keeps_old_key_and_transaction_failure_leaves_no_partial_write(self):
        first=self.prepare()
        content=self.finish(first)
        rotate=self.prepare(content,action='rotate_agent_credential',key='rotate')
        ComputerRuntimeCommand.objects.filter(pk=rotate.command_id).update(status='failed',error_code='TOOL_CONFIG_CONFLICT')
        tool_apply.fail_known(request=self.request(),session_id=self.session.pk,op_id=rotate.pk,code='TOOL_CONFIG_CONFLICT')
        self.assertIsNone(PersonalAgentCredential.objects.get(pk=first.agent_credential_id).revoked_at)
        self.assertIsNotNone(PersonalAgentCredential.objects.get(pk=rotate.agent_credential_id).revoked_at)
        before=ComputerRuntimeCommand.objects.count()
        with patch('nexus_personal.tool_apply._audit',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.prepare(content,action='rotate_agent_credential',key='audit-failure')
        self.assertEqual(ComputerRuntimeCommand.objects.count(),before)
        self.assertEqual(PersonalAgentCredential.objects.count(),2)

    def test_revoked_device_cancels_last_agent_removal_and_does_not_clear_api_metadata(self):
        content=self.finish(self.prepare())
        removal=self.prepare(content,action='remove_agent',key='remove')
        self.assertIsNone(removal.agent_credential_id)
        with self.captureOnCommitCallbacks(execute=True):
            self.device.revoked_at=timezone.now()
            self.device.save(update_fields=['revoked_at'])
        removal.refresh_from_db()
        removal.command.refresh_from_db()
        self.assertEqual(removal.state,'revoked')
        self.assertFalse(removal.active)
        self.assertEqual(removal.command.status,'canceled')

    def test_pending_allowlist_change_and_wrong_revision_prevent_promotion(self):
        content=self.finish(self.prepare())
        second=self.prepare(content,index=1,key='add')
        PersonalAgentCredential.objects.filter(pk=second.agent_credential_id).update(agent_ids=[str(self.agents[2].pk)])
        with self.assertRaises(WorkspaceConfigConflict):
            self.finish(second)
        second.refresh_from_db()
        self.assertTrue(second.active)

    def test_expired_agent_backup_cannot_be_restored_and_changed_payload_cannot_replay(self):
        content=self.finish(self.prepare())
        rotate=self.prepare(content,action='rotate_agent_credential',key='rotate')
        with self.assertRaises(WorkspaceConfigConflict):
            self.prepare(content,index=1,key='rotate')
        PersonalAgentCredential.objects.filter(pk=rotate.old_agent_credential_id).update(revoked_at=timezone.now())
        with self.assertRaises(WorkspaceConfigConflict):
            tool_recovery._old_configuration(request=self.request(),op=rotate)
