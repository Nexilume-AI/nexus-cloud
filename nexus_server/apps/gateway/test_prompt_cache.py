"""Protocol and disjoint tariff contracts, runnable in both distributions."""
from decimal import Decimal
from time import monotonic
from types import SimpleNamespace

from django.test import SimpleTestCase

from .prompt_cache import cache_usage, token_charge, configured_tariff, pricing_snapshot
from .provider_adapters import build_response
from .serializers import ChatCompletionRequestSerializer, ResponseCreateRequestSerializer
from .responses_native import gateway_payload, upstream_payload
from .services import build_stream_provider_response
from .openai_compat import response_usage


DETAILS = {"cached_tokens": 4000, "cache_write_tokens": 7000,
           "cache_write_tokens_details": {"ttl_30m_tokens": 3000, "ttl_12h_tokens": 4000}}


class PromptCacheTests(SimpleTestCase):
    def test_chat_and_responses_options_and_legacy_retention(self):
        for cls, base in ((ChatCompletionRequestSerializer, {"messages": [{"role": "user", "content": "hello"}]}),
                          (ResponseCreateRequestSerializer, {"input": "hello"})):
            for options in ({"prompt_cache_retention": "24h"},
                            {"prompt_cache_options": {"mode": "explicit", "ttl": "12h", "prewarm": True}}):
                with self.subTest(serializer=cls.__name__, options=options):
                    s = cls(data={"model": "model", "prompt_cache_key": " stable-prefix ", **base, **options})
                    self.assertTrue(s.is_valid(), s.errors)
                    self.assertEqual(s.validated_data["prompt_cache_key"], " stable-prefix ")
                    for key, value in options.items():
                        self.assertEqual(s.validated_data[key], value)
            for bad in ({"prompt_cache_options": {"ttl": "forever"}},
                        {"prompt_cache_options": {"mode": "unknown"}},
                        {"prompt_cache_options": {"private": True}},
                        {"prompt_cache_options": {}, "prompt_cache_retention": "24h"}):
                s = cls(data={"model": "model", **base, **bad})
                self.assertFalse(s.is_valid(), bad)
            s = cls(data={"model": "model", **base, "prompt_cache_key": None,
                          "prompt_cache_options": None, "prompt_cache_retention": None})
            self.assertTrue(s.is_valid(), s.errors)

    def test_breakpoints_survive_text_and_image_validation_and_native_wire(self):
        breakpoint = {"mode": "explicit", "ttl": "12h"}
        parts = [{"type": "text", "text": "prefix", "prompt_cache_breakpoint": breakpoint},
                 {"type": "image_url", "image_url": {"url": "https://example.com/image.png"},
                  "prompt_cache_breakpoint": breakpoint}]
        s = ChatCompletionRequestSerializer(data={"model": "alias", "messages": [{"role": "user", "content": parts}]})
        self.assertTrue(s.is_valid(), s.errors)
        self.assertEqual(s.validated_data["messages"][0]["content"], parts)
        native = {"model": "alias", "max_output_tokens": 10, "prompt_cache_key": "k",
                  "prompt_cache_options": {"mode": "explicit", "ttl": "12h"},
                  "input": [{"type": "message", "role": "user", "content": parts}]}
        s = ResponseCreateRequestSerializer(data=native)
        self.assertTrue(s.is_valid(), s.errors)
        wire = upstream_payload(gateway_payload(s.validated_data), "physical-model")
        self.assertEqual(wire["model"], "physical-model")
        self.assertEqual(wire["prompt_cache_options"], native["prompt_cache_options"])
        self.assertEqual([p["prompt_cache_breakpoint"] for p in wire["input"][0]["content"]], [breakpoint]*2)

    def test_json_sse_and_compat_usage_match_without_fabricating_hits(self):
        for details_key in ("prompt_tokens_details", "input_tokens_details"):
            usage = {"prompt_tokens": 12000, "completion_tokens": 20, details_key: DETAILS}
            parsed = build_response(raw={"usage": usage}, model="m", started=monotonic())
            streamed = build_stream_provider_response(deployment=SimpleNamespace(model="m"), requested_model="m",
                text="ok", usage=usage, finish_reason="stop", latency_ms=1, payload={"messages": []})
            self.assertEqual(parsed.cache_usage, streamed.cache_usage)
            self.assertEqual(parsed.cache_usage["uncached_input_tokens"], 1000)
            self.assertEqual(parsed.cache_usage["cache_write_tokens"], 7000)
            self.assertFalse(parsed.cache_usage["adjusted"])
            self.assertEqual(response_usage(usage)["input_tokens_details"], DETAILS)
        missing = cache_usage({}, 12000)
        self.assertFalse(missing["reported"])
        self.assertEqual(missing["cached_tokens"], 0)
        self.assertEqual(missing["uncached_input_tokens"], 12000)

    def test_malformed_details_cannot_overbill_or_create_negative_counts(self):
        for details in ({"cached_tokens": 30000, "cache_write_tokens": 30000},
                        {"cached_tokens": -5, "cache_write_tokens": "bad"},
                        {"cached_tokens": True, "cache_write_tokens": float("inf")},
                        {"cache_write_tokens": 2, "cache_write_tokens_details": {"ttl_30m_tokens": 9}}):
            usage = {"input_tokens": 10, "input_tokens_details": details}
            result = build_response(raw={"usage": usage}, model="m", started=monotonic())
            data = result.cache_usage
            self.assertTrue(data["adjusted"])
            self.assertEqual(data["cached_tokens"] + data["cache_write_tokens"] + data["uncached_input_tokens"], 10)
            self.assertLessEqual(sum(data["cache_write_tokens_details"].values()), data["cache_write_tokens"])

    def test_disjoint_mixed_ttl_billing_and_flat_fallback(self):
        tariff = SimpleNamespace(price_per_1k_tokens=Decimal("1"), output_price_per_1k_tokens=Decimal("2"),
            cached_input_price_per_1k_tokens=Decimal("0.1"), cache_write_price_per_1k_tokens=Decimal("1.1"),
            cache_write_30m_price_per_1k_tokens=Decimal("1.25"), cache_write_12h_price_per_1k_tokens=Decimal("2.5"))
        raw, _ = token_charge(tariff=tariff, request_tokens=12000, response_tokens=20, cache=DETAILS)
        # 1 ordinary + .4 reads + 3.75 short writes + 10 long writes + .04 output.
        self.assertEqual(raw, Decimal("15.19"))
        raw, _ = token_charge(tariff=SimpleNamespace(price_per_1k_tokens=Decimal("1")),
                              request_tokens=12000, response_tokens=20, cache=DETAILS)
        self.assertEqual(raw, Decimal("12.02"))

    def test_source_partial_override_preserves_existing_flat_rate(self):
        source = SimpleNamespace(pricing_rate=Decimal("3"), runtime_model_offer=SimpleNamespace(
            cached_input_price_per_1k_tokens=Decimal("0.2")))
        prices = pricing_snapshot(configured_tariff(source))
        self.assertEqual(prices["input_price_per_1k_tokens"], Decimal("3"))
        self.assertEqual(prices["cache_write_12h_price_per_1k_tokens"], Decimal("3"))
        self.assertEqual(prices["cached_input_price_per_1k_tokens"], Decimal("0.2"))
