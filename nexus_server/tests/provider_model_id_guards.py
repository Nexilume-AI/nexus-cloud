"""Model discovery validation shared by Personal and Enterprise test hosts."""
from unittest.mock import patch


class ProviderModelIDGuards:
    def test_discovery_rejects_invalid_upstream_model_ids_without_partial_catalog(self) -> None:
        from apps.providers.runtime_services import ProviderRuntimeError, discover_runtime_model_ids

        response = type("Response", (), {
            "read": lambda self, size=-1: ("{\"data\":[{\"id\":\"" + ("x" * 256) + "\"}]}").encode("utf-8")[:None if size < 0 else size],
            "__enter__": lambda self: self,
            "__exit__": lambda self, *args: False,
        })()
        with patch("apps.providers.runtime_services.urlopen", return_value=response):
            with self.assertRaises(ProviderRuntimeError):
                discover_runtime_model_ids(runtime=self.runtime)
        self.assertFalse(self.runtime.model_offers.exists())
