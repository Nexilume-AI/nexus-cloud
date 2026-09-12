"""Shared upload/inspection checks with actual Personal owner session and CSRF.

Builder mocks retain their original unit-test meaning; no container executes.
"""
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, SimpleTestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from apps.agents.models import Agent, AgentPythonBuild
from apps.tenancy.models import Project
from nexus_personal.services import provision_owner
from tests.python_build_guards import SourceInspectionGuards, PythonBuildHTTPGuards, SOURCE, DIGEST
from .test_installation import PASSWORD


class PersonalSourceInspectionTests(SourceInspectionGuards, SimpleTestCase):
    pass


@override_settings(
    ROOT_URLCONF="nexus_personal.urls", NEXUS_AGENT_PYTHON_BUILDS_ENABLED=True,
    NEXUS_AGENT_PYTHON_BASE_IMAGE=DIGEST, NEXUS_AGENT_RUNTIME_HOST_ID="personal-python-test",
)
class PersonalPythonBuildTests(PythonBuildHTTPGuards, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="python-owner@example.test", password=PASSWORD)
        cls.other = get_user_model().objects.create_user(username="other-python-owner")

    def setUp(self):
        self.owner, self.tenant = self.row.owner, self.row.tenant
        self.assertFalse(self.owner.is_superuser)
        self.client = APIClient(enforce_csrf_checks=True)
        self.assertTrue(self.client.login(username=self.owner.username, password=PASSWORD))
        self.client.get("/api/v1/public/bootstrap/")
        self.headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value}
        response = self.client.post("/api/v1/agents/", {"name": "personal-python"}, format="json", **self.headers)
        self.assertEqual(response.status_code, 201, response.content)
        self.agent = Agent.objects.get(pk=response.data["id"])
        self.url = f"/api/v1/agents/{self.agent.pk}/runtime/python-builds/"

    def test_upload_requires_csrf_and_installed_owner(self):
        response = self.client.post(self.url, {"file": SimpleUploadedFile("agent.py", SOURCE.encode())},
                                    format="multipart")
        self.assertEqual(response.status_code, 403)
        peer = APIClient()
        token = Token.objects.create(user=self.other)
        peer.credentials(HTTP_AUTHORIZATION="Bearer " + token.key)
        response = peer.post(self.url, {"file": SimpleUploadedFile("agent.py", SOURCE.encode())},
                             format="multipart")
        self.assertIn(response.status_code, (401, 403))
        self.assertFalse(AgentPythonBuild.objects.exists())

    def test_enabled_build_api_rejects_foreign_owner_or_project(self):
        self.assertEqual(self.upload().status_code, 202)
        for changes in (
            {"created_by": self.other},
            {"created_by": self.owner, "project": Project.objects.create(tenant=self.tenant, name="Other")},
        ):
            for name, value in changes.items():
                setattr(self.agent, name, value)
            self.agent.save(update_fields=list(changes))
            self.assertEqual(self.client.get(self.url, **self.headers).status_code, 404)
            self.assertEqual(self.upload().status_code, 404)
        self.assertEqual(AgentPythonBuild.objects.count(), 1)
