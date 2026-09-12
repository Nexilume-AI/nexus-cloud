"""Real owner and HTTP Source fixture for shared deployment assertions."""
from apps.deployments.models import Deployment
from .test_deployment_http import PersonalDeploymentHTTPFixture


class PersonalDeploymentGuardFixture(PersonalDeploymentHTTPFixture):
    def routing_deployment(self):
        self.tenant = self.installation.tenant
        return Deployment.objects.get(pk=self.source()["id"])

    def routing_pool(self, deployment):
        return deployment.model_group_links.get().model_group

    def authorize_routing(self):
        self.assertFalse(self.installation.owner.is_superuser)
        self.assertTrue(self.client.handler.enforce_csrf_checks)

    def routing_request(self, method, *args, **kwargs):
        self.assertEqual(kwargs.get("HTTP_X_NEXUS_TENANT"), str(self.installation.tenant_id))
        return getattr(self.client, method)(*args, **kwargs, **self.headers)

    def routing_payload(self, response):
        return response.json()

    def deployment_fixture(self, *, endpoint=None):
        # Enterprise retains its original mock/fail URLs. Personal uses the
        # same real fixture server for both observations, never those hosts.
        if endpoint not in (None, "http://mock.local/v1", "http://fail.local/v1"):
            raise AssertionError("Unexpected legacy health fixture selector")
        deployment = self.routing_deployment()
        type(self).catalog_status = 503 if endpoint == "http://fail.local/v1" else 200
        type(self).calls.clear()
        return deployment

    def source_path(self, deployment):
        return f"/api/v1/deployments/{deployment.pk}/"
