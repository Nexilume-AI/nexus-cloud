"""Authenticated owner fixture reused by Provider HTTP and lifecycle guards."""
from django.core.cache import cache
from rest_framework.test import APIClient
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalProviderConnectionFixture:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.installation = provision_owner(email="recovery-owner@example.test", password=PASSWORD)
        self.owner = self.installation.owner
        self.tenant = self.installation.tenant
        self.assertFalse(self.owner.is_superuser)
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.owner)
        response = self.client.get("/api/v1/public/bootstrap/")
        self.assertEqual(response.status_code, 200, response.content)
        self.headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value,
                        "HTTP_ORIGIN": "http://testserver"}

    def create_connection(self, *, name="Production OpenAI", engine="direct_api", upstream="openai-compatible"):
        payload = {"name": name, "engine": engine, "upstream_provider": upstream}
        if engine == "direct_api":
            payload.update({"url": "https://provider.example.test/v1", "key": "secret-provider-key"})
        response = self.client.post("/api/v1/provider-connections/", payload,
                                    format="json", **self.headers)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotIn("secret-provider-key", response.content.decode())
        return response.data

    def request(self, method, path, data=None):
        return getattr(self.client, method)(path, data or {}, format="json", **self.headers)

    def connection_list(self, response):
        return response.json()
