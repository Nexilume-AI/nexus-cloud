"""Explicit MCP preauthorization: transport isolation and caller/run scopes."""
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase, RequestFactory, override_settings
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.test import APIRequestFactory, force_authenticate
from apps.agents import credential_preauthorization as p, credential_services as c
from apps.agents.models import AgentCredentialAuthorization, AgentDisplayEvent
from apps.common.crypto import decrypt_secret
from tests.test_provider_credentials import CredentialsTests

SECRET = 'synthetic-preauthorized-key'
PATH = '/api/v1/agents/00000000-0000-0000-0000-000000000001/mcp/'

def headers(**changes):
    values = {'PREAUTHORIZE':'true','ENDPOINT':'https://api.example/v1','MODEL':'configured-model','KEY':SECRET}
    values.update(changes)
    return {p.PREFIX+k:v for k,v in values.items() if v is not None}


class PreauthorizationTransportTests(SimpleTestCase):
    def test_opt_in_complete_bundle_and_safe_destinations(self):
        for changes in ({'PREAUTHORIZE':None},{'PREAUTHORIZE':'false'},{'MODEL':None},{'KEY':None},
                        {'ENDPOINT':'https://127.0.0.1/v1'},{'ENDPOINT':'http://api.example/v1'},
                        {'ENDPOINT':'https://api.example/v1?key=secret'}, {'TTL_SECONDS':'86401'},
                        {'KEY':'bad\r\nkey'},{'UNKNOWN':'value'}):
            with self.subTest(changes=changes):
                req=RequestFactory().get(PATH,**headers(**changes))
                response=p.UpstreamPreauthorizationMiddleware(lambda r:HttpResponse('ok'))(req)
                self.assertEqual(response.status_code,400)
                self.assertNotIn(SECRET,response.content.decode())
                self.assertFalse(any(k.startswith(p.PREFIX) for k in req.META))
        for values in ({}, {p.PREFIX+'PREAUTHORIZE':'false'}):
            req=RequestFactory().get(PATH,**values);p.capture(req)
            self.assertFalse(hasattr(req,p.ATTRIBUTE))

    def test_headers_are_scrubbed_from_all_request_views_and_redaction(self):
        req=RequestFactory().get(PATH,**headers(),HTTP_AUTHORIZATION='Bearer mcp-connection-key')
        _=req.headers
        req.scope={'headers':[(b'x-nexus-upstream-key',SECRET.encode()),(b'accept',b'application/json')]}
        p.capture(req)
        self.assertNotIn(SECRET,str(req.META));self.assertNotIn(SECRET,str(req.headers));self.assertNotIn(SECRET,str(req.scope))
        self.assertNotIn(SECRET,getattr(req,p.ATTRIBUTE))
        self.assertEqual(json.loads(decrypt_secret(getattr(req,p.ATTRIBUTE)))['secret'],SECRET)
        self.assertEqual(req.META['HTTP_AUTHORIZATION'],'Bearer mcp-connection-key')
        from apps.common.redaction import redact_payload
        self.assertNotIn(SECRET,str(redact_payload({'X-Nexus-Upstream-Key':SECRET,'HTTP_X_NEXUS_UPSTREAM_KEY':SECRET})))

    def test_json_sse_and_task_transports_never_forward_upstream_headers(self):
        from apps.agents.mcp_views import AgentMCPProxyView
        from apps.agents.runtime_runner import RuntimeMCPResult, RuntimeMCPStreamResult
        for kind in ('json','sse','task'):
            with self.subTest(kind=kind):
                req=APIRequestFactory().post(PATH,{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'test','arguments':{}}},format='json',HTTP_ACCEPT='text/event-stream' if kind=='sse' else 'application/json',**headers())
                force_authenticate(req,user=SimpleNamespace(is_authenticated=True,is_active=True))
                def check(**kw):
                    self.assertFalse(any(k.upper().startswith('X-NEXUS-UPSTREAM-') for k in kw['headers']))
                    self.assertNotIn(SECRET,str(kw['headers']));self.assertNotIn(SECRET,kw['body'].decode())
                    self.assertTrue(getattr(kw['request'],p.ATTRIBUTE))
                def task(**kw):
                    check(**kw)
                    return RuntimeMCPResult(status_code=200,headers={},body=b'{}') if kind=='task' else None
                def call(**kw):
                    check(**kw);return RuntimeMCPResult(status_code=200,headers={},body=b'{}')
                def stream(**kw):
                    check(**kw);return RuntimeMCPStreamResult(status_code=200,headers={},chunks=iter([b'data: {}\n\n']))
                with patch('apps.agents.mcp_views.maybe_handle_mcp_task',side_effect=task),patch('apps.agents.mcp_views.call_runtime_mcp',side_effect=call),patch('apps.agents.mcp_views.call_runtime_mcp_stream',side_effect=stream):
                    response=p.UpstreamPreauthorizationMiddleware(lambda r:AgentMCPProxyView.as_view()(r,agent_id=PATH.split('/')[4]))(req)
                    self.assertEqual(response.status_code,200)
                    if response.streaming:self.assertNotIn(SECRET,b''.join(response.streaming_content).decode())


