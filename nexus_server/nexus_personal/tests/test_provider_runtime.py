"""Shared Direct API runtime against an actual loopback HTTP upstream, no Cloud."""
from django.test import TestCase, override_settings
from rest_framework import exceptions
from apps.common.resource_limits import capability_state
from apps.providers import runtime_services as runtime_api
from apps.providers.models import ProviderAccount, ProviderRuntimeAccount, ProviderRuntimeModelOffer
from apps.tenancy.models import Project
from nexus_personal.resource_limits import PersonalCapacityExceeded
from .provider_http_fixture import ProviderHTTPFixture


class PersonalProviderRuntimeTests(ProviderHTTPFixture, TestCase):
    def test_create_start_discover_stop_restart_and_remove_with_actual_http(self):
        account, runtime = self.create()
        self.assertNotEqual(account.encrypted_key, "local-provider-test-key")
        runtime_api.start_provider_runtime(request=self.request(), runtime_id=str(runtime.pk))
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, "active")
        offer = runtime.model_offers.get()
        self.assertEqual(offer.upstream_model_id, "personal-model")
        self.assertEqual(offer.health_status, "healthy")
        self.assertIn(("GET", "/v1/models"), self.calls)
        self.assertIn(("POST", "/v1/chat/completions", "personal-model"), self.calls)
        self.assertEqual(runtime_api.get_provider_runtime(request=self.request(), runtime_id=str(runtime.pk)).pk, runtime.pk)
        self.assertEqual(list(runtime_api.list_provider_runtimes(request=self.request()))[0].pk, runtime.pk)
        runtime_api.stop_provider_runtime(request=self.request(), runtime_id=str(runtime.pk))
        runtime.refresh_from_db()
        self.assertEqual(runtime.status, "stopped")
        runtime_api.start_provider_runtime(request=self.request(), runtime_id=str(runtime.pk))
        self.assertEqual(runtime.model_offers.get().pk, offer.pk)
        runtime_api.remove_provider_runtime(request=self.request(), runtime_id=str(runtime.pk))
        self.assertEqual(list(runtime_api.list_provider_runtimes(request=self.request())), [])

    def test_invalid_directory_retains_known_models_and_recovers(self):
        _, runtime = self.create()
        runtime_api.start_provider_runtime(request=self.request(), runtime_id=str(runtime.pk))
        offer = runtime.model_offers.get()
        type(self).catalog_invalid = True
        with self.assertRaises(runtime_api.ProviderRuntimeError):
            runtime_api.refresh_provider_runtime_models(request=self.request(), runtime_id=str(runtime.pk))
        offer.refresh_from_db()
        self.assertNotEqual(offer.status, "unavailable")
        type(self).catalog_invalid = False
        runtime_api.refresh_provider_runtime_models(request=self.request(), runtime_id=str(runtime.pk))
        self.assertEqual(runtime.model_offers.get().pk, offer.pk)

    def test_foreign_owner_project_and_source_bindings_are_not_accessible(self):
        account, runtime = self.create()
        ProviderRuntimeAccount.objects.filter(pk=runtime.pk).update(owner=self.other)
        with self.assertRaises(runtime_api.ProviderRuntimeNotFound):
            runtime_api.get_provider_runtime(request=self.request(), runtime_id=str(runtime.pk))
        ProviderRuntimeAccount.objects.filter(pk=runtime.pk).update(owner=self.installation.owner,
            project=Project.objects.create(tenant=self.installation.tenant, name="Different"))
        self.assertEqual(list(runtime_api.list_provider_runtimes(request=self.request())), [])
        ProviderAccount.objects.filter(pk=account.pk).update(created_by=self.other)
        with self.assertRaises(exceptions.NotFound):
            runtime_api.resolve_source_provider_account(tenant=self.installation.tenant, account_id=account.pk)

    def test_temporary_inference_failure_is_not_replayed_and_can_recover(self):
        _, runtime = self.create()
        runtime_api.start_provider_runtime(request=self.request(), runtime_id=str(runtime.pk))
        offer_id = runtime.model_offers.get().pk
        type(self).calls.clear()
        type(self).probe_status = 503
        with self.assertRaises(runtime_api.ProviderRuntimeError):
            runtime_api.refresh_provider_runtime_models(request=self.request(), runtime_id=str(runtime.pk))
        self.assertEqual(sum(call[0] == "POST" for call in self.calls), 1)
        self.assertEqual(runtime.model_offers.get().pk, offer_id)
        type(self).probe_status = 200
        runtime_api.refresh_provider_runtime_models(request=self.request(), runtime_id=str(runtime.pk))
        offer = runtime.model_offers.get()
        self.assertEqual(offer.pk, offer_id)
        self.assertEqual(offer.health_status, "healthy")

    @override_settings(NEXUS_PERSONAL_MODEL_LIMITS={"models.provider_connections": 1, "models.model_offers": 0})
    def test_measured_limits_reject_create_and_discovery_without_fake_receipts(self):
        _, runtime = self.create()
        with self.assertRaises(PersonalCapacityExceeded):
            self.create(name="over-limit")
        self.assertEqual(ProviderAccount.objects.count(), 1)
        with self.assertRaises(PersonalCapacityExceeded):
            runtime_api.start_provider_runtime(request=self.request(), runtime_id=str(runtime.pk))
        self.assertFalse(ProviderRuntimeModelOffer.objects.exists())
        state = capability_state(tenant=self.installation.tenant, code="models.provider_connections")
        self.assertEqual(state["used"], 1)
        self.assertEqual(state["remaining"], 0)
