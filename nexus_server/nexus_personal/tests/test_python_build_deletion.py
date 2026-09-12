"""Failed-build deletion through actual owner login/CSRF, without private fixtures."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.agents.models import AgentPythonBuild
from apps.tenancy.models import Project
from nexus_personal.services import provision_owner
from tests.python_build_guards import DIGEST
from tests.python_build_deletion_guards import PythonBuildDeletionFixture, PythonBuildDeletionHTTPGuards
from .test_installation import PASSWORD
from . import test_python_builds as build_tests


class PersonalBuildDeletionFixture(PythonBuildDeletionFixture):
    upload = build_tests.PersonalPythonBuildTests.upload

    def setUp(self):
        super().setUp()
        self.row = provision_owner(email="build-delete-owner@example.test", password=PASSWORD)
        self.other = get_user_model().objects.create_user(username="build-delete-other")
        build_tests.PersonalPythonBuildTests.setUp(self)


@override_settings(NEXUS_AGENT_PYTHON_BUILDS_ENABLED=True, NEXUS_AGENT_PYTHON_BASE_IMAGE=DIGEST,
                   NEXUS_AGENT_RUNTIME_HOST_ID="personal-build-delete-test")
class PersonalBuildDeletionTests(PersonalBuildDeletionFixture, PythonBuildDeletionHTTPGuards, TestCase):
    def test_deletion_rechecks_owner_project_and_csrf_without_erasing_source(self):
        build = self.failed()
        original = build.source
        url = f"{self.url}{build.pk}/"
        self.assertEqual(self.client.delete(url).status_code, 403)
        other_client = APIClient(enforce_csrf_checks=True)
        other_client.force_login(self.other)
        self.assertIn(other_client.delete(url).status_code, (401, 403))
        self.assertEqual(self.client.delete(url, HTTP_X_NEXUS_PROJECT="foreign", **self.headers).status_code, 403)
        project = Project.objects.create(tenant=self.tenant, name="Foreign project")
        self.agent.project = project
        self.agent.save(update_fields=["project"])
        self.assertEqual(self.delete(build).status_code, 404)
        build.refresh_from_db()
        self.assertEqual(build.source, original)
        self.assertEqual(build.status, "failed")
        self.assertEqual(AgentPythonBuild.objects.count(), 1)
