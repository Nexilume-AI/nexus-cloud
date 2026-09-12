"""Real MCP HTTP/SDK and durable authority tests; Tool Setup activation is a DB fixture."""
from datetime import timedelta
from types import SimpleNamespace
import importlib
import json
import uuid
from django.apps import apps
from django.db import connection
from django.test import TestCase,override_settings
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework import exceptions
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from apps.common.crypto import decrypt_secret
from apps.common.subjects import request_subject
from apps.agents.models import Agent,AgentRuntimeImage,AgentRuntimeDeployment,AgentTaskExecution,AgentRuntimeInvocation,AgentDisplayRun
from apps.agents import runtime_services,task_execution
from apps.workspaces.models import WorkspaceConnection,ComputerRuntimeDevice,WorkspaceTerminalSession,WorkspaceToolManagedProfile
from nexus_personal.models import PersonalAgentCredential,PersonalInvocationUsage
from nexus_personal import agent_credentials,tool_profiles
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD
from .mcp_http_fixture import SDKMCPFixture


@override_settings(ROOT_URLCONF='nexus_personal.urls',NEXUS_AGENT_RUNTIME_RUNNER='docker')
class PersonalAgentCredentialTests(SDKMCPFixture,TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.installation = provision_owner(email='mcp-owner@example.test',password=PASSWORD)
        cls.owner_token = Token.objects.create(user=cls.installation.owner)
        cls.other = get_user_model().objects.create_user(username='other-mcp-owner')
        cls.agents=[]
        cls.runtimes=[]
        for name in ('First','Second','Not connected'):
            agent = Agent.objects.create(tenant=cls.installation.tenant,project=cls.installation.project,
                created_by=cls.installation.owner,name=name,status='active',repo_metadata={'tools':[
                    {'name':'echo','input_schema':{'type':'object','properties':{'value':{'type':'string'}},'required':['value']}}]})
            image = AgentRuntimeImage.objects.create(agent=agent,tenant=agent.tenant,project=agent.project,
                created_by=cls.installation.owner,image_ref='sdk-http-probe:not-a-container-claim')
            runtime = AgentRuntimeDeployment.objects.create(agent=agent,tenant=agent.tenant,project=agent.project,
                image=image,status='active',health_status='healthy',internal_mcp_url=cls.mcp_url)
            cls.agents.append(agent)
            cls.runtimes.append(runtime)

    def request(self):
        return SimpleNamespace(user=self.installation.owner,tenant_id=str(self.installation.tenant_id),
            project_id=str(self.installation.project_id),META={},headers={},query_params={},
            build_absolute_uri=lambda path:'http://testserver'+path)

    def setUp(self):
        type(self).tool_calls.clear()
        self.computer = WorkspaceConnection.objects.create(tenant=self.installation.tenant,project=self.installation.project,
            created_by=self.installation.owner,name='MCP profile',connection_type='runtime',
            owner_subject_type='user',owner_subject_hash=request_subject(self.request()).subject_hash)
        self.device = ComputerRuntimeDevice.objects.create(connection=self.computer,public_key_pem='model-fixture-not-pairing',
            public_key_fingerprint='e'*64,platform='linux',last_seen_at=timezone.now(),
            capabilities={'tool_setup.v1':1,'tool_setup.cas.v2':1})
        self.session = WorkspaceTerminalSession.objects.create(tenant=self.installation.tenant,project=self.installation.project,
            created_by=self.installation.owner,connection=self.computer,status='active')
        self.profile = tool_profiles.get_profile(request=self.request(),session_id=self.session.pk,create=True)
        self.credential,self.token = agent_credentials.issue(request=self.request(),profile=self.profile,
            agent_ids=[str(agent.pk) for agent in self.agents[:2]])
        self.client=APIClient()
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+self.token)

    def activate_fixture(self):
        # This tests authentication after a verified-apply state. It deliberately
        # does not claim that Agent Tool Setup has written a Computer config yet.
        self.profile.agent_credential=self.credential
        self.profile.save(update_fields=['agent_credential'])
        self.credential.expires_at=timezone.now()+timedelta(days=90)
        self.credential.save(update_fields=['expires_at'])

    def mcp(self, agent=None, method='tools/list',params=None):
        return self.client.post(f'/api/v1/agents/{(agent or self.agents[0]).pk}/mcp/',
            {'jsonrpc':'2.0','id':'probe','method':method,'params':params or {}},format='json',
            HTTP_MCP_PROTOCOL_VERSION='2025-06-18',HTTP_ACCEPT='application/json')

    def task(self):
        request=self.request()
        request._nexus_personal_agent_credential_id=self.credential.pk
        run=runtime_services.start_agent_invocation(request=request,agent_id=str(self.agents[0].pk),
            tool_name='echo',arguments={'value':'queued'},force_task=True)
        execution=AgentTaskExecution.objects.get(task__run=run)
        return run,execution,json.loads(decrypt_secret(execution.encrypted_payload))

    def test_pending_token_cannot_call_before_profile_verification(self):
        self.assertEqual(self.mcp().status_code,401)
        self.assertEqual(self.tool_calls,[])

    def test_real_sdk_mcp_initialize_list_and_echo_without_forwarding_secret(self):
        self.activate_fixture()
        initialized=self.mcp(method='initialize',params={'protocolVersion':'2025-06-18','capabilities':{},
            'clientInfo':{'name':'personal-test','version':'1'}})
        self.assertEqual(initialized.status_code,200,initialized.content)
        self.assertIn('serverInfo',json.loads(initialized.content)['result'])
        for agent in self.agents[:2]:
            listed=self.mcp(agent=agent)
            self.assertEqual(listed.status_code,200,listed.content)
            self.assertEqual(json.loads(listed.content)['result']['tools'][0]['name'],'echo')
            result=self.mcp(agent=agent,method='tools/call',params={'name':'echo','arguments':{'value':'中文 echo'}})
            self.assertEqual(result.status_code,200,result.content)
            self.assertFalse(json.loads(result.content)['result'].get('isError',False))
            self.assertIn('中文 echo',result.content.decode())
            self.assertNotIn(self.token,result.content.decode())
        self.assertEqual(self.tool_calls,[{'value':'中文 echo','received_credential':False}]*2)
        self.assertEqual(set(AgentRuntimeInvocation.objects.values_list('status',flat=True)),{'success'})
        self.assertEqual(set(PersonalInvocationUsage.objects.values_list('agent_credential_id',flat=True)),{self.credential.pk})

    def test_credential_cannot_manage_computer_agent_owner_or_call_models(self):
        self.activate_fixture()
        self.assertEqual(self.mcp(agent=self.agents[2]).status_code,404)
        for method,path in [('get','/api/v1/auth/whoami/'),('get','/api/v1/agents/'),
                ('get','/api/v1/computers/'),('post',f'/api/v1/computers/{self.computer.pk}/revoke/'),
                ('post','/api/v1/openai/v1/chat/completions'),
                ('patch',f'/api/v1/agents/{self.agents[0].pk}/')]:
            self.assertEqual(getattr(self.client,method)(path,{},format='json').status_code,403,path)
        self.assertEqual(self.tool_calls,[])

    def test_allowlist_shrink_revocation_expiry_and_offline_live_checks(self):
        self.activate_fixture()
        ComputerRuntimeDevice.objects.filter(pk=self.device.pk).update(last_seen_at=timezone.now()-timedelta(days=1))
        self.assertEqual(self.mcp().status_code,200)
        PersonalAgentCredential.objects.filter(pk=self.credential.pk).update(agent_ids=[str(self.agents[1].pk)])
        self.assertEqual(self.mcp().status_code,404)
        self.assertEqual(self.mcp(agent=self.agents[1]).status_code,200)
        PersonalAgentCredential.objects.filter(pk=self.credential.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.mcp(agent=self.agents[1]).status_code,401)

    def test_computer_revoke_and_hard_delete_profile_invalidate_key(self):
        self.activate_fixture()
        with self.captureOnCommitCallbacks(execute=True):
            self.device.revoked_at=timezone.now()
            self.device.save(update_fields=['revoked_at'])
        self.credential.refresh_from_db()
        self.assertIsNotNone(self.credential.revoked_at)
        self.assertEqual(self.mcp().status_code,401)
        self.device.revoked_at=None
        self.device.save(update_fields=['revoked_at'])
        self.assertEqual(self.mcp().status_code,401)

    def test_missing_profile_or_changed_owner_cannot_bypass_live_binding(self):
        self.activate_fixture()
        WorkspaceConnection.objects.filter(pk=self.computer.pk).update(created_by=self.other)
        self.assertEqual(self.mcp().status_code,401)
        WorkspaceConnection.objects.filter(pk=self.computer.pk).update(created_by=self.installation.owner)
        with self.captureOnCommitCallbacks(execute=True):
            WorkspaceToolManagedProfile.objects.filter(pk=self.profile.pk).delete()
        self.credential.refresh_from_db()
        self.assertIsNotNone(self.credential.revoked_at)
        self.assertEqual(self.credential.tool_profile_id,self.profile.pk)

    def test_task_authority_retains_key_without_token_and_rechecks_live_allowlist(self):
        self.activate_fixture()
        run,execution,payload=self.task()
        self.assertNotIn(self.token,str(payload))
        self.assertEqual(PersonalInvocationUsage.objects.get(run_id=run.pk).agent_credential_id,self.credential.pk)
        request=task_execution.restore_request(payload)
        self.assertEqual(request._nexus_personal_agent_credential_id,self.credential.pk)
        self.assertEqual(runtime_services.get_runtime_use_agent(request=request,tenant=self.installation.tenant,
            agent_id=str(self.agents[0].pk)).pk,self.agents[0].pk)
        PersonalAgentCredential.objects.filter(pk=self.credential.pk).update(agent_ids=[str(self.agents[1].pk)])
        with self.assertRaises(exceptions.PermissionDenied):
            task_execution.restore_request(payload)

    def test_deleted_key_snapshot_does_not_restore_full_owner_authority(self):
        self.activate_fixture()
        run,execution,payload=self.task()
        self.credential.delete()
        self.assertIsNotNone(PersonalInvocationUsage.objects.get(run_id=run.pk).agent_credential_id)
        with self.assertRaises(exceptions.PermissionDenied):
            task_execution.restore_request(payload)

    def test_queued_follow_up_restoration_retains_original_agent_capability(self):
        from nexus_personal.agent_invocations import PersonalInvocationLifecycle
        self.activate_fixture()
        run,execution,payload=self.task()
        # Follow-up HTTP loads a persisted Run, not the constructor's string FK
        # values returned by the initial invocation helper.
        run.refresh_from_db()
        authority={key:value for key,value in payload.items() if key!='context'}
        authority.update(PersonalInvocationLifecycle().capture_follow_up(run=run,task=execution.task))
        self.assertEqual(task_execution.restore_request(authority)._nexus_personal_agent_credential_id,self.credential.pk)
        PersonalAgentCredential.objects.filter(pk=self.credential.pk).update(revoked_at=timezone.now())
        with self.assertRaises(exceptions.PermissionDenied):
            task_execution.restore_request(authority)

    def test_foreign_profile_cannot_issue_a_credential(self):
        WorkspaceToolManagedProfile.objects.filter(pk=self.profile.pk).update(created_by=self.other)
        with self.assertRaises(exceptions.NotFound):
            agent_credentials.issue(request=self.request(),profile=self.profile,agent_ids=[str(self.agents[0].pk)])
        self.assertEqual(PersonalAgentCredential.objects.count(),1)

    def test_revocation_after_dispatch_closes_receipt_without_accepting_success(self):
        from nexus_personal.agent_invocations import PersonalInvocationLifecycle
        self.activate_fixture()
        run,execution,payload=self.task()
        request=task_execution.restore_request(payload)
        invocation=AgentRuntimeInvocation.objects.get(display_run=run)
        # Original synchronous MCP completion writes the Run result first.
        runtime_services.finish_invocation_display_run(run=run,succeeded=True)
        PersonalAgentCredential.objects.filter(pk=self.credential.pk).update(revoked_at=timezone.now())
        with self.assertRaises(exceptions.AuthenticationFailed):
            PersonalInvocationLifecycle().finalize(request=request,invocation=invocation,
                succeeded=True,error_code='',latency_ms=12)
        invocation.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(invocation.status,'failed')
        self.assertEqual(invocation.error_code,'AGENT_CREDENTIAL_REVOKED')
        self.assertIsNotNone(PersonalInvocationUsage.objects.get(invocation=invocation).finished_at)
        self.assertEqual(run.status,'failed')
        self.assertFalse(run.context_token_hash)
        failures=run.events.filter(event_type='RUN_ERROR').count()
        self.assertEqual(failures,1)
        with self.assertRaises(exceptions.AuthenticationFailed):
            PersonalInvocationLifecycle().finalize(request=request,invocation=invocation,
                succeeded=True,error_code='',latency_ms=12)
        self.assertEqual(run.events.filter(event_type='RUN_ERROR').count(),failures)

    def test_missing_durable_receipt_and_downgrade_with_pending_capability_fail_closed(self):
        self.activate_fixture()
        run,execution,payload=self.task()
        migration=importlib.import_module('nexus_personal.migrations.0011_agent_mcp_credentials')
        with self.assertRaises(RuntimeError):
            migration.require_drained_capabilities(apps,SimpleNamespace(connection=connection))
        PersonalInvocationUsage.objects.filter(run_id=run.pk).delete()
        with self.assertRaises(exceptions.PermissionDenied):
            task_execution.restore_request(payload)

    def test_wrong_context_tampered_token_and_external_caller_are_refused(self):
        self.activate_fixture()
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+self.token[:-1]+('A' if self.token[-1]!='A' else 'B'))
        self.assertEqual(self.mcp().status_code,401)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+self.token,HTTP_X_NEXUS_PROJECT=str(uuid.uuid4()))
        self.assertEqual(self.mcp().status_code,403)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+self.token,HTTP_X_NEXUS_END_USER='different-caller')
        self.assertEqual(self.mcp().status_code,403)
