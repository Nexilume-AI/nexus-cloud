"""Same proxy/lifecycle assertions with only the installed Personal owner.

These are real model/service tests with the original mocked process controller,
not evidence of a live provider login or a real Docker restart.
"""
from django.test import TestCase, override_settings
from tempfile import TemporaryDirectory
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from apps.providers import runtime_services
from apps.providers.models import ProviderRuntimeAccount
from apps.providers.runtime_runner import ProviderRuntimeHealthResult
from apps.tenancy.models import Project, Tenant

from nexus_personal.services import provision_owner
from tests.provider_proxy_runtime_guards import ProviderProxyRuntimeGuards
from .test_installation import PASSWORD


@override_settings(NEXUS_PROVIDER_RUNTIME_RUNNER="fake")
class PersonalProviderProxyRuntimeTests(ProviderProxyRuntimeGuards, TestCase):
    @classmethod
    def setUpTestData(cls):
        installation = provision_owner(email="proxy-owner@example.test", password=PASSWORD)
        cls.provider_owner = installation.owner
        cls.provider_tenant = installation.tenant

    def setUp(self):
        storage = TemporaryDirectory(prefix="nexus-provider-proxy-test-")
        self.addCleanup(storage.cleanup)
        settings = override_settings(NEXUS_PROVIDER_RUNTIME_STORAGE_ROOT=storage.name)
        settings.enable()
        self.addCleanup(settings.disable)

    def foreign_runtimes(self, status):
        other = get_user_model().objects.create_user(username="other-maintenance-owner")
        project = Project.objects.create(tenant=self.provider_tenant, name="Other project")
        tenant = Tenant.objects.create(name="Other instance", slug="other-maintenance-instance")
        rows = [ProviderRuntimeAccount.objects.create(
            tenant=scope_tenant, owner=owner, project=scope_project,
            name=f"Foreign {index}", runtime_type=ProviderRuntimeAccount.RUNTIME_CODEX_PROXY,
            status=status,
        ) for index, (scope_tenant, owner, scope_project) in enumerate((
            (self.provider_tenant, other, None),
            (self.provider_tenant, self.provider_owner, project),
            (tenant, self.provider_owner, None),
        ))]
        ProviderRuntimeAccount.objects.filter(pk__in=[row.pk for row in rows]).update(
            updated_at=timezone.now() - timedelta(hours=1))
        return rows

    def test_startup_skips_foreign_owner_project_and_instance_without_touching_credentials(self):
        rows = self.foreign_runtimes(ProviderRuntimeAccount.STATUS_UNHEALTHY)
        before = list(ProviderRuntimeAccount.objects.order_by("id").values())
        with patch("apps.providers.runtime_services.get_provider_runtime_runner") as runner:
            runner.return_value.health_check.return_value = ProviderRuntimeHealthResult(True, False, "available")
            self.assertEqual(runtime_services.restore_provider_runtime_processes(),
                             {"examined": 0, "restored": 0, "already_running": 0, "failed": 0})
            runner.assert_not_called()
        self.assertEqual(list(ProviderRuntimeAccount.objects.order_by("id").values()), before)
        self.assertEqual(len(rows), 3)

    def test_direct_health_lookup_rechecks_installed_scope(self):
        rows = self.foreign_runtimes(ProviderRuntimeAccount.STATUS_ACTIVE)
        before = list(ProviderRuntimeAccount.objects.order_by("id").values())
        with patch("apps.providers.runtime_services.get_provider_runtime_runner") as runner:
            runner.return_value.health_check.return_value = ProviderRuntimeHealthResult(True, False, "available")
            for runtime in rows:
                self.assertIsNone(runtime_services.reconcile_provider_runtime_health(runtime_id=runtime.pk))
            runner.assert_not_called()
        self.assertEqual(list(ProviderRuntimeAccount.objects.order_by("id").values()), before)

    def test_interrupted_start_and_stop_sweeps_preserve_foreign_records(self):
        rows = self.foreign_runtimes(ProviderRuntimeAccount.STATUS_STARTING)
        with patch("apps.providers.runtime_services.get_provider_runtime_runner") as runner:
            for status, operation, count in (
                (ProviderRuntimeAccount.STATUS_STARTING, runtime_services.reconcile_stale_provider_runtime_starts, "recovered"),
                (ProviderRuntimeAccount.STATUS_STOPPING, runtime_services.reconcile_stale_provider_runtime_stops, "stopped"),
            ):
                ProviderRuntimeAccount.objects.filter(pk__in=[row.pk for row in rows]).update(
                    status=status, updated_at=timezone.now() - timedelta(hours=1))
                before = list(ProviderRuntimeAccount.objects.order_by("id").values())
                with self.subTest(status=status):
                    self.assertEqual(operation(), {"examined": 0, count: 0, "failed": 0})
                    self.assertEqual(list(ProviderRuntimeAccount.objects.order_by("id").values()), before)
            runner.assert_not_called()

    def test_disabled_owner_blocks_all_recovery_before_controller_access(self):
        runtime = ProviderRuntimeAccount.objects.create(
            tenant=self.provider_tenant, owner=self.provider_owner, name="Owner disabled",
            runtime_type=ProviderRuntimeAccount.RUNTIME_CODEX_PROXY,
            status=ProviderRuntimeAccount.STATUS_UNHEALTHY)
        get_user_model().objects.filter(pk=self.provider_owner.pk).update(is_active=False)
        before = list(ProviderRuntimeAccount.objects.values())
        with patch("apps.providers.runtime_services.get_provider_runtime_runner") as runner:
            runner.return_value.health_check.return_value = ProviderRuntimeHealthResult(True, False, "available")
            for operation in (
                runtime_services.restore_provider_runtime_processes,
                lambda: runtime_services.reconcile_provider_runtime_health(runtime_id=runtime.pk),
                runtime_services.reconcile_stale_provider_runtime_starts,
                runtime_services.reconcile_stale_provider_runtime_stops,
            ):
                with self.subTest(operation=operation), self.assertRaises(ImproperlyConfigured):
                    operation()
            runner.assert_not_called()
        self.assertEqual(list(ProviderRuntimeAccount.objects.values()), before)

    def test_owner_change_during_health_probe_prevents_restore_and_state_write(self):
        other = get_user_model().objects.create_user(username="replacement-maintenance-owner")
        runtime = ProviderRuntimeAccount.objects.create(
            tenant=self.provider_tenant, owner=self.provider_owner, name="Changed during probe",
            runtime_type=ProviderRuntimeAccount.RUNTIME_CODEX_PROXY,
            status=ProviderRuntimeAccount.STATUS_UNHEALTHY)
        def change_owner(**kwargs):
            ProviderRuntimeAccount.objects.filter(pk=runtime.pk).update(owner=other)
            return ProviderRuntimeHealthResult(False, False, "container is stopped")
        with patch("apps.providers.runtime_services.get_provider_runtime_runner") as runner:
            runner.return_value.health_check.side_effect = change_owner
            with self.assertRaises(ProviderRuntimeAccount.DoesNotExist):
                runtime_services.reconcile_provider_runtime_health(runtime_id=runtime.pk)
            runner.return_value.health_check.assert_called_once()
            runner.return_value.restore.assert_not_called()
        runtime.refresh_from_db()
        self.assertEqual(runtime.owner_id, other.pk)
        self.assertEqual(runtime.status, ProviderRuntimeAccount.STATUS_UNHEALTHY)
        self.assertIsNone(runtime.last_health_check_at)
        self.assertEqual(runtime.encrypted_proxy_api_key, "")
