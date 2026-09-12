"""Operational maintenance against the existing actual HTTP upstream fixture."""
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from apps.providers import tasks, runtime_services
from apps.providers.models import ProviderRuntimeAccount, ProviderRuntimeModelOffer
from nexus_personal.worker_composition import BEAT_SCHEDULE
from .provider_http_fixture import ProviderHTTPFixture


class PersonalWorkerMaintenanceTests(ProviderHTTPFixture, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()  # The isolated test host uses its private LocMemCache.

    def due(self, runtime):
        cache.delete(f'nexus:providers:catalog:{runtime.pk}')
        runtime.model_offers.update(last_discovered_at=timezone.now() - timedelta(days=1))

    def test_real_catalog_refresh_failure_then_recovery_preserves_model_identity(self):
        _, runtime = self.create()
        runtime_services.start_provider_runtime(request=self.request(), runtime_id=str(runtime.pk))
        offer_id = runtime.model_offers.get().pk
        self.due(runtime)
        type(self).catalog_invalid = True
        result = tasks.refresh_active_provider_runtime_models()
        self.assertEqual(result['failed'], 1)
        self.assertEqual(runtime.model_offers.get().pk, offer_id)
        type(self).catalog_invalid = False
        self.due(runtime)
        result = tasks.refresh_active_provider_runtime_models()
        self.assertEqual(result['refreshed'], 1)
        self.assertEqual(runtime.model_offers.get().pk, offer_id)
        self.assertIn(('GET', '/v1/models'), self.calls)
        self.assertNotIn('settlement_pending', result)

    def test_real_health_and_stale_start_recovery_do_not_read_accounting_models(self):
        _, runtime = self.create()
        runtime_services.start_provider_runtime(request=self.request(), runtime_id=str(runtime.pk))
        result = tasks.reconcile_provider_runtime_health_task()
        self.assertEqual(result['healthy'], 1)
        self.assertNotIn('settlement_pending', result)
        ProviderRuntimeAccount.objects.filter(pk=runtime.pk).update(status='starting', updated_at=timezone.now() - timedelta(hours=1))
        result = tasks.reconcile_provider_runtime_health_task()
        self.assertEqual(result['stale_starts_recovered'], 1)
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, 'active')

    def test_catalog_does_not_inspect_foreign_owner_rows(self):
        _, runtime = self.create()
        runtime_services.start_provider_runtime(request=self.request(), runtime_id=str(runtime.pk))
        self.due(runtime)
        ProviderRuntimeAccount.objects.filter(pk=runtime.pk).update(owner=self.other)
        type(self).calls.clear()
        self.assertEqual(tasks.refresh_active_provider_runtime_models()['refreshed'], 0)
        self.assertEqual(self.calls, [])

    def test_overlap_lock_blocks_duplicate_maintenance_without_claiming_success(self):
        cache.set('nexus:providers:maintenance:model-catalog', 'other-worker', timeout=30)
        result = tasks.refresh_active_provider_runtime_models()
        self.assertEqual(result, {'refreshed': 0, 'failed': 0, 'skipped_overlap': 1})
        self.assertEqual(cache.get('nexus:providers:maintenance:model-catalog'), 'other-worker')

    def test_schedule_is_explicit_operational_tasks_not_commercial_beat(self):
        from celery import current_app
        from importlib import import_module
        from nexus_personal.worker_composition import TASK_MODULES
        for module in TASK_MODULES:
            import_module(module)
        for entry in BEAT_SCHEDULE.values():
            self.assertIn(entry['task'], current_app.tasks)
            self.assertGreater(entry['schedule'], 0)
            self.assertNotIn('billing', entry['task'])
            self.assertNotIn('tokenbank', entry['task'])
            self.assertNotIn('simulate', entry['task'])
