import secrets
import uuid
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory
from apps.agents.models import Agent, AgentDisplayRun, AgentInteractiveView, AgentViewAction
from apps.agents import interactive_views as v
from apps.common.subjects import hash_token
from apps.tenancy.models import Tenant


@override_settings(NEXUS_PUBLIC_BASE_URL='https://cloud.example.test')
class InteractiveViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='view-owner')
        self.other = get_user_model().objects.create_user(username='view-other')
        self.tenant = Tenant.objects.create(name='Views', slug='views')
        self.agent = Agent.objects.create(name='View Agent', tenant=self.tenant, created_by=self.user, status='active')
        self.run = self.new_run()
        self.definition = {'title': 'Counter', 'actions': {'increment': {'label': 'Increment', 'input_schema': {
            'type': 'object', 'properties': {'amount': {'type': 'integer', 'minimum': 1, 'maximum': 10}},
            'required': ['amount'], 'additionalProperties': False}}}}

    def new_run(self, **kw):
        return AgentDisplayRun.objects.create(**(dict(agent=self.agent, tenant=self.tenant, consumer_tenant=self.tenant,
            run_kind='invocation', caller_principal_type='user', caller_principal_id=str(self.user.pk),
            caller_subject_hash='a'*64, interaction_token_hash=hash_token('callback'),
            interaction_token_expires_at=timezone.now()+timedelta(hours=1), status='running') | kw))

    def req(self, cls, data=None, method='post', view_id=None, cookie='', token='', run=None, **headers):
        request = getattr(APIRequestFactory(), method)('/', data, format='json',
            HTTP_X_NEXUS_INTERACTION_TOKEN='callback', HTTP_X_NEXUS_VIEW_TOKEN=token, **headers)
        if cookie: request.COOKIES[v.COOKIE] = cookie
        kwargs = {'run_id': (run or self.run).id} if cls == v.InternalView else {}
        if view_id: kwargs['view_id'] = view_id
        with patch.object(v, 'notify'):
            return cls.as_view()(request, **kwargs)

    def create(self, **overrides):
        response = self.req(v.InternalView, {'definition': self.definition, 'state': {'count': 0}, **overrides})
        self.assertEqual(response.status_code, 200, response.data)
        info = response.data
        self.vid = info['view_id']; self.token = info['_runtime']['token']
        self.ticket = parse_qs(urlsplit(info['url']).fragment)['ticket'][0]
        response = self.req(v.RedeemView, {'ticket': self.ticket}, view_id=self.vid, HTTP_X_NEXUS_VIEW='1')
        self.assertEqual(response.status_code, 200, response.data)
        self.cookie = response.cookies[v.COOKIE].value
        return info

    def runtime(self, payload):
        return self.req(v.RuntimeView, payload, view_id=self.vid, token=self.token)

    def submit(self, **kw):
        data = {'request_id': str(uuid.uuid4()), 'name': 'increment', 'arguments': {'amount': 2}, **kw}
        return self.req(v.ActionView, data, view_id=self.vid, cookie=self.cookie, HTTP_X_NEXUS_VIEW='1'), data

    def test_ticket_browser_runtime_and_caller_are_separate(self):
        info = self.create()
        self.assertEqual(self.req(v.RedeemView, {'ticket': self.ticket}, view_id=self.vid, HTTP_X_NEXUS_VIEW='1').status_code, 403)
        self.assertEqual(self.req(v.StateView, method='get', view_id=self.vid, token=self.token).status_code, 403)
        self.assertEqual(self.req(v.RuntimeView, method='get', view_id=self.vid, cookie=self.cookie).status_code, 403)
        other = self.new_run(caller_principal_id=str(self.other.pk))
        self.assertEqual(self.req(v.InternalView, {'action': 'ticket'}, run=other, view_id=self.vid).status_code, 404)
        self.assertEqual(self.req(v.InternalView, {'action': 'ticket'}, run=self.new_run(), view_id=self.vid).status_code, 200)
        state = self.req(v.StateView, method='get', view_id=self.vid, cookie=self.cookie).data
        for secret in (self.token, self.ticket, info['_runtime']['url']): self.assertNotIn(secret, str(state))
        row = AgentInteractiveView.objects.get(pk=self.vid)
        self.assertNotEqual(row.producer_hash, self.token)

    def test_actions_schema_idempotency_claim_and_result_after_run_finishes(self):
        self.create(); self.run.status='completed'; self.run.save()
        self.assertEqual(self.runtime({'operation': 'heartbeat'}).status_code, 200)
        response, data = self.submit(); self.assertEqual(response.status_code, 202)
        self.assertEqual(self.submit(**data)[0].status_code, 200)
        self.assertEqual(self.submit(**{**data, 'arguments': {'amount': 3}})[0].status_code, 400)
        self.assertEqual(AgentViewAction.objects.count(), 1)
        self.assertEqual(self.submit(arguments={'amount': 'bad'})[0].status_code, 400)
        self.assertEqual(self.submit(name='shell')[0].status_code, 400)
        lease = secrets.token_urlsafe(32)
        claim = {'operation': 'claim', 'request_id': data['request_id'], 'lease': lease}
        self.assertTrue(self.runtime(claim).data['claimed'])
        self.assertFalse(self.runtime(claim).data['claimed'])
        complete = {**claim, 'operation': 'complete', 'result': {'count': 2}}
        self.assertEqual(self.runtime({**complete, 'lease': 'x'*40}).status_code, 403)
        self.assertEqual(self.runtime(complete).data['status'], 'succeeded')
        self.assertEqual(self.runtime(complete).data['status'], 'succeeded')
        state = self.req(v.RuntimeView, method='get', view_id=self.vid, token=self.token).data
        self.assertEqual(self.runtime({'operation': 'update', 'revision': state['revision']-1, 'state': {}}).status_code, 409)
        self.assertEqual(self.runtime({'operation': 'update', 'revision': state['revision'], 'state': {'count': 2}}).status_code, 200)

    def test_timeout_never_requeues_uncertain_side_effects(self):
        self.create(); self.runtime({'operation': 'heartbeat'})
        _, data = self.submit(); lease=secrets.token_urlsafe(32)
        self.runtime({'operation': 'claim', 'request_id': data['request_id'], 'lease': lease})
        AgentViewAction.objects.update(deadline=timezone.now()-timedelta(seconds=1))
        result=self.runtime({'operation': 'claim', 'request_id': data['request_id'], 'lease': lease})
        self.assertFalse(result.data['claimed']); self.assertEqual(result.data['status'], 'expired')
        self.assertEqual(self.submit(**data)[0].data['status'], 'expired')

    def test_csrf_disconnect_close_and_limits(self):
        self.create()
        self.assertEqual(self.submit()[0].status_code, 500)
        self.runtime({'operation': 'heartbeat'})
        self.assertEqual(self.req(v.ActionView, {}, view_id=self.vid, cookie=self.cookie).status_code, 403)
        self.assertEqual(self.req(v.ActionView, {}, view_id=self.vid, cookie=self.cookie, HTTP_X_NEXUS_VIEW='1', HTTP_ORIGIN='https://evil.example').status_code, 403)
        for _ in range(16): self.assertEqual(self.submit()[0].status_code, 202)
        self.assertEqual(self.submit()[0].status_code, 429)
        self.runtime({'operation': 'close'})
        self.assertEqual(self.req(v.StateView, method='get', view_id=self.vid, cookie=self.cookie).status_code, 404)
        self.assertEqual(self.runtime({'operation': 'heartbeat'}).status_code, 404)

    def test_custom_sandbox_and_schema_ref_rejected(self):
        self.create(definition={**self.definition, 'template':'custom','html':'<script>document.cookie</script>'})
        result=self.req(v.CustomView,method='get',view_id=self.vid,cookie=self.cookie)
        policy=result['Content-Security-Policy']
        self.assertIn('sandbox allow-scripts allow-forms;',policy);self.assertNotIn('allow-same-origin',policy)
        self.assertIn("connect-src 'none'",policy);self.assertNotIn(self.token,result.content.decode())
        self.definition['actions']['increment']['input_schema']['$ref']='https://evil.example/schema'
        self.assertEqual(self.req(v.InternalView,{'definition':self.definition}).status_code,400)
        self.assertEqual(self.req(v.InternalView,{'definition':{},'ttl_seconds':3601}).status_code,400)

    def test_expired_and_revoked_owner(self):
        self.create()
        AgentInteractiveView.objects.filter(pk=self.vid).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.req(v.StateView,method='get',view_id=self.vid,cookie=self.cookie).status_code,404)
