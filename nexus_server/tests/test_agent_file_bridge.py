"""Uses a dedicated Redis database supplied by the isolated test runner."""
import hashlib
import json
import threading
import uuid
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIRequestFactory
from apps.agents.models import Agent, AgentDisplayRun
from apps.tenancy.models import Tenant
from apps.common.subjects import hash_token
from apps.agents import file_bridge_views as v


@override_settings(NEXUS_PUBLIC_BASE_URL='https://cloud.example.test')
class FileBridgeTests(TestCase):
    def setUp(self):
        self.prefix = patch.object(v,'PREFIX','test:file-bridge:'+uuid.uuid4().hex+':');self.prefix.start()
        self.user=get_user_model().objects.create_user(username='bridge-owner')
        self.tenant=Tenant.objects.create(name='Bridge',slug='bridge')
        self.agent=Agent.objects.create(name='Bridge',tenant=self.tenant,created_by=self.user,status='active')
        self.run=AgentDisplayRun.objects.create(agent=self.agent,tenant=self.tenant,consumer_tenant=self.tenant,
            run_kind='invocation',caller_principal_type='user',caller_principal_id=str(self.user.pk),
            caller_subject_hash='a'*64,interaction_token_hash=hash_token('callback'),
            interaction_token_expires_at=timezone.now()+timedelta(hours=1),status='running')
        self.factory=APIRequestFactory()

    def tearDown(self):
        client=v.db()
        for key in client.scan_iter(v.PREFIX+'*'):client.delete(key)
        self.prefix.stop()

    def create(self,mode='download',size=10):
        r=v.CreateBridge.as_view()(self.factory.post('/',{'name':'weights.safetensors','mode':mode,'size_bytes':size,'sha256':'a'*64},format='json',HTTP_X_NEXUS_INTERACTION_TOKEN='callback'),run_id=self.run.pk)
        self.assertEqual(r.status_code,200,r.data);return r.data

    def redeem(self,info):
        ticket=parse_qs(urlsplit(info['url']).fragment)['ticket'][0]
        return v.Redeem.as_view()(self.factory.post('/',{'ticket':ticket},format='json'),bridge_id=info['bridge_id'])

    def file(self,info,cookie='',method='get',data=None,**headers):
        req=getattr(self.factory,method)('/',data,content_type='application/octet-stream',**headers)
        req.COOKIES[v.COOKIE]=cookie
        return v.File.as_view()(req,bridge_id=info['bridge_id'])

    def test_capability_single_use_scope_expiry_and_revocation(self):
        a=self.create();b=self.create()
        self.assertEqual(self.file(a).status_code,403)
        r=self.redeem(a);cookie=r.cookies[v.COOKIE]
        self.assertTrue(cookie['secure']);self.assertTrue(cookie['httponly'])
        self.assertEqual(self.redeem(a).status_code,403)
        self.assertEqual(self.file(b,cookie.value,method='head').status_code,403)
        self.assertEqual(self.file(a,cookie.value,method='head').status_code,200)
        self.user.is_active=False;self.user.save()
        self.assertEqual(self.file(a,cookie.value,method='head').status_code,404)
        self.user.is_active=True;self.user.save()
        req=self.factory.delete('/',HTTP_X_NEXUS_FILE_WORKER=a['_runtime']['token'])
        self.assertEqual(v.Runtime.as_view()(req,bridge_id=a['bridge_id']).status_code,200)
        self.assertEqual(self.file(a,cookie.value,method='head').status_code,404)

    def test_ranges_if_range_bounded_streaming(self):
        a=self.create(size=v.CHUNK+17);cookie=self.redeem(a).cookies[v.COOKIE].value
        with patch.object(v,'exchange',side_effect=lambda bid,op,offset,length: b'x'*length) as x:
            r=self.file(a,cookie,HTTP_RANGE='bytes=4-12');self.assertEqual(r.status_code,206)
            self.assertEqual(r['Content-Range'],f'bytes 4-12/{v.CHUNK+17}');self.assertEqual(b''.join(r.streaming_content),b'x'*9)
            self.assertEqual(self.file(a,cookie,HTTP_RANGE='bytes=999999999-').status_code,416)
            self.assertEqual(self.file(a,cookie,HTTP_RANGE='bytes=1-2,5-6').status_code,416)
            r=self.file(a,cookie,HTTP_RANGE='bytes=4-12',HTTP_IF_RANGE='"other"');self.assertEqual(r.status_code,200)
            chunks=list(r.streaming_content);self.assertEqual(list(map(len,chunks)),[v.CHUNK,17])
            self.assertTrue(all(call.args[3]<=v.CHUNK for call in x.call_args_list))

    def test_full_chunk_upload_and_integrity(self):
        a=self.create(mode='upload',size=v.CHUNK+1);cookie=self.redeem(a).cookies[v.COOKIE].value
        body=b'x'*v.CHUNK
        with patch.object(v,'exchange',return_value=json.dumps({'offset':len(body)}).encode()) as x:
            r=self.file(a,cookie,method='put',data=body,HTTP_X_NEXUS_UPLOAD_OFFSET='0',HTTP_X_NEXUS_CHUNK_SHA256=hashlib.sha256(body).hexdigest())
            self.assertEqual(r.status_code,200,r.data)
            self.assertEqual(int(self.file(a,cookie,method='head')['X-Nexus-Upload-Offset']),len(body))
            r=self.file(a,cookie,method='put',data=body,HTTP_X_NEXUS_UPLOAD_OFFSET='0',HTTP_X_NEXUS_CHUNK_SHA256='bad')
            self.assertEqual(r.status_code,400);self.assertEqual(x.call_count,1)
        self.assertEqual(self.file(a,cookie).status_code,403)

    def test_worker_command_exchange_and_isolation(self):
        a=self.create();b=self.create()
        req=self.factory.get('/',HTTP_X_NEXUS_FILE_WORKER=b['_runtime']['token'])
        self.assertEqual(v.Runtime.as_view()(req,bridge_id=a['bridge_id']).status_code,403)
        # Exercise the real Redis queue and single-response acknowledgement.
        row=v.load(a['bridge_id']);output=[]
        def exchange():
            with patch.object(v,'load',return_value=row):output.append(v.exchange(a['bridge_id'],'read',0,10))
        t=threading.Thread(target=exchange);t.start()
        r=v.Runtime.as_view()(self.factory.get('/',HTTP_X_NEXUS_FILE_WORKER=a['_runtime']['token']),bridge_id=a['bridge_id'])
        cid=r.data['command']['id']
        def answer():return v.Command.as_view()(self.factory.post('/',b'O1234567890',content_type='application/octet-stream',HTTP_X_NEXUS_FILE_WORKER=a['_runtime']['token']),bridge_id=a['bridge_id'],command_id=cid)
        self.assertEqual(answer().status_code,200);t.join(5);self.assertEqual(output,[b'1234567890'])
        self.assertEqual(answer().status_code,404)

    def test_inactive_run_and_metadata_rejected(self):
        self.run.status='completed';self.run.save()
        r=v.CreateBridge.as_view()(self.factory.post('/',{},format='json',HTTP_X_NEXUS_INTERACTION_TOKEN='callback'),run_id=self.run.pk)
        self.assertIn(r.status_code,(400,403))
