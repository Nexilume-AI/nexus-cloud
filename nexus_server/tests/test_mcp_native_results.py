import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.agents.runtime_runner import OpenWrtRelayRuntimeRunner, _relay_tool_result


class MCPNativeResultsTests(SimpleTestCase):
    def test_relay_unicode_arguments_use_ascii_json_without_data_loss(self):
        runner = OpenWrtRelayRuntimeRunner()
        registration = SimpleNamespace(mcp_tools=[{'name':'upload','intent':'test.upload'}],
            origin='agent://test/a', relay_id='r', relay_router_id='router')
        content = '# 中文数据 😀\n' + ('数' * 1024)
        payload = {'jsonrpc':'2.0','id':1,'method':'tools/call',
                   'params':{'name':'upload','arguments':{'content':content}}}
        def invoke(**kwargs):
            body = kwargs['body']
            self.assertTrue(body.isascii())
            decoded = json.loads(body)
            self.assertEqual(decoded['payload']['request'], payload)
            self.assertEqual(decoded['target_agent'], registration.origin)
            return 200, {}, b'{"uploaded":true}'
        with patch.object(runner, '_registration', return_value=registration), \
             patch('apps.agents.relay_services.relay_invoke', side_effect=invoke):
            result = runner.call_mcp(deployment=SimpleNamespace(tenant_id='tenant'),
                method='POST', headers={}, body=json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        self.assertEqual(result.status_code, 200)

    def test_same_rpc_id_cannot_replay_another_invocations_result(self):
        runner = OpenWrtRelayRuntimeRunner()
        registration = SimpleNamespace(mcp_tools=[{'name':'authorize','intent':'credential.authorize'}], origin='agent://test/a', relay_id='r',relay_router_id='router')
        task_ids=[]
        def invoke(**kwargs):
            task_ids.append(kwargs['task_id'])
            return 200, {}, b'{"status":"pending"}'
        with patch.object(runner,'_registration',return_value=registration), patch('apps.agents.relay_services.relay_invoke',side_effect=invoke):
            for _ in range(2):
                result=runner.call_mcp(deployment=SimpleNamespace(tenant_id='tenant'),method='POST',headers={},
                    body=b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"authorize","arguments":{}}}')
                self.assertEqual(json.loads(result.body)['id'],1)
        self.assertNotEqual(task_ids[0],task_ids[1])

    def test_relay_keeps_native_blocks_error_and_structured_content(self):
        native = {"content": [
            {"type": "text", "text": "工具"},
            {"type": "image", "data": "YWJj", "mimeType": "image/png"},
            {"type": "resource", "resource": {"uri": "test://asset", "text": "body"}},
        ], "isError": True, "structuredContent": {"marker": "native"}}
        body = json.dumps({"nexus_mcp_result_version": 1, "result": native}).encode()
        self.assertEqual(_relay_tool_result(body, {"Content-Type": "application/json"}), native)

    def test_plain_business_json_is_unchanged(self):
        body = {"content": [], "isError": True}
        result = _relay_tool_result(json.dumps(body).encode(), {})
        self.assertEqual(result["structuredContent"], body)
        self.assertFalse(result["isError"])

    def test_invalid_native_envelope_is_not_a_success(self):
        for native in ({"content": [], "isError": "false"}, {"content": [], "id": "forged"},
                       {"content": [{"type": "text", "text": 42}]}, {"content": [{"type": "unknown"}]},
                       {"content": [{"type": ["invalid"]}]}):
            with self.assertRaises(ValueError):
                _relay_tool_result(json.dumps({"nexus_mcp_result_version": 1, "result": native}).encode(), {})

    def test_relay_ping_is_local_and_keeps_request_id(self):
        runner = OpenWrtRelayRuntimeRunner()
        with patch.object(runner, "_registration", return_value=SimpleNamespace(mcp_tools=[])):
            result = runner.call_mcp(deployment=SimpleNamespace(), method="POST", headers={},
                                    body=b'{"jsonrpc":"2.0","id":"alive","method":"ping"}')
        self.assertEqual(json.loads(result.body), {"jsonrpc": "2.0", "id": "alive", "result": {}})
