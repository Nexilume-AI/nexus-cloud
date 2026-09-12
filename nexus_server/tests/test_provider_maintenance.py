from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from apps.providers.tasks import reconcile_provider_runtime_health_task


class ProviderMaintenanceCommandTests(SimpleTestCase):
    @override_settings(
        NEXUS_PROVIDER_HEALTH_INTERVAL_SECONDS=60,
        NEXUS_PROVIDER_MODEL_DISCOVERY_INTERVAL_SECONDS=1800,
    )
    @patch("apps.providers.management.commands.run_provider_maintenance.refresh_active_provider_runtime_models")
    @patch("apps.providers.management.commands.run_provider_maintenance.reconcile_provider_runtime_health_task")
    def test_once_runs_both_maintenance_paths_and_reports_pending_settlement(self, health, catalog):
        health.return_value = {
            "examined": 2,
            "healthy": 1,
            "unavailable": 1,
            "stale_starts_recovered": 1,
            "stale_stops_recovered": 0,
            "settlement_pending": 3,
        }
        catalog.return_value = {"refreshed": 1, "failed": 1}
        output = StringIO()

        call_command("run_provider_maintenance", once=True, stdout=output)

        health.assert_called_once_with()
        catalog.assert_called_once_with()
        self.assertIn("settlement_pending=3", output.getvalue())


class ProviderMaintenanceLockTests(SimpleTestCase):
    @patch("apps.providers.tasks.cache.add", return_value=False)
    def test_overlapping_health_sweep_is_skipped(self, _cache_add):
        self.assertEqual(
            reconcile_provider_runtime_health_task(),
            {"examined": 0, "healthy": 0, "unavailable": 0, "skipped_overlap": 1},
        )
