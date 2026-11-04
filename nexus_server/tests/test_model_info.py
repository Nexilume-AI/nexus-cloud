import io
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.gateway.model_metadata import normalize, merge_fields, aggregate, endpoint_hash
from apps.gateway import models_dev
from apps.gateway.model_info import source_fields
from apps.providers.models import ProviderRuntimeAccount
from apps.providers.runtime_services import discover_runtime_model_ids


class ModelMetadataTests(SimpleTestCase):
    def test_upstream_litellm_duplicate_aliases_use_conservative_limits(self):
        from apps.providers.model_info import read_optional_info
        rows = {"data": [{"model_name": "shared", "model_info": {"max_input_tokens": 32000}},
                         {"model_name": "shared", "model_info": {"max_input_tokens": 128000, "max_output_tokens": 16000}}]}
        opener = SimpleNamespace(open=lambda *a, **kw: io.BytesIO(json.dumps(rows).encode()))
        with patch("apps.providers.model_info.build_opener", return_value=opener):
            values = read_optional_info("https://upstream.test/v1", {})["shared"]
        self.assertEqual(values["max_input_tokens"], 32000)
        self.assertIsNone(values["max_output_tokens"])

    def test_litellm_capacity_keeps_each_routes_independent_input_ceiling(self):
        from apps.gateway.model_info import compatible_input_capacity
        def fields(**values):
            return merge_fields([(values, "upstream", timezone.now().isoformat(), True)])
        self.assertEqual(compatible_input_capacity([
            fields(context_window=64000, max_input_tokens=32000), fields(context_window=64000)]), 32000)
        self.assertIsNone(compatible_input_capacity([fields(context_window=64000), {}]))
        self.assertEqual(normalize({"model_info": {"supports_reasoning": True}})["reasoning_supported"], True)

    def test_optional_info_does_not_follow_redirects_or_change_health(self):
        from apps.providers.model_info import read_optional_info, retain_capacity
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                calls.append(self.path)
                self.send_response(302); self.send_header("Location", "/credential-leak")
                self.send_header("Content-Length", "0"); self.end_headers()
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            self.assertEqual(read_optional_info(f"http://127.0.0.1:{server.server_port}/v1", {"Authorization": "Bearer fixture"}), {})
            self.assertEqual(calls, ["/v1/model/info"])
        finally:
            server.shutdown(); server.server_close(); thread.join()
        offer = SimpleNamespace(upstream_model_id="model", health_status="healthy", metadata={"health_probe": {"state": "verified"}})
        runtime = SimpleNamespace(internal_api_url="https://provider.test/v1", _discovered_model_capacities={"model": {"context_window": 128000}})
        retain_capacity(offer, runtime)
        self.assertEqual(offer.health_status, "healthy")
        self.assertEqual(offer.metadata["health_probe"], {"state": "verified"})
        self.assertEqual(offer.metadata["model_capacity"]["values"]["context_window"], 128000)

    def test_typed_limits_do_not_conflate_input_context_or_generic_max_tokens(self):
        self.assertIsNone(normalize({"max_tokens": 8192})["context_window"])
        self.assertIsNone(normalize({"max_tokens": 8192})["max_output_tokens"])
        row = normalize({"context_window": True, "context_length": "8192", "max_output_tokens": -1})
        self.assertEqual(row, dict.fromkeys(("context_window", "max_input_tokens", "max_output_tokens")))
        row = normalize({"type": "model", "max_input_tokens": 100000, "max_tokens": 16000})
        self.assertEqual(row["max_input_tokens"], 100000)
        self.assertEqual(row["max_output_tokens"], 16000)
        self.assertIsNone(row["context_window"])

    def test_fallback_merge_is_per_field_and_route_requires_every_candidate(self):
        now = timezone.now().isoformat()
        first = merge_fields([(normalize({"context_window": 64000}), "upstream", now, True),
                              (normalize({"limit": {"context": 128000, "output": 16000}}), "models.dev", now, False)])
        second = merge_fields([(normalize({"context_window": 32000, "max_output_tokens": 8000}), "upstream", now, True)])
        row = aggregate([first, second])
        self.assertEqual(row["context_window"]["value"], 32000)
        self.assertTrue(row["context_window"]["upstream_confirmed"])
        self.assertEqual(row["max_output_tokens"]["value"], 8000)
        self.assertFalse(row["max_output_tokens"]["upstream_confirmed"])
        self.assertIsNone(aggregate([first, {}])["context_window"]["value"])

    def test_source_matches_provider_and_real_model_not_alias_and_invalidates_endpoint_cache(self):
        now = timezone.now()
        source = SimpleNamespace(provider=SimpleNamespace(name="minimax"), upstream_model_id="MiniMax-exact",
            canonical_model=SimpleNamespace(metadata={}, context_window=None, updated_at=now),
            provider_runtime=SimpleNamespace(internal_api_url="https://upstream.test/v1"),
            runtime_model_offer=SimpleNamespace(metadata={"model_capacity": {"values": {"context_window": 64000},
                "updated_at": now.isoformat(), "endpoint_hash": endpoint_hash("https://upstream.test/v1")}}))
        catalog = {"updated_at": now.isoformat(), "providers": {"minimax": {
            "MiniMax-exact": {"context_window": 128000, "max_output_tokens": 16000},
            "alias": {"context_window": 999999}}}}
        row = source_fields(source, catalog)
        self.assertEqual(row["context_window"]["value"], 64000)
        self.assertEqual(row["max_output_tokens"]["source"], "models.dev")
        source.provider_runtime.internal_api_url = "https://replacement.test/v1"
        self.assertEqual(source_fields(source, catalog)["context_window"]["value"], 128000)
        source.upstream_model_id = "MiniMax-exact-preview"
        self.assertEqual(source_fields(source, catalog), {})

    def test_atomic_catalog_validation_keeps_last_good_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(NEXUS_MODELS_DEV_CACHE_PATH=str(Path(directory) / "cache.json")):
            def response(value):
                return SimpleNamespace(open=lambda *a, **kw: io.BytesIO(json.dumps(value).encode()))
            raw = {"minimax": {"models": {"real-model": {"limit": {"context": 100000, "output": 32000}, "api_key": "must-not-cache"}}}}
            with patch.object(models_dev, "build_opener", return_value=response(raw)):
                self.assertEqual(models_dev.refresh()["models"], 1)
            before = models_dev.cache_path().read_bytes()
            self.assertNotIn(b"must-not-cache", before)
            for invalid in ({}, {"minimax": {"models": {}}}, []):
                with patch.object(models_dev, "build_opener", return_value=response(invalid)), self.assertRaises(ValueError):
                    models_dev.refresh()
                self.assertEqual(models_dev.cache_path().read_bytes(), before)
            self.assertEqual(models_dev.lookup(models_dev.snapshot(), "other", "real-model"), {})

    @override_settings(NEXUS_PROVIDER_MODEL_INFO_ENABLED=True)
    def test_provider_refresh_merges_optional_info_without_altering_advertised_ids(self):
        runtime = SimpleNamespace(runtime_type=ProviderRuntimeAccount.RUNTIME_DIRECT_API,
            internal_api_url="https://example.test/v1", source_provider_account=None, encrypted_proxy_api_key="")
        with patch("apps.providers.runtime_services._read_provider_catalog", return_value={"data": [
            {"id": "alias", "context_window": 64000}]}), patch("apps.providers.model_info.read_optional_info", return_value={
                "alias": {"context_window": 999999, "max_output_tokens": 16000}, "unadvertised": {"context_window": 99999}}):
            self.assertEqual(discover_runtime_model_ids(runtime=runtime), ["alias"])
        self.assertEqual(runtime._discovered_model_capacities["alias"]["context_window"], 64000)
        self.assertEqual(runtime._discovered_model_capacities["alias"]["max_output_tokens"], 16000)
        self.assertNotIn("unadvertised", runtime._discovered_model_capacities)
