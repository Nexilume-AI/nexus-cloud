from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import unittest

from apps.common.distribution import (
    DistributionConfigurationError, DistributionExtension, Insertion, compose_settings,
)


class DistributionCompositionTests(unittest.TestCase):
    def test_cold_shared_import_has_no_cloud_bootstrap_or_environment_side_effect(self):
        probe = '''
import importlib.abc, os, sys
class BlockBootstrap(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('config', 'celery', 'nexus_enterprise', 'django'):
            raise ModuleNotFoundError('Unexpected bootstrap: ' + fullname)
sys.meta_path.insert(0, BlockBootstrap())
before = dict(os.environ)
from apps.common.distribution import compose_settings, DistributionExtension
result = compose_settings({'INSTALLED_APPS': [], 'NEXUS_API_ROUTES': (), 'CELERY_BEAT_SCHEDULE': {}}, DistributionExtension('personal'))
assert result['NEXUS_DISTRIBUTION'] == 'personal'
assert dict(os.environ) == before
'''
        result = subprocess.run([sys.executable, '-c', probe],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def setUp(self):
        self.base = {
            "INSTALLED_APPS": ["core.identity", "core.execution"],
            "NEXUS_API_ROUTES": (("account/", "core.identity.urls"), ("api/v1/", "core.execution.urls")),
            "CELERY_BEAT_SCHEDULE": {"core-health": {"task": "core.health", "schedule": 15}},
            "NEXUS_SECURITY_REQUIRED": True,
        }

    def test_empty_extension_preserves_core_and_does_not_invent_backend(self):
        result = compose_settings(self.base, DistributionExtension("test"))
        self.assertEqual(result["INSTALLED_APPS"], self.base["INSTALLED_APPS"])
        self.assertEqual(result["NEXUS_API_ROUTES"], self.base["NEXUS_API_ROUTES"])
        self.assertNotIn("NEXUS_AUTHORIZATION_BACKEND", result)

    def test_ordered_insertions_preserve_original_prefixes(self):
        result = compose_settings(self.base, DistributionExtension("test",
            applications=(Insertion("core.execution", ("extension.policy",)),),
            api_routes=(Insertion("core.execution.urls", ("extension.policy.urls",)),)))
        self.assertEqual(result["INSTALLED_APPS"], ["core.identity", "extension.policy", "core.execution"])
        self.assertEqual(result["NEXUS_API_ROUTES"], (
            ("account/", "core.identity.urls"), ("api/v1/", "extension.policy.urls"),
            ("api/v1/", "core.execution.urls")))

    def test_missing_anchor_rejects_instead_of_appending(self):
        for field in ("applications", "api_routes"):
            with self.subTest(field=field), self.assertRaises(DistributionConfigurationError):
                compose_settings(self.base, DistributionExtension("test", **{field: (Insertion("missing", ("extra",)),)}))

    def test_duplicate_registrations_rejected(self):
        for items in (("core.identity",), ("extra", "extra"), ()):
            with self.subTest(items=items), self.assertRaises(DistributionConfigurationError):
                compose_settings(self.base, DistributionExtension("test",
                    applications=(Insertion("core.execution", items),)))

    def test_duplicate_route_modules_rejected(self):
        with self.assertRaises(DistributionConfigurationError):
            compose_settings(self.base, DistributionExtension("test",
                api_routes=(Insertion("core.execution.urls", ("core.identity.urls",)),)))

    def test_reserved_and_existing_settings_cannot_be_overridden(self):
        for key in ("NEXUS_SECURITY_REQUIRED", "NEXUS_API_ROUTES", "NEXUS_DISTRIBUTION", "DEBUG", "INSTALLED_APPS"):
            with self.subTest(key=key), self.assertRaises(DistributionConfigurationError):
                compose_settings(self.base, DistributionExtension("test", settings={key: False}))

    def test_task_collision_is_atomic(self):
        before = deepcopy(self.base)
        with self.assertRaises(DistributionConfigurationError):
            compose_settings(self.base, DistributionExtension("test",
                applications=(Insertion("core.execution", ("extra",)),),
                periodic_tasks={"core-health": {"task": "other", "schedule": 1}}))
        self.assertEqual(before, self.base)

    def test_schedule_and_extension_values_are_not_shared_mutably(self):
        extra = {"extra": {"task": "extra.health", "schedule": 20, "options": {"queue": "checks"}}}
        result = compose_settings(self.base, DistributionExtension("test", periodic_tasks=extra))
        result["CELERY_BEAT_SCHEDULE"]["extra"]["options"]["queue"] = "changed"
        result["CELERY_BEAT_SCHEDULE"]["core-health"]["schedule"] = 99
        self.assertEqual(extra["extra"]["options"]["queue"], "checks")
        self.assertEqual(self.base["CELERY_BEAT_SCHEDULE"]["core-health"]["schedule"], 15)

    def test_incomplete_task_rejected(self):
        with self.assertRaises(DistributionConfigurationError):
            compose_settings(self.base, DistributionExtension("test", periodic_tasks={"broken": {"task": "extra.health"}}))


if __name__ == "__main__":
    unittest.main()
