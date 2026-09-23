import json
import socket
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from apps.gateway import provider_http as http


@override_settings(NEXUS_PROVIDER_DOH_HOSTS="api.siliconflow.cn")
class ProviderDnsRecoveryTests(SimpleTestCase):
    def setUp(self):
        http._doh_cache.clear()

    def response(self, address="47.239.184.63", ttl=60):
        response=Mock()
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        response.read.return_value=json.dumps({"Status":0,"Answer":[{"type":1,"TTL":ttl,"data":address}]}).encode()
        return response

    def test_exact_host_scope_and_tls_name_are_preserved(self):
        with patch.object(http,"urlopen",return_value=self.response()) as resolve:
            endpoint=http.resolve_provider_endpoint("https://api.siliconflow.cn/v1/models")
            self.assertEqual(endpoint.hostname,"api.siliconflow.cn")
            self.assertEqual(endpoint.addresses[0][3],("47.239.184.63",443))
            connection=http._PinnedHTTPSConnection(endpoint,3)
            with patch.object(http,"_connect_address",return_value=Mock()), patch.object(connection._context,"wrap_socket") as tls:
                connection.connect()
                self.assertEqual(tls.call_args.kwargs["server_hostname"],"api.siliconflow.cn")
            with patch.object(http.socket,"getaddrinfo",return_value=[]) as ordinary:
                http._provider_address_rows("other.example",443)
                ordinary.assert_called_once()
            resolve.assert_called_once()

    def test_expired_cache_refreshes_instead_of_sticking_to_dead_address(self):
        with patch.object(http,"urlopen",side_effect=[self.response(),self.response("47.239.215.199")]) as resolve:
            with patch.object(http.time,"monotonic",return_value=10):
                first=http._provider_address_rows("api.siliconflow.cn",443)
            with patch.object(http.time,"monotonic",return_value=20):
                self.assertEqual(http._provider_address_rows("api.siliconflow.cn",443),first)
            with patch.object(http.time,"monotonic",return_value=71):
                self.assertNotEqual(http._provider_address_rows("api.siliconflow.cn",443),first)
            self.assertEqual(resolve.call_count,2)

    def test_private_dns_answer_cannot_bypass_endpoint_validation(self):
        private=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("127.0.0.1",443))]
        with patch.object(http,"urlopen",return_value=self.response("127.0.0.1")), patch.object(http.socket,"getaddrinfo",return_value=private):
            with self.assertRaises(http.ProviderEndpointRejected):
                http.resolve_provider_endpoint("https://api.siliconflow.cn/v1/models")
        self.assertFalse(http._doh_cache)

    def test_unavailable_resolver_does_not_cache_failure(self):
        with patch.object(http,"urlopen",side_effect=[TimeoutError(),self.response()]) as resolve, patch.object(http.socket,"getaddrinfo",return_value=[]) as ordinary:
            self.assertEqual(http._provider_address_rows("api.siliconflow.cn",443),[])
            self.assertTrue(http._provider_address_rows("api.siliconflow.cn",443))
            self.assertEqual(resolve.call_count,2)
            ordinary.assert_called_once()
