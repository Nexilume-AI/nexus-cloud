import io
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from django.test import SimpleTestCase
from apps.gateway import system_one
from apps.gateway.provider_adapters import adapter_for_provider, ProviderClientError, SystemOneAdapter


class SystemOneProtocolTests(SimpleTestCase):
    def test_native_and_maas_catalogs_do_not_claim_chat(self):
        for payload in ({"models": [{"name": "jev", "capabilities": {"questions": ["choice"]}}]}, {"data": [{"id": "jev"}]}):
            rows = system_one.catalog_rows(payload)
            self.assertEqual(rows[0]["id"], "jev")
            self.assertEqual(rows[0]["model_contract"]["operations"], ["systemone"])
        for payload in ({"models": []}, {"models": [{"name": ""}]}, {"data": "wrong"}):
            with self.assertRaises(ValueError):
                system_one.catalog_rows(payload)

    def test_adapter_never_emulates_chat_or_streaming(self):
        adapter = adapter_for_provider("system_one")
        self.assertIsInstance(adapter, SystemOneAdapter)
        for method in (adapter.chat_completions, adapter.stream_chat_completions):
            with self.assertRaises(ProviderClientError) as caught:
                method()
            self.assertFalse(caught.exception.affects_health)

    def test_untrusted_probability_numbers_are_bounded_before_float_conversion(self):
        for value in (10 ** 1000, -1, float("nan"), float("inf"), True, "0.8"):
            with self.assertRaises(ValueError):
                system_one.choice_answer({"answers": {"route": {"type": "choice", "choice": "a", "probabilities": {"a": value}}}}, question="route", choices={"a": "first", "b": "second"})

    def call(self):
        return system_one.decide(base_url="https://native.test/v1", key="private", model="jev", state={"task": "fixture"}, choices={"a": "fast", "b": "strong"}, operation_id="operation-fixture")

    def test_native_wire_key_and_one_request(self):
        @contextmanager
        def opened(url, **options):
            self.assertEqual(url, "https://native.test/v1/systemone")
            self.assertEqual(options["headers"]["Idempotency-Key"], "operation-fixture")
            self.assertEqual(options["headers"]["Authorization"], "Bearer private")
            body = json.loads(options["body"])
            self.assertEqual(set(body), {"model", "state", "questions"})
            self.assertEqual(body["questions"]["route"]["type"], "choice")
            yield io.BytesIO(b'{"answers":{"route":{"type":"choice","choice":"b","probabilities":{"a":0.1,"b":0.9}}}}')
        with patch.object(system_one, "open_provider_url", side_effect=opened) as network:
            self.assertEqual(self.call(), "b")
            self.assertEqual(network.call_count, 1)

    def test_unknown_results_and_errors_never_replay_or_echo_raw_response(self):
        for raw in (b'not-json-private', b'{"answers":{"route":{"type":"choice","choice":"outside"}}}', b'{"answers":{"route":{"type":"choice","choice":"a","probabilities":{"a":2}}}}', b'x' * 65537):
            with patch.object(system_one, "open_provider_url") as network:
                network.return_value.__enter__.return_value = io.BytesIO(raw)
                with self.assertRaises(ProviderClientError) as caught:
                    self.call()
                self.assertNotIn("private", str(caught.exception))
                self.assertEqual(network.call_count, 1)
        for code in (401, 403, 429, 503):
            with patch.object(system_one, "open_provider_url", side_effect=HTTPError("https://native.test", code, "private", {}, io.BytesIO(b'private'))) as network:
                with self.assertRaises(ProviderClientError) as caught:
                    self.call()
                self.assertEqual(caught.exception.status_code, code)
                self.assertFalse(caught.exception.affects_health)
                self.assertEqual(network.call_count, 1)
