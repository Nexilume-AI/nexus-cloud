import json
from datetime import timedelta
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import exceptions
from apps.agents.models import Agent, AgentDisplayRun, AgentCredentialAuthorization, AgentDisplayEvent
from apps.agents import credential_services as c
from apps.agents.runtime_services import _mcp_client_capabilities
from apps.tenancy.models import Tenant, Project
from apps.common.subjects import request_subject, hash_token


@override_settings(NEXUS_PUBLIC_BASE_URL='https://cloud.example.test', ROOT_URLCONF='tests.provider_credential_urls')
class CredentialsTests(TestCase):
    def setUp(self):
        from nexus_personal.services import provision_owner
        installation = provision_owner(email='credential-owner@example.test', password='Provider-Test-Passphrase-2026!')
        self.user=installation.owner
        self.other=get_user_model().objects.create_user(username='credential-other')
        self.tenant=installation.tenant
        self.agent=Agent.objects.create(name='Atrex Test',tenant=self.tenant,created_by=self.user)
        setting=override_settings(NEXUS_AGENT_CREDENTIAL_ENABLED_AGENTS=[str(self.agent.id)])
        setting.enable(); self.addCleanup(setting.disable)
        self.req=SimpleNamespace(user=self.user,tenant_id=str(self.tenant.id),headers={},data={})
        self.run=self.new_run()
        self.secret='synthetic-key-never-a-real-credential'

    def new_run(self, **kwargs):
        return AgentDisplayRun.objects.create(**(dict(agent=self.agent,tenant=self.tenant,consumer_tenant=self.tenant,
            run_kind='invocation',caller_principal_type='user',caller_principal_id=str(self.user.pk),
            caller_subject_hash=request_subject(self.req).subject_hash,interaction_token_hash=hash_token('test-callback'),
            interaction_token_expires_at=timezone.now()+timedelta(hours=1),status='running')|kwargs))

    def begin(self):
        return c.request_authorization(self.run.id,'test-callback','anthropic')

    def approve(self, aid):
        self.req.data={'action':'authorize','secret':self.secret,'consent':True,'ttl_seconds':300}
        return c.decide(self.req,aid)

    def test_encrypted_private_and_revoke(self):
        value=self.begin(); aid=value['authorization_id']; result=self.approve(aid)
        row=AgentCredentialAuthorization.objects.get(pk=aid)
        self.assertNotIn(self.secret,row.encrypted_secret)
        self.assertNotIn(self.secret,json.dumps(result))
        self.assertEqual(c.internal_status(self.run.id,'test-callback',aid,True)['secret'],self.secret)
        self.assertFalse(AgentDisplayEvent.objects.filter(run=self.run).exists())
        self.req.data={'action':'revoke'}; c.decide(self.req,aid)
        with self.assertRaises(exceptions.PermissionDenied): c.internal_status(self.run.id,'test-callback',aid,True)
        row.refresh_from_db();self.assertEqual(row.encrypted_secret,'')

    def test_another_user_cannot_view_or_approve(self):
        aid=self.begin()['authorization_id']; self.req.user=self.other
        with self.assertRaises(exceptions.NotFound): c.browser_row(self.req,aid)
        with self.assertRaises(exceptions.NotFound): self.approve(aid)

    def test_scope_isolates_caller_project_and_agent(self):
        aid=self.begin()['authorization_id']; self.approve(aid)
        project=Project.objects.create(tenant=self.tenant,name='Other scope')
        for changes in ({'caller_subject_hash':'another'}, {'consumer_project':project}, {'consumer_tenant':Tenant.objects.create(name='Other',slug='credential-other')}):
            run=self.new_run(**changes)
            with self.assertRaises(exceptions.NotFound): c.internal_status(run.id,'test-callback',aid,True)
        other=Agent.objects.create(name='Other',tenant=self.tenant)
        with override_settings(NEXUS_AGENT_CREDENTIAL_ENABLED_AGENTS=[str(other.id)]):
            run=self.new_run(agent=other)
            with self.assertRaises(exceptions.NotFound): c.internal_status(run.id,'test-callback',aid,True)

    def test_reuse_same_caller_but_not_finished_run(self):
        aid=self.begin()['authorization_id'];self.approve(aid)
        another=self.new_run()
        self.assertEqual(c.request_authorization(another.id,'test-callback','anthropic')['authorization_id'],aid)
        another.status='completed';another.save()
        with self.assertRaises(exceptions.PermissionDenied):c.internal_status(another.id,'test-callback',aid,True)

    def test_expiry_and_decline(self):
        aid=self.begin()['authorization_id']
        AgentCredentialAuthorization.objects.filter(pk=aid).update(expires_at=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(exceptions.ValidationError):self.approve(aid)
        self.assertEqual(c.internal_status(self.run.id,'test-callback',aid)['status'],'expired')
        self.req.data={'action':'decline'};c.decide(self.req,aid)
        with self.assertRaises(exceptions.PermissionDenied):c.internal_status(self.run.id,'test-callback',aid,True)

    def test_consent_and_limits(self):
        aid=self.begin()['authorization_id']
        for data in ({'action':'authorize','secret':self.secret}, {'action':'authorize','secret':self.secret,'consent':True,'ttl_seconds':86401}):
            self.req.data=data
            with self.assertRaises(exceptions.ValidationError):c.decide(self.req,aid)
        self.assertEqual(AgentCredentialAuthorization.objects.get(pk=aid).status,'pending')

    def test_unowned_api_key_principal_is_denied(self):
        self.run.caller_principal_type='api_key';self.run.caller_principal_id='unowned';self.run.save()
        with self.assertRaises(exceptions.PermissionDenied): self.begin()

    def test_url_negotiation_and_accept_is_not_authorization(self):
        aid=self.begin()['authorization_id'];seen=set()
        capabilities=_mcp_client_capabilities(json.dumps({'method':'initialize','params':{'capabilities':{'elicitation':{'url':{}}}}}).encode())
        self.assertTrue(capabilities['elicitation_url']);self.assertFalse(capabilities['elicitation'])
        session=SimpleNamespace(client_capabilities_json=capabilities,caller_subject_hash=self.run.caller_subject_hash,external_session_hash=hash_token('session'))
        events=list(c.chunks(self.req,self.run,session,seen));self.assertEqual(len(events),1)
        rpc=json.loads(events[0].decode().split('data: ')[1]); self.assertEqual(rpc['params']['mode'],'url')
        c.handle_response(self.req,self.agent.id,{'id':rpc['id'],'result':{'action':'accept'}},{'Mcp-Session-Id':'session'})
        self.assertEqual(AgentCredentialAuthorization.objects.get(pk=aid).status,'pending')
        self.approve(aid)
        self.assertIn(b'notifications/elicitation/complete',b''.join(c.chunks(self.req,self.run,session,seen)))
        self.assertNotIn(self.secret.encode(),b''.join(events))

    def test_decline_from_wrong_session_is_ignored(self):
        aid=self.begin()['authorization_id']; session=SimpleNamespace(client_capabilities_json={'elicitation_url':True},caller_subject_hash=self.run.caller_subject_hash,external_session_hash=hash_token('session'))
        list(c.chunks(self.req,self.run,session,set()))
        payload={'id':'nexus-credential-'+aid,'result':{'action':'decline'}}
        c.handle_response(self.req,self.agent.id,payload,{'Mcp-Session-Id':'wrong'})
        self.assertEqual(AgentCredentialAuthorization.objects.get(pk=aid).status,'pending')
        c.handle_response(self.req,self.agent.id,payload,{'Mcp-Session-Id':'session'})
        self.assertEqual(AgentCredentialAuthorization.objects.get(pk=aid).status,'cancelled')

    def test_form_only_client_gets_no_secret_form(self):
        self.begin()
        session=SimpleNamespace(client_capabilities_json={'elicitation':True},caller_subject_hash=self.run.caller_subject_hash)
        self.assertEqual(list(c.chunks(self.req,self.run,session,set())),[])

    def test_disabled_agent_bad_callback_and_public_origin(self):
        from apps.agents.runtime_services import AgentRuntimeNotFound
        with self.assertRaises(AgentRuntimeNotFound):c.request_authorization(self.run.id,'wrong','anthropic')
        with override_settings(NEXUS_AGENT_CREDENTIAL_ENABLED_AGENTS=[]):
            with self.assertRaises(exceptions.PermissionDenied):self.begin()
        with override_settings(NEXUS_PUBLIC_BASE_URL='http://unsafe'):
            with self.assertRaises(exceptions.APIException):self.begin()

    def test_browser_endpoint_no_store_auth_and_csrf(self):
        from django.test import Client
        aid=self.begin()['authorization_id']; path='/api/v1/agent-authorizations/'+aid+'/'
        client=Client(enforce_csrf_checks=True)
        page=client.get(path+'page/',secure=True)
        self.assertEqual(page.status_code,200)
        self.assertIn("frame-ancestors 'none'",page['Content-Security-Policy'])
        self.assertIn('no-store',page['Cache-Control'])
        self.assertNotIn(b'__NONCE__',page.content)
        self.assertIn(client.get(path,secure=True).status_code,(401,403))
        client.force_login(self.user)
        self.assertEqual(client.get(path,secure=True).status_code,200)
        body={'action':'authorize','secret':self.secret,'consent':True}
        self.assertEqual(client.post(path,body,content_type='application/json',secure=True).status_code,403)
        response=client.post(path,body,content_type='application/json',secure=True,
            HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value,HTTP_ORIGIN='https://testserver')
        self.assertEqual(response.status_code,200,response.content)
        self.assertNotIn(self.secret.encode(),response.content)
        self.assertIn('no-store',response['Cache-Control'])

    def test_stateless_relay_gets_caller_bound_mcp_session(self):
        from apps.agents.models import AgentRuntimeDeployment, EdgeNode, EdgeAgentRegistration
        from apps.agents.runtime_services import finalize_mcp_session_response
        node=EdgeNode.objects.create(tenant=self.tenant,router_id='test',domain_id='test',device_token_hash='b'*64)
        registration=EdgeAgentRegistration.objects.create(node=node,agent=self.agent,origin='agent://test/test',route_id='test',lease_expires_at=timezone.now()+timedelta(hours=1),last_renewed_at=timezone.now())
        runtime=AgentRuntimeDeployment.objects.create(agent=self.agent,tenant=self.tenant,runtime_kind='openwrt_relay',edge_registration=registration)
        response_headers={}
        session=finalize_mcp_session_response(request=self.req,tenant=self.tenant,agent=self.agent,runtime=runtime,
            method='POST',status_code=200,response_headers=response_headers,session=None,external_session_id='',
            request_body=json.dumps({'method':'initialize','params':{'capabilities':{'elicitation':{'url':{}}}}}).encode())
        self.assertTrue(session.client_capabilities_json['elicitation_url'])
        self.assertEqual(session.caller_subject_hash,self.run.caller_subject_hash)
        self.assertTrue(response_headers['Mcp-Session-Id'])
