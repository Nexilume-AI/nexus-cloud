import json
from unittest.mock import patch
from asgiref.sync import async_to_sync
from apps.routers import services
from apps.gateway.models import GatewayRequestLog
from .test_router_cascade import RouterCascadeTests


class AutoCapacityCascadeTests(RouterCascadeTests):
    def configure_auto(self, context):
        self.source_row.model_capabilities_override={'context_window':context,'max_output_tokens':4096}
        self.source_row.save(update_fields=['model_capabilities_override'])
        services.update_router(request=self.request(),router_id=str(self.aggregate.pk),data={'strategy':'task_type'})
        self.aggregate.refresh_from_db()
        parent=self.parent(self.aggregate,'auto','delegated')
        return self.client_for(parent)

    def body(self,response):
        if not getattr(response,'streaming',False):return json.dumps(response.data)
        if getattr(response,'is_async',False):
            async def collect():return b''.join([x async for x in response.streaming_content])
            return async_to_sync(collect)().decode()
        return b''.join(response.streaming_content).decode()

    def test_cascaded_capacity_errors_have_details_json_and_sse_without_dispatch(self):
        client=self.configure_auto(1024);self.calls.clear()
        for native in (False,True):
            for stream in (False,True):
                payload={'model':'delegated','stream':stream}
                payload.update({'input':'hello '*3000,'max_output_tokens':128} if native else
                               {'messages':[{'role':'user','content':'hello '*3000}],'max_tokens':128})
                with patch('apps.gateway.models_dev.snapshot',return_value={}):
                    response=client.post('/api/v1/openai/v1/'+('responses' if native else 'chat/completions'),payload,format='json')
                    text=self.body(response)
                self.assertEqual(response.status_code,200 if stream else 400,text)
                self.assertIn('context_window_exceeded',text)
                self.assertIn('excluded_candidates',text)
                self.assertIn('required_tokens',text)
                self.assertNotIn('hello',text)
        self.assertEqual(self.calls,[])

    def test_large_text_reaches_real_upstream_and_trace_records_estimate(self):
        # Personal's separate HTTP body policy remains unchanged. This still
        # exceeds the old byte-based capacity check while fitting in tokens.
        client=self.configure_auto(131072);self.calls.clear()
        payload={'model':'delegated','messages':[{'role':'user','content':'hello '*40000}],'max_tokens':128}
        with patch('apps.gateway.models_dev.snapshot',return_value={}):
            response=client.post('/api/v1/openai/v1/chat/completions',payload,format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.assertTrue(any(x[0]=='POST' for x in self.calls))
        log=GatewayRequestLog.objects.filter(status='success',model='delegated').latest('created_at')
        trace=json.dumps(log.routing_trace)
        self.assertIn('candidate_assessments',trace)
        self.assertIn('o200k_base',trace)
