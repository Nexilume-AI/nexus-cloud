"""Real deployment preparation and local limits, not a fake Docker runner."""
from decimal import Decimal
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from rest_framework import exceptions
from apps.agents.docker_lifecycle import prepare_state
from apps.agents.models import Agent, AgentResourceConfig, AgentRuntimeDeployment
from apps.common.resource_limits import capability_state, enforce_capability
from apps.tenancy.models import Tenant
from nexus_personal.resource_limits import PersonalCapacityExceeded
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalDockerAdmissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email='docker-admission@example.test', password=PASSWORD)
        cls.agent = Agent.objects.create(tenant=cls.row.tenant, project=cls.row.project,
            created_by=cls.row.owner, name='Local container')

    def prepare(self, cpu='1.5', memory='768m'):
        AgentResourceConfig.objects.update_or_create(agent=self.agent, defaults={'cpu': cpu, 'memory': memory})
        # Reload to avoid Django's related-object cache hiding configuration changes.
        return prepare_state(agent=Agent.objects.get(pk=self.agent.pk), env='production', image=None)

    def test_actual_preparation_retains_normalized_container_limits(self):
        state = self.prepare()
        self.assertEqual(Decimal(state['resources']['cpu']), Decimal('1.5'))
        self.assertEqual(state['resources']['memory_bytes'], 768 * 1024**2)
        self.assertEqual(state['desired'], 'running')
        self.assertNotEqual(state['generation'], self.prepare()['generation'])
        self.assertFalse(AgentRuntimeDeployment.objects.exists())

    def test_cpu_and_memory_excess_fail_before_runtime_creation(self):
        for cpu, memory, code in [('2.01', '512m', 'agents.hosted_cpu'), ('1', '2049m', 'agents.hosted_memory_mb')]:
            with self.subTest(code=code), self.assertRaises(PersonalCapacityExceeded) as error:
                self.prepare(cpu, memory)
            self.assertEqual(error.exception.detail['resource'], code)
        self.assertFalse(AgentRuntimeDeployment.objects.exists())

    def test_exact_boundary_is_a_per_container_ceiling(self):
        self.prepare('2', '2048m')
        for code in ('agents.hosted_cpu', 'agents.hosted_memory_mb'):
            state = capability_state(tenant=self.row.tenant, code=code)
            self.assertEqual(state['used'], Decimal(0))
            enforce_capability(tenant=self.row.tenant, code=code, requested=state['limit'])
            enforce_capability(tenant=self.row.tenant, code=code, requested=state['limit'])

    def test_missing_or_invalid_limits_fail_closed(self):
        for limits in ({}, {'agents.hosted_cpu': None}, {'agents.hosted_cpu': 'Infinity'}, {'agents.hosted_cpu': -1}):
            with self.subTest(limits=limits), override_settings(NEXUS_PERSONAL_AGENT_LIMITS=limits):
                with self.assertRaises(ImproperlyConfigured):
                    self.prepare()
        with override_settings(NEXUS_PERSONAL_AGENT_LIMITS={'agents.hosted_cpu': 0}):
            with self.assertRaises(PersonalCapacityExceeded):
                self.prepare()

    def test_foreign_context_and_inexact_requests_are_rejected(self):
        other = Tenant.objects.create(name='Other')
        with self.assertRaises(exceptions.NotFound):
            enforce_capability(tenant=other, code='agents.hosted_cpu')
        for value in (True, 1.2, '-1', 'NaN'):
            with self.subTest(value=value), self.assertRaises(exceptions.ValidationError):
                enforce_capability(tenant=self.row.tenant, code='agents.hosted_cpu', requested=value)
