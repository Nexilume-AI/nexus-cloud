"""Shared Provider transaction assertions; each distribution supplies its own fixtures."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from django.db import close_old_connections, connection, transaction
from rest_framework.exceptions import APIException
from apps.providers import runtime_services as providers
from apps.providers.models import ProviderRuntimeModelOffer
from apps.tenancy.models import Tenant
from django.db.backends.postgresql.base import Database


class ProviderProbeGuardrails:
    def in_thread(self, function):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout='5s'")
                cursor.execute("SET statement_timeout='8s'")
            return function()
        finally:
            connection.close()

    def test_provider_network_probe_runs_outside_transaction_and_lock(self):
        runtime,offer=self.fixture_offer()
        def discovery(**kwargs):
            self.assertFalse(connection.in_atomic_block)
            def edit():
                with transaction.atomic():
                    ProviderRuntimeModelOffer.objects.select_for_update(nowait=True).get(pk=offer.pk)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(self.in_thread,edit).result(timeout=5)
            return [offer.upstream_model_id]
        def probe(**kwargs):
            self.assertFalse(connection.in_atomic_block)
            return True,"Healthy"
        with patch.object(providers,"discover_runtime_model_ids",side_effect=discovery), patch.object(providers,"probe_runtime_model",side_effect=probe):
            result=providers.refresh_provider_runtime_model_offer(request=self.request,runtime_id=runtime.pk,offer_id=offer.pk)
        self.assertEqual(result.health_status, ProviderRuntimeModelOffer.HEALTH_HEALTHY)

    def test_late_probe_does_not_overwrite_disabled_offer_or_stopped_runtime(self):
        runtime,offer=self.fixture_offer()
        for target in (offer,runtime):
            with self.subTest(target=target._meta.model_name):
                def probe(**kwargs):
                    target.status="disabled"
                    target.save(update_fields=["status","updated_at"])
                    return True,"Late success"
                with patch.object(providers,"discover_runtime_model_ids",return_value=[offer.upstream_model_id]), patch.object(providers,"probe_runtime_model",side_effect=probe):
                    with self.assertRaises(APIException) as raised:
                        providers.refresh_provider_runtime_model_offer(request=self.request,runtime_id=runtime.pk,offer_id=offer.pk)
                    self.assertEqual(raised.exception.status_code,409)
                offer.refresh_from_db()
                self.assertEqual(offer.health_status,ProviderRuntimeModelOffer.HEALTH_DEGRADED)
                target.status="active" if target is runtime else "detected"
                target.save(update_fields=["status","updated_at"])

    def test_probe_network_failure_preserves_offer(self):
        runtime,offer=self.fixture_offer()
        with patch.object(providers,"discover_runtime_model_ids",side_effect=providers.ProviderRuntimeError()):
            with self.assertRaises(providers.ProviderRuntimeError):
                providers.refresh_provider_runtime_model_offer(request=self.request,runtime_id=runtime.pk,offer_id=offer.pk)
        offer.refresh_from_db()
        self.assertEqual(offer.health_status,ProviderRuntimeModelOffer.HEALTH_DEGRADED)
        self.assertIsNone(offer.last_health_check_at)

    def assert_postgres_connection_budgets(self, params):
        db=Database.connect(**params)
        try:
            with db.cursor() as cursor:
                cursor.execute("SHOW statement_timeout")
                self.assertEqual(cursor.fetchone()[0],"500ms")
                cursor.execute("SHOW lock_timeout")
                self.assertEqual(cursor.fetchone()[0],"100ms")
                cursor.execute("SHOW idle_in_transaction_session_timeout")
                self.assertEqual(cursor.fetchone()[0],"2min")
                with self.assertRaises(Exception) as timeout:
                    cursor.execute("SELECT pg_sleep(1)")
                self.assertEqual(getattr(timeout.exception,"sqlstate",None),"57014")
            db.rollback()
            with transaction.atomic():
                Tenant.objects.select_for_update().get(pk=self.tenant.pk)
                with db.cursor() as cursor:
                    with self.assertRaises(Exception) as timeout:
                        cursor.execute('SELECT id FROM tenancy_tenant WHERE id=%s FOR UPDATE',[self.tenant.pk])
                    self.assertEqual(getattr(timeout.exception,"sqlstate",None),"55P03")
            db.rollback()
        finally:
            db.close()