@override_settings(NEXUS_PUBLIC_BASE_URL='https://cloud.example.test')
class PreauthorizationGrantTests(TestCase):
    setUp=CredentialsTests.setUp
    new_run=CredentialsTests.new_run
    begin=CredentialsTests.begin
    approve=CredentialsTests.approve

    def configure(self,run=None,**changes):
        req=RequestFactory().post(PATH,**headers(**changes));p.capture(req)
        with patch('apps.gateway.provider_http.resolve_provider_endpoint') as resolver:
            p.attach(req,run or self.run)
            resolver.assert_called_once_with(changes.get('ENDPOINT','https://api.example/v1'),public_https_only=True)
        return p.current(run or self.run)

    def test_explicit_config_overrides_tool_defaults_without_exposing_secret(self):
        row=self.configure()
        result=c.request_authorization(self.run.pk,'test-callback','nexus',model='tool-default',force_new=True)
        self.assertEqual(result['model'],'configured-model');self.assertTrue(result['preauthorized'])
        self.assertEqual(result['status'],'authorized');self.assertNotIn(SECRET,json.dumps(result))
        self.assertNotIn(SECRET,row.encrypted_secret)
        self.assertEqual(c.internal_status(self.run.pk,'test-callback',row.pk,True)['secret'],SECRET)
        self.assertFalse(AgentDisplayEvent.objects.filter(run=self.run).exists())
        self.assertEqual(c.saved_grants(row).count(),0)

    def test_web_flow_remains_and_cannot_reuse_config_without_headers(self):
        row=self.configure();other=self.new_run()
        value=c.request_authorization(other.pk,'test-callback','custom_openai','https://api.example/v1','configured-model')
        self.assertEqual(value['status'],'pending')
        with self.assertRaises(exceptions.PermissionDenied):c.internal_status(other.pk,'test-callback',row.pk,True)

    def test_configuration_rotation_expiry_and_revoke_fail_closed(self):
        row=self.configure();same=self.new_run();shorter=self.configure(same,TTL_SECONDS='300')
        self.assertEqual(c.internal_status(same.pk,'test-callback',row.pk,True)['expires_at'],shorter.expires_at.isoformat())
        self.assertEqual(c.internal_status(same.pk,'test-callback',row.pk,True)['secret'],SECRET)
        for changes in ({'KEY':'a-different-synthetic-key'},{'MODEL':'different-model'},{'ENDPOINT':'https://another.example/v1'}):
            other=self.new_run();self.configure(other,**changes)
            with self.assertRaises(exceptions.PermissionDenied):c.internal_status(other.pk,'test-callback',row.pk,True)
        with patch.object(c,'owner_id',return_value=None),self.assertRaises(exceptions.PermissionDenied):c.request_authorization(self.run.pk,'test-callback','nexus')
        row.status='revoked';row.save()
        with self.assertRaises(exceptions.PermissionDenied):c.request_authorization(self.run.pk,'test-callback','nexus')
        with self.assertRaises(exceptions.PermissionDenied):c.internal_status(same.pk,'test-callback',row.pk,True)
        row.status='authorized';row.expires_at=timezone.now()-timedelta(seconds=1);row.save()
        with self.assertRaises(exceptions.PermissionDenied):c.internal_status(self.run.pk,'test-callback',row.pk,True)

    def test_scope_owner_allowlist_and_dns_are_enforced(self):
        row=self.configure()
        other=self.new_run(caller_subject_hash='other-caller')
        with self.assertRaises(exceptions.NotFound):c.internal_status(other.pk,'test-callback',row.pk,True)
        req=RequestFactory().post(PATH,**headers());p.capture(req)
        with patch.object(c,'owner_id',return_value=None),self.assertRaises(exceptions.PermissionDenied):p.attach(req,self.new_run())
        with self.settings(NEXUS_AGENT_CREDENTIAL_ENABLED_AGENTS=[]),self.assertRaises(exceptions.PermissionDenied):p.attach(req,self.new_run())
        with patch('apps.gateway.provider_http.resolve_provider_endpoint',side_effect=OSError('private endpoint')),self.assertRaises(exceptions.ValidationError):p.attach(req,self.new_run())

    def test_config_cannot_resolve_an_unrelated_web_grant(self):
        value=self.begin();self.approve(value['authorization_id'])
        configured=self.new_run();self.configure(configured)
        with self.assertRaises(exceptions.PermissionDenied):c.internal_status(configured.pk,'test-callback',value['authorization_id'],True)
