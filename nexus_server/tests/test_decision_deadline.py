import socket
import threading
import time
from io import BytesIO
from unittest.mock import patch
from django.test import SimpleTestCase
from apps.gateway.system_one import decide
from apps.gateway.provider_adapters import ProviderClientError
from apps.gateway.request_deadline import RequestDeadline
from apps.routers.inputs import DecisionConfigSerializer


class DecisionDeadlineTests(SimpleTestCase):
    def invoke(self, timeout):
        return decide(base_url='https://fixture.invalid', key='test', model='decision',
                      state={'task': 'test'}, choices={'a': 'fast', 'b': 'strong'}, timeout=timeout)

    def test_schema_default_and_bounds(self):
        base = {'provider_connection_id': '00000000-0000-0000-0000-000000000001', 'model': 'decision'}
        value = DecisionConfigSerializer(data=base)
        self.assertTrue(value.is_valid(), value.errors)
        self.assertEqual(value.validated_data['timeout_ms'], 20000)
        for ms, valid in [(999, False), (1000, True), (20000, True), (60000, True), (60001, False)]:
            value = DecisionConfigSerializer(data={**base, 'timeout_ms': ms})
            self.assertEqual(value.is_valid(), valid)

    def test_late_dns_never_sends_and_returns_within_deadline(self):
        release, finished = threading.Event(), threading.Event()
        def resolve(*args, **kwargs):
            release.wait(1)
            finished.set()
            return None
        with patch('apps.routers.decision_budget.fit', return_value=(b'{}', {})), patch('apps.gateway.provider_http.resolve_provider_endpoint', side_effect=resolve), patch('apps.gateway.provider_http._PinnedHTTPSConnection') as connect:
            start = time.monotonic()
            try:
                with self.assertRaises(ProviderClientError) as caught:
                    self.invoke(.08)
                self.assertEqual(caught.exception.error_code, 'SYSTEM_ONE_DEADLINE_EXCEEDED')
                self.assertFalse(caught.exception.safe_to_retry)
                self.assertLess(time.monotonic() - start, .4)
            finally:
                release.set()
                self.assertTrue(finished.wait(1))
            time.sleep(.03)
            connect.assert_not_called()

    def test_slow_drip_read_is_interrupted_not_extended(self):
        reader, writer = socket.socketpair()
        stop = threading.Event()
        def drip():
            try:
                while not stop.wait(.01):
                    writer.sendall(b'x')
            except OSError:
                pass
        sender = threading.Thread(target=drip, daemon=True); sender.start()
        deadline = RequestDeadline(.1)
        def read():
            deadline.watch(reader)
            while reader.recv(1):
                pass
        start = time.monotonic()
        try:
            with self.assertRaises(TimeoutError):
                deadline.run(read)
            self.assertLess(time.monotonic() - start, .4)
        finally:
            stop.set(); writer.close(); reader.close(); sender.join(1)

    def test_success_dispatches_once_with_remaining_deadline(self):
        response = BytesIO(b'{"answers":{"route":{"type":"choice","choice":"a"}}}')
        with patch('apps.gateway.system_one.open_provider_url', return_value=response) as send:
            self.assertEqual(self.invoke(20), 'a')
        self.assertEqual(send.call_count, 1)
        self.assertLessEqual(send.call_args.kwargs['timeout'], 20)
        self.assertIn('total_deadline', send.call_args.kwargs)

    def test_timeout_uses_existing_objective_fallback_once(self):
        from types import SimpleNamespace as NS
        from unittest.mock import Mock
        from apps.routers.decision_routing import select_pools
        router = NS(decision_config={'model':'custom', 'timeout_ms':60000, 'routing_preference':'performance'}, model_group_bindings=Mock())
        router.model_group_bindings.filter.return_value.order_by.return_value = [NS(model_group_id='a'), NS(model_group_id='b')]
        pools = [{'id':n, 'group':NS(display_name=n,name=n), 'binding':NS(routing_hint='',priority=i), 'deployments':[]} for i,n in enumerate(['a','b'])]
        failure = ProviderClientError('deadline', error_code='SYSTEM_ONE_DEADLINE_EXCEEDED', affects_health=False, safe_to_retry=False)
        with patch('apps.routers.decision_routing.resolve_provider',return_value=(NS(encrypted_key='test'), NS(internal_api_url='https://fixture.invalid'))), patch('apps.common.crypto.decrypt_secret',return_value='test'), patch('apps.gateway.models_dev.snapshot',return_value={}), patch('apps.routers.decision_budget.input_limit',return_value=65536), patch('apps.routers.decision_routing.decide',side_effect=failure) as send:
            ordered, trace = select_pools(router=router,pools=pools,payload={'messages':[{'role':'user','content':'Prove correctness'}]})
        self.assertEqual(send.call_count,1)
        self.assertEqual(send.call_args.kwargs['timeout'],60)
        self.assertEqual(trace['status'],'fallback')
        self.assertEqual(len(ordered),2)
