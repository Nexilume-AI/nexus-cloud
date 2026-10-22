from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIRequestFactory
from apps.agents.models import Agent, AgentDisplayRun, AgentFileTransfer, AgentWaveView
from apps.tenancy.models import Tenant
from apps.common.subjects import hash_token
from apps.agents import wave_views as v


@override_settings(NEXUS_PUBLIC_BASE_URL='https://cloud.example.test')
class WaveViewsTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user(username='wave-owner')
        self.other=get_user_model().objects.create_user(username='wave-other')
        self.tenant=Tenant.objects.create(name='Wave Test',slug='wave-test')
        self.agent=Agent.objects.create(name='Wave Test',tenant=self.tenant,created_by=self.user)
        self.run=self.new_run()

    def new_run(self, **kwargs):
        return AgentDisplayRun.objects.create(**(dict(agent=self.agent,tenant=self.tenant,consumer_tenant=self.tenant,
            run_kind='invocation',caller_principal_type='user',caller_principal_id=str(self.user.pk),
            caller_subject_hash='a'*64,interaction_token_hash=hash_token('test-callback'),
            interaction_token_expires_at=timezone.now()+timedelta(hours=1),status='running')|kwargs))

    def request(self, cls, data=None, method='post', run=None, view_id=None, cookie='', **extra):
        req = getattr(APIRequestFactory(), method)('/', data, format='json',
            HTTP_X_NEXUS_INTERACTION_TOKEN='test-callback', **extra)
        if cookie:
            req.COOKIES[v.COOKIE] = cookie
        kw = {'run_id': (run or self.run).id} if cls == v.InternalWaveView else {}
        if view_id:
            kw['view_id'] = view_id
        with patch.object(v, 'notify'):
            return cls.as_view()(req, **kw)

    def create(self):
        self.agent.status='active';self.agent.save()
        f = AgentFileTransfer.objects.create(agent=self.agent, tenant=self.tenant, run=self.run,
            caller_subject_hash='unused', direction='output', state='ready', name='wave.fst',
            size_bytes=42, storage_backend='local', object_key='test.fst', expires_at=timezone.now()+timedelta(hours=2))
        data = {'file_ids':[str(f.id)], 'snapshot':{'panels':[{'label':'Counter','commands':'variable_add top.count;zoom_fit;cursor_set 40'}]}}
        result = self.request(v.InternalWaveView, data)
        self.assertEqual(result.status_code, 200, result.data)
        return result.data, data

    def redeem(self, info):
        token = parse_qs(urlsplit(info['url']).fragment)['ticket'][0]
        return self.request(v.RedeemView, {'ticket': token}, view_id=info['public_view_id'])

    def test_single_use_cookie_refresh_private_files_and_close(self):
        info, _ = self.create();vid = info['public_view_id']
        self.assertNotIn('ticket=', urlsplit(info['url']).query)
        self.assertEqual(self.request(v.StateView, method='get', view_id=vid).status_code, 403)
        redeemed=self.redeem(info);self.assertEqual(redeemed.status_code,200)
        cookie=redeemed.cookies[v.COOKIE]
        self.assertTrue(cookie['secure']);self.assertTrue(cookie['httponly']);self.assertEqual(cookie['samesite'],'Strict')
        self.assertEqual(self.redeem(info).status_code,403)
        for _ in range(2):
            self.assertEqual(self.request(v.StateView, method='get',view_id=vid,cookie=cookie.value).status_code,200)
        other=self.new_run(caller_principal_id=str(self.other.pk))
        self.assertEqual(self.request(v.InternalWaveView,{'action':'ticket'},run=other,view_id=vid).status_code,404)
        same=self.new_run()
        self.assertEqual(self.request(v.InternalWaveView,{'action':'ticket'},run=same,view_id=vid).status_code,200)
        self.assertEqual(self.request(v.InternalWaveView,{'action':'close'},view_id=vid).status_code,200)
        self.assertEqual(self.request(v.StateView,method='get',view_id=vid,cookie=cookie.value).status_code,404)

    def test_file_provenance_and_command_injection_rejected(self):
        info,data=self.create()
        self.assertEqual(self.request(v.InternalWaveView,data,run=self.new_run()).status_code,400)
        for command in ('variable_add top.a;load_url https://evil.test/x','variable_add top.a\nload_url x','surver_select_file /etc/passwd','script_eval x'):
            data['snapshot']['panels'][0]['commands']=command
            self.assertEqual(self.request(v.InternalWaveView,data).status_code,400)
        self.assertEqual(self.request(v.InternalWaveView,{'action':'update','snapshot':{'panels':[]}},view_id=info['public_view_id']).status_code,400)

    def test_expired_ticket_view_and_browser_csrf(self):
        info,_=self.create();vid=info['public_view_id']
        AgentWaveView.objects.filter(pk=vid).update(ticket_expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.redeem(info).status_code,403)
        info=self.request(v.InternalWaveView,{'action':'ticket'},view_id=vid).data
        cookie=self.redeem(info).cookies[v.COOKIE].value
        self.assertEqual(self.request(v.StateView,{'status':'ready'},view_id=vid,cookie=cookie).status_code,403)
        self.assertEqual(self.request(v.StateView,{'status':'ready'},view_id=vid,cookie=cookie,HTTP_X_NEXUS_WAVE='1',HTTP_ORIGIN='https://evil.test').status_code,403)
        self.assertEqual(self.request(v.StateView,{'status':'ready'},view_id=vid,cookie=cookie,HTTP_X_NEXUS_WAVE='1').status_code,200)
        AgentWaveView.objects.filter(pk=vid).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.request(v.StateView,method='get',view_id=vid,cookie=cookie).status_code,404)

    def test_snapshot_is_minimized_and_asset_path_allowlisted(self):
        info,_=self.create();vid=info['public_view_id'];cookie=self.redeem(info).cookies[v.COOKIE].value
        request=APIRequestFactory().get('/');request.COOKIES[v.COOKIE]=cookie
        self.assertEqual(v.AssetView.as_view()(request,view_id=vid,name='../settings.py').status_code,404)
        self.assertEqual(v.WaveFileView.as_view()(request,view_id=vid,index=99).status_code,404)
        row=AgentWaveView.objects.get(pk=vid)
        self.assertNotIn('ticket',str(row.snapshot));self.assertNotIn('object_key',str(row.snapshot))

    def test_disabled_owner_invalidates_browser_cookie(self):
        info,_=self.create();vid=info['public_view_id'];cookie=self.redeem(info).cookies[v.COOKIE].value
        self.user.is_active=False;self.user.save()
        self.assertEqual(self.request(v.StateView,method='get',view_id=vid,cookie=cookie).status_code,404)
