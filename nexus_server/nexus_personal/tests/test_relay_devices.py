"""Real Community sessions, owner checks and Provider/Router registration."""
from uuid import uuid4
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.accounts.models import RelayDevice
from apps.deployments.models import Deployment
from .provider_http_fixture import ProviderHTTPFixture


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PUBLIC_BASE_URL="https://cloud.example",
                   NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalRelayTests(ProviderHTTPFixture, TestCase):
    base = "/api/v1/auth/relay/"

    def setUp(self):
        super().setUp()
        cache.clear()
        self.browser = APIClient(enforce_csrf_checks=True)
        self.browser.force_login(self.installation.owner)
        self.browser.get(self.base + "page/")
        self.csrf = {"HTTP_X_CSRFTOKEN": self.browser.cookies["csrftoken"].value}
        self.device = APIClient()
        response = self.device.post(self.base + "start/", {"name": "Test device", "models": [{
            "name": "Test model", "model": "personal-model", "endpoint": "https://models.example/v1",
            "protocol": "openai-compatible"}]}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.start = response.data

    def approve(self):
        result = self.browser.post(self.base + "approval/", {"code": self.start["user_code"],
            "approve": True, "tenant_id": str(self.installation.tenant_id)}, format="json", **self.csrf)
        self.assertEqual(result.status_code, 200, result.data)
        response = self.device.post(self.base + "token/", {"device_code": self.start["device_code"]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.device.credentials(HTTP_AUTHORIZATION="Bearer " + response.data["access_token"])
        return response.data

    def test_session_context_csrf_and_bearer_separation(self):
        review = self.browser.get(self.base + "approval/", {"code": self.start["user_code"]})
        self.assertEqual(review.status_code, 200, review.data)
        self.assertEqual([r["id"] for r in review.data["organizations"]], [str(self.installation.tenant_id)])
        data = {"code": self.start["user_code"], "approve": True, "tenant_id": str(self.installation.tenant_id)}
        self.assertEqual(self.browser.post(self.base + "approval/", data, format="json").status_code, 403)
        wrong = {**data, "tenant_id": str(uuid4())}
        self.assertEqual(self.browser.post(self.base + "approval/", wrong, format="json", **self.csrf).status_code, 403)
        self.approve()
        self.assertEqual(self.device.get(self.base + "models/").status_code, 200)
        for path in ["/api/v1/auth/whoami/", "/api/v1/providers/", "/api/v1/openai/v1/models", self.base + "devices/"]:
            self.assertGreaterEqual(self.device.get(path).status_code, 400)
        self.assertGreaterEqual(self.device.get(self.base + "models/", HTTP_ORIGIN="https://evil.example").status_code, 400)

    def test_registration_idempotence_and_revocation_against_real_http_upstream(self):
        self.approve()
        # Test-only loopback fixture replaces the approved HTTPS endpoint in DB.
        # Production start validation continues to reject HTTP endpoints.
        row = RelayDevice.objects.get(pk=self.start["device_id"])
        row.manifest[0]["endpoint"] = f"http://127.0.0.1:{self.upstream.server_port}/v1"
        row.save(update_fields=["manifest"])
        payload = {"model": "personal-model", "endpoint_api_key": "local-provider-test-key"}
        first = self.device.post(self.base + "models/", payload, format="json")
        self.assertEqual(first.status_code, 200, first.data)
        second = self.device.post(self.base + "models/", payload, format="json")
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data, second.data)
        source = Deployment.objects.get(pk=first.data["source_id"])
        self.assertEqual(source.status, "active")
        self.assertTrue(type(self).calls)
        revoke = self.browser.post(self.base + f"devices/{row.pk}/revoke/", {}, format="json", **self.csrf)
        self.assertEqual(revoke.status_code, 200, revoke.data)
        source.refresh_from_db()
        self.assertEqual(source.status, "disabled")
        self.assertGreaterEqual(self.device.get(self.base + "models/").status_code, 400)

    def test_owner_disable_invalidates_device_access(self):
        self.approve()
        owner = self.installation.owner
        owner.is_active = False
        owner.save(update_fields=["is_active"])
        self.assertGreaterEqual(self.device.get(self.base + "models/").status_code, 400)

    def test_outbound_new_provider_staged_activation(self):
        from unittest.mock import patch
        from apps.accounts.models import RelayModelBinding
        self.approve()
        row=RelayDevice.objects.get(pk=self.start['device_id'])
        upstream=f'http://127.0.0.1:{self.upstream.server_port}/v1'
        row.manifest[0].update(endpoint=upstream,transport='outbound');row.save(update_fields=['manifest'])
        first=self.device.post(self.base+'models/',{'model':'personal-model','endpoint_api_key':'local-provider-test-key'},format='json')
        self.assertEqual(first.status_code,200,first.data)
        self.assertEqual(first.data['status'],'prepared')
        binding=RelayModelBinding.objects.get(pk=first.data['binding_id'])
        self.assertEqual(Deployment.objects.filter(provider_runtime__source_provider_account=binding.provider).count(),0)
        # The real HTTP fixture represents the already-authenticated broker.
        with patch('apps.accounts.relay_transport.endpoint',return_value=upstream):
            activated=self.device.post(self.base+'transport/activate/',{'binding_id':str(binding.pk)},format='json')
        self.assertEqual(activated.status_code,200,activated.data)
        row.refresh_from_db();self.assertIn('router_id',row.registrations['personal-model'])
        self.assertEqual(self.device.post(self.base+'revoke/',{},format='json').status_code,200)
        binding.refresh_from_db();self.assertEqual(binding.status,'revoked')
