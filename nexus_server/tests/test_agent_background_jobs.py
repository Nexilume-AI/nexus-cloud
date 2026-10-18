import asyncio
import json
import tempfile
import uuid
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase, SimpleTestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import exceptions
from apps.agents import background_jobs as jobs
from apps.agents.models import AgentBackgroundJob
from tests.test_provider_credentials import CredentialsTests


@override_settings(NEXUS_PUBLIC_BASE_URL='https://cloud.example.test')
class BackgroundJobTests(TestCase):
    setUp=CredentialsTests.setUp
    new_run=CredentialsTests.new_run
    begin=CredentialsTests.begin
    approve=CredentialsTests.approve

    def create(self):
        aid=self.begin()['authorization_id'];self.approve(aid)
        config=jobs.register(self.run.id,'test-callback',{'authorization_id':aid,'request_id':str(uuid.uuid4())})
        self.jid=config['job_ref'];self.token=config['token'];return config

    def event(self,seq=1,kind='state',data=None):
        return {'seq':seq,'kind':kind,'data':data or {'status':'running'},'time':timezone.now().timestamp()}

    @patch.object(jobs,'notify')
    def test_replay_dedup_gap_conflict_and_notification_retry(self,notify):
        self.create();e=self.event()
        self.assertEqual(jobs.ingest(self.jid,self.token,[e])['ack'],1)
        self.assertEqual(jobs.ingest(self.jid,self.token,[e])['ack'],1)
        self.assertEqual(len(jobs.replay(self.jid,0)),1);self.assertEqual(jobs.replay(self.jid,1),[])
        self.assertEqual(notify.call_count,2)
        for value in [self.event(3),{**e,'data':{'status':'failed'}}]:
            with self.assertRaises(exceptions.ValidationError):jobs.ingest(self.jid,self.token,[value])
        notify.side_effect=exceptions.APIException('Redis unavailable')
        e2=self.event(2)
        with self.assertRaises(exceptions.APIException):jobs.ingest(self.jid,self.token,[e2])
        self.assertEqual(AgentBackgroundJob.objects.get(pk=self.jid).last_seq,2)
        notify.side_effect=None
        self.assertEqual(jobs.ingest(self.jid,self.token,[e2])['ack'],2)

    def test_owner_and_producer_boundaries(self):
        config=self.create();row=jobs.owner(self.req,self.jid)
        self.assertNotIn(self.token,json.dumps(jobs.snapshot(row)))
        self.assertNotIn(self.token,row.push_secret)
        self.req.user=self.other
        with self.assertRaises(exceptions.NotFound):jobs.owner(self.req,self.jid)
        for token in ('', 'wrong'):
            with self.assertRaises(exceptions.PermissionDenied):jobs.producer(self.jid,token)
        other=self.new_run(caller_subject_hash='another-caller')
        with self.assertRaises(exceptions.NotFound):jobs.register(other.id,'test-callback',{'authorization_id':str(row.authorization_id),'request_id':str(uuid.uuid4())})

    @patch.object(jobs,'notify')
    def test_manifest_verified_private_upload_and_terminal_state(self,notify):
        import hashlib
        self.create();content=b'public test report';checksum=hashlib.sha256(content).hexdigest()
        item={'path':'artifacts/report.txt','bytes':len(content),'sha256':checksum}
        with tempfile.TemporaryDirectory() as directory,override_settings(NEXUS_PRIVATE_JOB_ROOT=directory):
            with self.assertRaises(exceptions.ValidationError):jobs.upload(self.jid,self.token,item['path'],SimpleUploadedFile('x',content))
            jobs.ingest(self.jid,self.token,[self.event(data={'status':'succeeded','artifacts':[item]})])
            with self.assertRaises(exceptions.ValidationError):jobs.upload(self.jid,self.token,item['path'],SimpleUploadedFile('x',b'bad'))
            jobs.upload(self.jid,self.token,item['path'],SimpleUploadedFile('x',content))
            row=AgentBackgroundJob.objects.get(pk=self.jid)
            with jobs.storage().open(row.artifacts[item['path']]['storage_name']) as file:self.assertEqual(file.read(),content)
            self.assertNotIn('storage_name',json.dumps(jobs.snapshot(row)))
            with self.assertRaises(exceptions.ValidationError):jobs.ingest(self.jid,self.token,[self.event(2,data={'status':'running'})])
        for path in ('../secret','/secret','a\\b','a\r\nb'):
            with self.assertRaises(exceptions.ValidationError):jobs.artifact_path(path)

    def test_secret_fields_and_bad_sequences_are_rejected(self):
        for e in [self.event(data={'secret':'never'}),self.event(True),self.event(kind='unknown'),self.event(kind='log',data={'text':'x'*16385})]:
            with self.assertRaises(exceptions.ValidationError):jobs.validate_event(e)


@override_settings(REDIS_URL='redis://127.0.0.1:6379/15')
class StreamTests(SimpleTestCase):
    def test_http_accept_sse_is_not_rejected_by_json_renderer(self):
        from rest_framework.test import APIRequestFactory,force_authenticate
        from apps.agents.background_views import EventsView
        request=APIRequestFactory().get('/events/',HTTP_ACCEPT='text/event-stream',HTTP_LAST_EVENT_ID='2')
        force_authenticate(request,user=SimpleNamespace(is_authenticated=True))
        with patch.object(jobs,'owner',return_value=SimpleNamespace(last_seq=3)):
            response=EventsView.as_view()(request,job_id=uuid.uuid4())
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['Content-Type'],'text/event-stream')
        response.close()

    def test_subscription_is_acknowledged_before_replay_and_cursor_is_preserved(self):
        from unittest.mock import AsyncMock, Mock
        order=[];pubsub=Mock();pubsub.subscribe=AsyncMock(side_effect=lambda *a:order.append('subscribe'));pubsub.aclose=AsyncMock()
        pubsub.get_message=AsyncMock(return_value={'type':'subscribe'})
        client=Mock();client.pubsub.return_value=pubsub;client.aclose=AsyncMock()
        async def exercise():
            iterator=jobs.stream(uuid.uuid4(),7)
            packet=await anext(iterator);await iterator.aclose();return packet
        def replay(jid,after):
            order.append('replay');self.assertEqual(after,7)
            return [{'seq':8,'kind':'log','data':{'text':'hello'},'time':0}]
        with patch.object(jobs.redis_async.Redis,'from_url',return_value=client),patch.object(jobs,'replay',side_effect=replay):
            packet=asyncio.run(exercise())
        self.assertEqual(order,['subscribe','replay']);self.assertIn('id: 8',packet)
        pubsub.aclose.assert_awaited_once();client.aclose.assert_awaited_once()
