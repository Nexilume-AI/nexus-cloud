"""Owned configuration through real HTTP/DB; not repository storage or Docker acceptance."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.agents.models import Agent, AgentVersion, AgentResourceConfig
from apps.tenancy.models import Project
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


@override_settings(ROOT_URLCONF="nexus_personal.urls")
class PersonalConfigurationHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.installation = provision_owner(email="configuration-owner@example.test", password=PASSWORD)
        cls.other = get_user_model().objects.create_user(username="configuration-other")

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.installation.owner)
        self.client.get("/api/v1/public/bootstrap/")
        self.csrf = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value}
        response = self.client.post("/api/v1/agents/", {"name": "configured-agent"},
                                    format="json", **self.csrf)
        self.assertEqual(response.status_code, 201, response.content)
        self.agent = Agent.objects.get(pk=response.data["id"])
        self.base = f"/api/v1/agents/{self.agent.pk}/"

    def post(self, endpoint, data=None):
        return self.client.post(self.base + endpoint, data or {}, format="json", **self.csrf)

    def test_version_history_snapshot_and_rollback(self):
        self.agent.repo_metadata = {"commit_id": "first", "tools": [{"name": "echo"}], "rating": 5}
        self.agent.save(update_fields=["repo_metadata"])
        first = self.post("versions/publish/", {"release_notes": " First release "})
        self.assertEqual(first.status_code, 201, first.content)
        self.agent.repo_metadata = {"commit_id": "second", "tools": [{"name": "changed"}]}
        self.agent.save(update_fields=["repo_metadata"])
        second = self.post("versions/publish/", {"release_notes": "Second release"})
        self.assertEqual(second.status_code, 201, second.content)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.current_version, "v2")
        history = self.client.get(self.base + "versions/")
        self.assertEqual(history.status_code, 200, history.content)
        self.assertEqual([row["version"] for row in history.data], ["v2", "v1"])
        version = AgentVersion.objects.get(agent=self.agent, version="v1")
        self.assertEqual(version.artifact_metadata["tools"], [{"name": "echo"}])
        self.assertEqual(version.artifact_metadata["release_notes"], "First release")
        self.assertNotIn("rating", version.artifact_metadata)
        self.assertEqual(self.post("versions/v1/rollback/").status_code, 200)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.current_version, "v1")
        self.assertEqual(AgentVersion.objects.filter(agent=self.agent).count(), 2)
        self.assertEqual(self.post("versions/no-such-version/rollback/").status_code, 404)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.current_version, "v1")

    def test_repository_metadata_compatibility_does_not_claim_artifact_execution(self):
        response = self.post("repo/init/")
        self.assertEqual(response.status_code, 200, response.content)
        self.agent.refresh_from_db()
        self.assertTrue(self.agent.repo_metadata["initialized"])
        response = self.post("repo/push/", {"commit_id": "metadata-commit"})
        self.assertEqual(response.status_code, 200, response.content)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.repo_metadata["commit_id"], "metadata-commit")
        self.assertFalse(AgentVersion.objects.filter(agent=self.agent).exists())
        self.assertEqual(self.agent.runtime_deployments.count(), 0)

    def test_resources_use_existing_docker_units_and_preserve_single_configuration(self):
        response = self.post("resources/", {"cpu": "500m", "memory": "512m"})
        self.assertEqual(response.status_code, 200, response.content)
        config_id = response.data["id"]
        response = self.post("resources/", {"cpu": "1", "memory": "1g"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["id"], config_id)
        self.assertEqual(AgentResourceConfig.objects.filter(agent=self.agent).count(), 1)
        config = AgentResourceConfig.objects.get(agent=self.agent)
        self.assertEqual((config.cpu, config.memory), ("1", "1g"))

    def test_resource_invalid_values_or_capacity_rejection_preserve_current_config(self):
        self.assertEqual(self.post("resources/", {"cpu": "1", "memory": "512m"}).status_code, 200)
        for data in ({"cpu": "NaN", "memory": "512m"}, {"cpu": "0", "memory": "512m"},
                     {"cpu": "1", "memory": "../file"}, {"cpu": "1", "memory": "1m"}, {"cpu": "1"}):
            with self.subTest(data=data):
                response = self.post("resources/", data)
                self.assertEqual(response.status_code, 400, response.content)
        for data in ({"cpu": "999999", "memory": "512m"}, {"cpu": "1", "memory": "999999g"}):
            response = self.post("resources/", data)
            self.assertEqual(response.status_code, 409, response.content)
        config = AgentResourceConfig.objects.get(agent=self.agent)
        self.assertEqual((config.cpu, config.memory), ("1", "512m"))

    def test_all_mutations_require_csrf_and_no_commercial_routes_are_composed(self):
        mutations = (("repo/init/", {}), ("repo/push/", {}), ("versions/publish/", {}),
                     ("versions/v1/rollback/", {}), ("resources/", {"cpu": "1", "memory": "512m"}))
        for endpoint, data in mutations:
            response = self.client.post(self.base + endpoint, data, format="json")
            self.assertEqual(response.status_code, 403, endpoint)
        self.assertFalse(AgentVersion.objects.exists())
        self.assertFalse(AgentResourceConfig.objects.exists())
        for endpoint in ("pricing/", "publication/", "visibility/", "keys/"):
            self.assertEqual(self.post(endpoint).status_code, 404, endpoint)

    def test_anonymous_other_owner_and_other_project_cannot_read_or_mutate(self):
        self.assertIn(APIClient().get(self.base + "versions/").status_code, (401, 403))
        for changes in ({"created_by": self.other},
                        {"created_by": self.installation.owner,
                         "project": Project.objects.create(tenant=self.installation.tenant, name="Elsewhere")}):
            for field, value in changes.items():
                setattr(self.agent, field, value)
            self.agent.save(update_fields=list(changes))
            self.assertEqual(self.client.get(self.base + "versions/").status_code, 404)
            for endpoint, data in (("repo/init/", {}), ("repo/push/", {}),
                                   ("versions/publish/", {}), ("versions/v1/rollback/", {}),
                                   ("resources/", {"cpu": "1", "memory": "512m"})):
                self.assertEqual(self.post(endpoint, data).status_code, 404, endpoint)
        self.assertFalse(AgentVersion.objects.exists())
        self.assertFalse(AgentResourceConfig.objects.exists())
