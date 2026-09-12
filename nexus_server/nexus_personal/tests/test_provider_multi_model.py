"""Actual multi-model upstream discovery in the isolated owner installation."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.providers.models import ProviderRuntimeAccount, ProviderRuntimeModelOffer
from tests.provider_model_id_guards import ProviderModelIDGuards
from .provider_http_fixture import ProviderHTTPFixture


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalMultiModelProviderTests(ProviderModelIDGuards, ProviderHTTPFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.account, self.runtime = self.create("multi-model")
        self.runtime.status = ProviderRuntimeAccount.STATUS_ACTIVE
        self.runtime.internal_api_url = self.account.url
        self.runtime.save()
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.installation.owner)
        self.client.get("/api/v1/public/bootstrap/")
        self.headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value,
                        "HTTP_ORIGIN": "http://testserver"}
        self.path = f"/api/v1/provider-connections/{self.account.pk}/"

    def refresh(self):
        return self.client.post(self.path + "models/refresh/", {}, format="json", **self.headers)

    def test_actual_catalog_add_remove_restore_preserves_offer_identity(self):
        type(self).catalog_models = ["vendor-a", "vendor-b"]
        response = self.refresh()
        self.assertEqual(response.status_code, 200, response.data)
        offers = {row.upstream_model_id: row for row in self.runtime.model_offers.select_related("canonical_model")}
        self.assertEqual(set(offers), {"vendor-a", "vendor-b"})
        for name, offer in offers.items():
            self.assertEqual(offer.canonical_model.key, name)
            self.assertEqual(offer.health_status, "healthy")
            self.assertIn(("POST", "/v1/chat/completions", name), self.calls)
        type(self).catalog_models = ["vendor-a", "vendor-c"]
        self.assertEqual(self.refresh().status_code, 200)
        offers["vendor-b"].refresh_from_db()
        self.assertEqual(offers["vendor-b"].status, ProviderRuntimeModelOffer.STATUS_UNAVAILABLE)
        type(self).catalog_models = ["vendor-a", "vendor-b", "vendor-c"]
        self.assertEqual(self.refresh().status_code, 200)
        restored = self.runtime.model_offers.get(upstream_model_id="vendor-b")
        self.assertEqual(restored.pk, offers["vendor-b"].pk)
        self.assertEqual(restored.canonical_model_id, offers["vendor-b"].canonical_model_id)
        self.assertNotEqual(restored.status, ProviderRuntimeModelOffer.STATUS_UNAVAILABLE)
        self.assertEqual(restored.health_status, "healthy")
        detail = self.client.get(self.path)
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["model_count"], 3)
        self.assertEqual({row["upstream_model_id"] for row in detail.data["models"]}, set(type(self).catalog_models))

    def test_invalid_later_model_does_not_partially_replace_known_catalog(self):
        type(self).catalog_models = ["known-a", "known-b"]
        self.assertEqual(self.refresh().status_code, 200)
        before = set(self.runtime.model_offers.values_list("pk", "upstream_model_id", "canonical_model_id"))
        type(self).catalog_models = ["must-not-be-added", "x" * 256]
        response = self.refresh()
        self.assertGreaterEqual(response.status_code, 400, response.data)
        self.assertEqual(set(self.runtime.model_offers.values_list("pk", "upstream_model_id", "canonical_model_id")), before)
        self.assertFalse(self.runtime.model_offers.filter(upstream_model_id="must-not-be-added").exists())
        type(self).catalog_models = ["known-a", "known-b"]
        self.assertEqual(self.refresh().status_code, 200)
        self.assertEqual(set(self.runtime.model_offers.values_list("pk", "upstream_model_id", "canonical_model_id")), before)

    def test_single_model_refresh_contacts_only_selected_model_and_preserves_other_offer(self):
        type(self).catalog_models = ["selected-model", "untouched-model"]
        response = self.refresh()
        self.assertEqual(response.status_code, 200, response.data)
        selected = self.runtime.model_offers.get(upstream_model_id="selected-model")
        untouched = self.runtime.model_offers.get(upstream_model_id="untouched-model")
        untouched.health_status = ProviderRuntimeModelOffer.HEALTH_DEGRADED
        untouched.health_reason = "Retain independent model state"
        untouched.save()
        before = ProviderRuntimeModelOffer.objects.values().get(pk=untouched.pk)
        self.upstream.assert_healthy()
        self.calls.clear()

        refreshed = self.client.post(self.path + f"models/{selected.pk}/refresh/", {},
                                     format="json", **self.headers)

        self.assertEqual(refreshed.status_code, 200, refreshed.data)
        self.assertEqual(self.calls, [("GET", "/v1/models"),
                                     ("POST", "/v1/chat/completions", "selected-model")])
        selected.refresh_from_db()
        self.assertEqual(selected.health_status, ProviderRuntimeModelOffer.HEALTH_HEALTHY)
        self.assertEqual(ProviderRuntimeModelOffer.objects.values().get(pk=untouched.pk), before)
