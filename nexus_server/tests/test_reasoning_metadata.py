"""Reasoning discovery is a typed contract, not a guess from a model name."""
import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from apps.gateway.model_metadata import normalize, merge_fields, aggregate, endpoint_hash
from apps.gateway.model_info import source_fields
from apps.providers.model_info import read_optional_info
from apps.providers.models import ProviderRuntimeAccount
from apps.providers.runtime_services import discover_runtime_model_ids


class ReasoningMetadataTests(SimpleTestCase):
    def fields(self, values):
        return merge_fields([(normalize(values), "upstream", timezone.now().isoformat(), True)])

    def test_standard_contract_and_partial_flags_do_not_invent_levels(self):
        row = normalize({"model_info": {"supports_reasoning": True, "reasoning_effort_levels": ["high", "none", "low"],
            "default_reasoning_effort": "low", "supported_openai_params": ["messages", "reasoning_effort"]}})
        self.assertEqual(row["reasoning_efforts"], ["none", "low", "high"])
        self.assertEqual(row["reasoning_default"], "low")
        self.assertTrue(row["reasoning_supported"])
        self.assertNotIn("reasoning_efforts", normalize({"supports_reasoning": True}))
        flags = normalize({"supports_reasoning": True, "supports_none_reasoning_effort": True,
            "supports_xhigh_reasoning_effort": False, "supports_max_reasoning_effort": True})
        self.assertEqual(flags["reasoning_efforts"], ["none", "max"])
        self.assertNotIn("supports_low_reasoning_effort", flags)
        self.assertNotIn("reasoning_supported", normalize({"id": "gpt-pretend-reasoning-max"}))
        self.assertNotIn("reasoning_efforts", normalize({"reasoning_effort_levels": ["low", "bad\nvalue"]}))
        future = normalize({"reasoning_effort_levels": ["ultra", "none", "low"], "default_reasoning_effort": "ultra",
                            "supports_max_reasoning_effort": True})
        self.assertEqual(future["reasoning_efforts"], ["none", "low", "ultra"])
        self.assertEqual(future["reasoning_default"], "ultra")
        self.assertEqual(aggregate([self.fields(future), self.fields(future)])["reasoning_efforts"]["value"], ["none", "low", "ultra"])

    def test_negative_contract_and_default_are_consistent(self):
        for fields in ({"supports_reasoning": False, "reasoning": {"supported": True}},
                       {"supports_reasoning": True, "supported_openai_params": ["messages"]}):
            row = normalize({**fields, "reasoning_effort_levels": ["low", "high"], "default_reasoning_effort": "high"})
            self.assertEqual(row["reasoning_efforts"], [])
            self.assertNotIn("reasoning_default", row)
        row = normalize({"reasoning_effort_levels": ["none", "low"], "default_reasoning_effort": "high"})
        self.assertNotIn("reasoning_default", row)
        self.assertIn("none", row["reasoning_efforts"])
        self.assertNotIn("off", row["reasoning_efforts"])

    def test_router_intersection_unknown_candidates_and_mismatched_defaults(self):
        first = self.fields({"supports_reasoning": True, "reasoning_effort_levels": ["none", "low", "high"],
            "default_reasoning_effort": "high", "supported_openai_params": ["messages", "reasoning_effort"]})
        second = self.fields({"supports_reasoning": True, "reasoning_effort_levels": ["low", "high", "xhigh"],
            "default_reasoning_effort": "low", "supported_openai_params": ["messages", "reasoning_effort", "temperature"]})
        row = aggregate([first, second])
        self.assertEqual(row["reasoning_efforts"]["value"], ["low", "high"])
        self.assertTrue(row["reasoning_efforts"]["upstream_confirmed"])
        self.assertIsNone(row["reasoning_default"]["value"])
        self.assertEqual(row["supported_openai_params"]["value"], ["messages", "reasoning_effort"])
        row = aggregate([first, self.fields({"supports_reasoning": True})])
        self.assertIsNone(row["reasoning_efforts"]["value"])
        self.assertIn("unknown", row["reasoning_efforts"]["sources"])
        self.assertFalse(row["reasoning_efforts"]["upstream_confirmed"])
        self.assertEqual(aggregate([first, self.fields({"reasoning_effort_levels": ["max"]})])["reasoning_efforts"]["value"], [])
        incompatible = self.fields({"reasoning_effort_levels": ["low", "high"], "supported_openai_params": ["messages", "reasoning"]})
        self.assertEqual(aggregate([first, incompatible])["reasoning_efforts"]["value"], [])

    def test_upstream_negative_beats_catalog_and_endpoint_change_invalidates_it(self):
        now = timezone.now()
        source = SimpleNamespace(provider=SimpleNamespace(name="provider"), upstream_model_id="exact",
            canonical_model=SimpleNamespace(metadata={}, context_window=None, updated_at=now),
            provider_runtime=SimpleNamespace(internal_api_url="https://upstream.test/v1"),
            runtime_model_offer=SimpleNamespace(metadata={"model_capacity": {"values": {"supports_reasoning": False},
                "updated_at": now.isoformat(), "endpoint_hash": endpoint_hash("https://upstream.test/v1")}}))
        catalog = {"updated_at": now.isoformat(), "providers": {"provider": {"exact": {
            "reasoning_supported": True, "reasoning_efforts": ["low", "high"]}}}}
        row = source_fields(source, catalog)
        self.assertEqual(row["reasoning_efforts"]["value"], [])
        self.assertEqual(row["reasoning_efforts"]["source"], "upstream")
        source.provider_runtime.internal_api_url = "https://replacement.test/v1"
        row = source_fields(source, catalog)
        self.assertEqual(row["reasoning_efforts"]["source"], "models.dev")
        self.assertFalse(row["reasoning_efforts"]["upstream_confirmed"])
        source.upstream_model_id = "exact-preview"
        self.assertNotIn("reasoning_supported", source_fields(source, catalog))

    @override_settings(NEXUS_PROVIDER_MODEL_INFO_ENABLED=True)
    def test_complete_capacity_still_fetches_reasoning_and_never_widens_model_list(self):
        runtime = SimpleNamespace(runtime_type=ProviderRuntimeAccount.RUNTIME_DIRECT_API,
            internal_api_url="https://provider.test/v1", source_provider_account=None, encrypted_proxy_api_key="")
        payload = {"data": [{"id": "alias", "context_window": 64000, "max_input_tokens": 32000, "max_output_tokens": 16000,
                             "supports_reasoning": False}]}
        with patch("apps.providers.runtime_services._read_provider_catalog", return_value=payload), \
             patch("apps.providers.model_info.read_optional_info", return_value={"alias": {
                 "reasoning_supported": True, "reasoning_efforts": ["low", "high"], "reasoning_default": "low"},
                 "hidden": {"reasoning_efforts": ["max"]}}) as optional:
            self.assertEqual(discover_runtime_model_ids(runtime=runtime), ["alias"])
        optional.assert_called_once()
        self.assertTrue(runtime._discovered_model_capacities["alias"]["reasoning_supported"])
        self.assertEqual(runtime._discovered_model_capacities["alias"]["reasoning_efforts"], ["low", "high"])
        self.assertNotIn("hidden", runtime._discovered_model_capacities)

    def test_canonical_litellm_path_fallback_and_duplicate_alias_intersection(self):
        calls = []
        def open_request(request, **kwargs):
            calls.append(request.full_url)
            if request.full_url.endswith("/v1/model/info"):
                raise HTTPError(request.full_url, 404, "missing", {}, io.BytesIO())
            return io.BytesIO(json.dumps({"data": [
                {"model_name": "alias", "model_info": {"reasoning_effort_levels": ["none", "low", "high"]}},
                {"model_name": "alias", "model_info": {"reasoning_effort_levels": ["low", "max"]}}]}).encode())
        with patch("apps.providers.model_info.build_opener", return_value=SimpleNamespace(open=open_request)):
            row = read_optional_info("https://provider.test/v1", {})["alias"]
        self.assertEqual(calls, ["https://provider.test/v1/model/info", "https://provider.test/model/info"])
        self.assertEqual(row["reasoning_efforts"], ["low"])
