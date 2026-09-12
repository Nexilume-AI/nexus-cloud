"""Shared wire contracts with private source absent, not live Edge transport."""
from django.test import SimpleTestCase, TestCase
from tests.agent_protocol_guards import MCPTaskResponseGuards, EdgeJWTGuards, DockerBrowserEnvironmentGuards
from apps.agents.models import Agent, AgentRuntimeImage
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalMCPTaskResponseTests(MCPTaskResponseGuards, SimpleTestCase):
    pass


class PersonalDockerBrowserEnvironmentTests(DockerBrowserEnvironmentGuards, TestCase):
    """Command construction only; never starts Docker or reenables legacy SSH."""
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="docker-contract@example.test", password=PASSWORD)
        cls.tenant = cls.row.tenant
        cls.agent = Agent.objects.create(tenant=cls.tenant, project=cls.row.project,
            name="Browser contract", created_by=cls.row.owner)

    def _register_image(self):
        return AgentRuntimeImage.objects.create(tenant=self.tenant, project=self.row.project,
            agent=self.agent, created_by=self.row.owner, image_ref="fixture.invalid/browser:latest")


class PersonalEdgeJWTTests(EdgeJWTGuards, TestCase):
    @classmethod
    def setUpTestData(cls):
        provision_owner(email="protocol-owner@example.test", password=PASSWORD)
