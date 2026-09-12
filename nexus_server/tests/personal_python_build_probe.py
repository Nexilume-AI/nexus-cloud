"""Python upload/queue/deletion guards in the initialized PostgreSQL fixture.

Not a database runner or service. Builder outcomes are mocks, not live Docker.
"""
import io
import re
import unittest
from urllib.parse import urlsplit
from django.conf import settings
from django.db import connection
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from apps.agents.models import Agent, AgentPythonBuild
from nexus_personal.models import PersonalInstallation
from tests.python_build_guards import PythonBuildQueueGuards, DIGEST
from tests.python_build_deletion_guards import (
    PythonBuildDeletionFixture, PythonBuildDeletionHTTPGuards, PythonBuildCleanupGuards,
)


class SecureOwnerClient(APIClient):
    def generic(self, method, path, data="", content_type="application/octet-stream", secure=False, **extra):
        extra.setdefault("HTTP_HOST", urlsplit(settings.NEXUS_PUBLIC_BASE_URL).netloc)
        return super().generic(method, path, data=data, content_type=content_type, secure=True, **extra)


@override_settings(NEXUS_AGENT_PYTHON_BUILDS_ENABLED=True, NEXUS_AGENT_PYTHON_BASE_IMAGE=DIGEST)
class PersonalPythonBuildProbeTests(PythonBuildQueueGuards, TestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        row = PersonalInstallation.objects.select_related("owner", "tenant", "project").get(slot=1)
        self.owner, self.tenant = row.owner, row.tenant
        self.assertFalse(self.owner.is_superuser)
        self.client = SecureOwnerClient()
        token, _ = Token.objects.get_or_create(user=self.owner)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token.key)
        self.headers = {}
        response = self.client.post("/api/v1/agents/", {"name": "python-probe"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.agent = Agent.objects.get(pk=response.data["id"])
        self.url = f"/api/v1/agents/{self.agent.pk}/runtime/python-builds/"


@override_settings(NEXUS_AGENT_PYTHON_BUILDS_ENABLED=True, NEXUS_AGENT_PYTHON_BASE_IMAGE=DIGEST)
class PersonalPythonBuildDeletionProbeTests(
        PythonBuildDeletionFixture, PythonBuildDeletionHTTPGuards, PythonBuildCleanupGuards, TestCase):
    # Reuse the actual initialized owner and production HTTP stack, not the
    # Personal test host. TestCase rolls back each guard without flushing the
    # installed owner or altering the production database configuration.
    setUp = PersonalPythonBuildProbeTests.setUp
    upload = PythonBuildQueueGuards.upload


def run(expected_database):
    if (connection.vendor != "postgresql"
            or not re.fullmatch(r"nexus_personal_[0-9a-f]{32}", expected_database)
            or connection.settings_dict["NAME"] != expected_database):
        raise AssertionError("Python build probe requires the exact fixture-owned PostgreSQL database")
    if AgentPythonBuild.objects.exists():
        raise AssertionError("Python build fixture is not fresh")
    suite = unittest.TestSuite(
        unittest.defaultTestLoader.loadTestsFromTestCase(case)
        for case in (PersonalPythonBuildProbeTests, PersonalPythonBuildDeletionProbeTests)
    )
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=1).run(suite)
    if not result.wasSuccessful() or result.skipped or result.testsRun != 21:
        # The guarded test is private-input aware; do not echo response bodies
        # or arbitrary builder exceptions into an installation acceptance log.
        names = [test.id().rsplit(".", 1)[-1] for test, _ in result.failures + result.errors]
        raise AssertionError("Personal Python build guards failed: " + ", ".join(names))
    return {"scope": "personal-python-build-postgres-guards", "tests_run": result.testsRun,
            "skipped": 0, "vendor": connection.vendor}
