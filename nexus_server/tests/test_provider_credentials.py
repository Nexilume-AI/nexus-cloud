import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework import exceptions
from apps.agents.models import Agent, AgentDisplayRun, AgentCredentialAuthorization, AgentDisplayEvent
from apps.agents import credential_services as c
from apps.agents.runtime_services import _mcp_client_capabilities
from apps.tenancy.models import Tenant, Project
from apps.common.subjects import request_subject, hash_token


class ProviderEndpointTests(SimpleTestCase):
    def test_endpoint_configuration_rejects_unsafe_destinations(self):
        from apps.agents.credential_config import configuration
        for url in ('http://example.com/v1','https://user:key@example.com/v1','https://127.0.0.1/v1','https://[::1]/v1','https://api.example/v1?q=key','https://api.example/../admin','https://localhost/v1'):
            with self.subTest(url=url),self.assertRaises(exceptions.ValidationError):configuration('custom_openai',url,'gpt-6-sol')
        self.assertEqual(configuration('custom_openai','https://API.EXAMPLE:443/v1/','gpt-6-sol')[1],'https://api.example/v1')
        with self.assertRaises(exceptions.ValidationError):configuration('openai','https://another.example/v1','gpt-6-sol')

@override_settings(NEXUS_PUBLIC_BASE_URL='https://cloud.example.test', ROOT_URLCONF='tests.provider_credential_urls')
class CredentialsTests(TestCase):
    def test_endpoint_and_model_scope_are_bound_to_approval(self):
        value=c.request_authorization(self.run.id,'test-callback','custom_openai','https://api.example/v1','gpt-6-sol')
        self.req.data={'action':'authorize','model':'gpt-6-sol','secret':self.secret,'consent':True}
        with patch('apps.gateway.provider_http.resolve_provider_endpoint') as resolver:
            result=c.decide(self.req,value['authorization_id'])
            resolver.assert_called_once_with('https://api.example/v1',public_https_only=True)
        self.assertEqual((result['base_url'],result['model']),('https://api.example/v1','gpt-6-sol'))
        grant=c.internal_status(self.run.id,'test-callback',value['authorization_id'],True)
        self.assertEqual((grant['base_url'],grant['model'],grant['protocol']),('https://api.example/v1','gpt-6-sol','openai'))
        another=self.new_run()
        self.assertEqual(c.request_authorization(another.id,'test-callback','custom_openai','https://api.example/v1','gpt-6-sol')['status'],'authorized')
        for url,model in [('https://another.example/v1','gpt-6-sol'),('https://api.example/v1','other-model')]:
            pending=c.request_authorization(self.new_run().id,'test-callback','custom_openai',url,model)
            self.assertEqual(pending['status'],'pending')
        self.req.data={'action':'authorize','secret':self.secret,'consent':True,'base_url':'https://another.example/v1'}
        with self.assertRaises(exceptions.ValidationError):c.decide(self.req,value['authorization_id'])

    def test_saved_credentials_are_same_caller_same_endpoint_and_revocable(self):
        root=self.begin()['authorization_id'];self.approve(root)
        run=self.new_run()
        child=c.request_authorization(run.id,'test-callback','anthropic',model='another-model')['authorization_id']
        detail=c.browser_details(c.browser_row(self.req,child))
        self.assertEqual([x['authorization_id'] for x in detail['saved_credentials']],[root])
        self.assertNotIn(self.secret,json.dumps(detail))
        self.req.data={'action':'authorize','saved_authorization_id':root,'consent':True,'model':'another-model','ttl_seconds':86400}
        result=c.decide(self.req,child)
        self.assertLessEqual(result['expires_at'],c.public(AgentCredentialAuthorization.objects.get(pk=root))['expires_at'])
        self.assertEqual(AgentCredentialAuthorization.objects.get(pk=child).encrypted_secret,'')
        self.assertEqual(c.internal_status(run.id,'test-callback',child,True)['secret'],self.secret)
        self.req.data={'action':'revoke'};c.decide(self.req,root)
        self.assertEqual(c.internal_status(run.id,'test-callback',child)['status'],'revoked')
        with self.assertRaises(exceptions.PermissionDenied):c.internal_status(run.id,'test-callback',child,True)

    def test_saved_credential_cannot_be_retargeted(self):
        root=self.begin()['authorization_id'];self.approve(root)
        child=c.request_authorization(self.new_run().id,'test-callback','openai',model='gpt-6-sol')['authorization_id']
        self.req.data={'action':'authorize','saved_authorization_id':root,'model':'gpt-6-sol','consent':True}
        with self.assertRaises(exceptions.ValidationError):c.decide(self.req,child)
        self.assertEqual(AgentCredentialAuthorization.objects.get(pk=child).status,'pending')

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
        self.req.data={'action':'authorize','secret':self.secret,'consent':True,'ttl_seconds':300,'model':'test-model'}
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
        body={'action':'authorize','secret':self.secret,'consent':True,'model':'test-model'}
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

    def test_personal_nexus_uses_explicit_credential_not_installation_key(self):
        value=c.request_authorization(self.run.id,'test-callback','nexus',model='test-model')
        detail=c.browser_details(c.browser_row(self.req,value['authorization_id']))
        self.assertFalse(detail['nexus_router_delegation'])
        self.assertEqual(detail['nexus_routers'],[])
        self.req.data={'action':'authorize','model':'test-model','consent':True}
        with self.assertRaises(exceptions.ValidationError):c.decide(self.req,value['authorization_id'])
        self.req.data['secret']=self.secret
        c.decide(self.req,value['authorization_id'])
        self.assertEqual(c.internal_status(self.run.id,'test-callback',value['authorization_id'],True)['secret'],self.secret)

    def test_personal_router_delegation_fails_closed(self):
        from apps.agents.credential_router import issue
        with self.assertRaises(exceptions.PermissionDenied):issue(None,'router','model')
