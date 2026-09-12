"""Same two-worker PostgreSQL recovery race; each host owns its fixture.

The original mocked process controller is intentional. Database concurrency is
real; this does not assert live Docker restart behavior.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from django.db import close_old_connections
from apps.providers.models import ProviderRuntimeAccount
from apps.providers.runtime_services import reconcile_provider_runtime_health
from apps.providers.runtime_runner import ProviderRuntimeHealthResult, ProviderRuntimeStartResult


class ProviderRecoveryConcurrencyGuards:
    @patch("apps.providers.runtime_services.get_provider_runtime_runner")
    def test_two_workers_recover_a_runtime_only_once(self, get_runner):
        runtime = self.recovery_runtime()
        barrier = Barrier(2)

        def probe(*, runtime):
            if runtime.container_id == "old-container":
                barrier.wait(timeout=20)
                return ProviderRuntimeHealthResult(False, False, "container is stopped", True)
            return ProviderRuntimeHealthResult(True, False, "ready")

        get_runner.return_value.health_check.side_effect = probe
        get_runner.return_value.restore.return_value = ProviderRuntimeStartResult("new-container", "http://127.0.0.1:29080/login", "http://127.0.0.1:29080/v1")

        def reconcile(_):
            close_old_connections()
            try:
                return reconcile_provider_runtime_health(runtime_id=runtime.id).status
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(reconcile, range(2)))
        self.assertEqual(statuses, [ProviderRuntimeAccount.STATUS_ACTIVE] * 2)
        get_runner.return_value.restore.assert_called_once()
        runtime.refresh_from_db()
        self.assertEqual(runtime.container_id, "new-container")
