import json
from types import SimpleNamespace
from django.test import SimpleTestCase, override_settings
from apps.agents.relay_chat import relay_chat_enabled, collect_relay_task_result


class RelayChatPolicyTests(SimpleTestCase):
    @override_settings(NEXUS_AGENT_RELAY_CHAT_AGENTS=("allowed",))
    def test_explicit_agent_rollout_only(self):
        self.assertTrue(relay_chat_enabled(SimpleNamespace(runtime_kind="openwrt_relay",agent_id="allowed")))
        self.assertFalse(relay_chat_enabled(SimpleNamespace(runtime_kind="openwrt_relay",agent_id="other")))
        self.assertFalse(relay_chat_enabled(SimpleNamespace(runtime_kind="docker",agent_id="allowed")))

    def test_heartbeats_and_progress_do_not_replace_final_result(self):
        chunks=[b": ping\n\n"]*1000+[b'event: message\ndata: {"method":"notifications/progress"}\n\n',b'event: message\ndata: {"id":7,"result":{"content":[]}}\n\n']
        self.assertEqual(json.loads(collect_relay_task_result(iter(chunks),7)),{"id":7,"result":{"content":[]}})

    def test_missing_and_duplicate_results_rejected(self):
        packet=b'event: message\ndata: {"id":7,"result":{}}\n\n'
        for chunks in ([packet,packet],[b': ping\n\n'],[packet]):
            with self.assertRaises(ValueError):collect_relay_task_result(iter(chunks),8 if chunks==[packet] else 7)

    def test_error_packet_preserved(self):
        packet=b'event: message\ndata: {"id":7,"error":{"code":-1}}\n\n'
        self.assertEqual(json.loads(collect_relay_task_result(iter([packet]),7))['error']['code'],-1)
