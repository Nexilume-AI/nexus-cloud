import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml
from django.test import SimpleTestCase

from apps.providers.runtime_runner import CLIProxyAPIRuntimeAdapter, CodexProxyRuntimeAdapter


class ProviderRuntimeConfigTests(SimpleTestCase):
    def test_recreation_preserves_custom_settings_and_exact_backup(self):
        for adapter, relative in [(CLIProxyAPIRuntimeAdapter(), "config.yaml"),
                                  (CodexProxyRuntimeAdapter(), "data/local.yaml")]:
            with self.subTest(adapter=type(adapter).__name__), tempfile.TemporaryDirectory() as root:
                target = Path(root) / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                original = b'# custom proxy\nproxy-url: "http://proxy.test:9000"\nserver:\n  custom: true\nopenai-compatibility:\n- name: test\n  api-key-entries:\n  - api-key: fixture-only\n'
                target.write_bytes(original)
                with patch("apps.providers.runtime_runner.runtime_storage_root", return_value=Path(root)):
                    adapter.prepare_storage(runtime=None, proxy_api_key="fixture-managed-key")
                    first = target.read_bytes()
                    adapter.prepare_storage(runtime=None, proxy_api_key="fixture-managed-key")
                saved = yaml.safe_load(target.read_text())
                self.assertEqual(saved["proxy-url"], "http://proxy.test:9000")
                self.assertTrue(saved["server"]["custom"])
                self.assertEqual(saved["openai-compatibility"][0]["name"], "test")
                self.assertEqual(target.read_bytes(), first)
                backups = list(target.parent.glob(target.name + ".backup-*"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), original)

    def test_invalid_existing_config_fails_without_overwriting(self):
        for original in [b'broken: [', b'- not-a-mapping', b'port: 1\nport: 2\n']:
            with self.subTest(original=original), tempfile.TemporaryDirectory() as root:
                target = Path(root) / "config.yaml"
                target.write_bytes(original)
                with patch("apps.providers.runtime_runner.runtime_storage_root", return_value=Path(root)):
                    with self.assertRaises(Exception):
                        CLIProxyAPIRuntimeAdapter().prepare_storage(runtime=None, proxy_api_key="fixture-key")
                self.assertEqual(target.read_bytes(), original)

    def test_write_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "config.yaml"
            original = b'custom: preserved\n'
            target.write_bytes(original)
            with patch("apps.providers.runtime_runner.runtime_storage_root", return_value=Path(root)), \
                 patch("os.replace", side_effect=OSError("fixture write failure")):
                with self.assertRaises(Exception):
                    CLIProxyAPIRuntimeAdapter().prepare_storage(runtime=None, proxy_api_key="fixture-key")
            self.assertEqual(target.read_bytes(), original)
