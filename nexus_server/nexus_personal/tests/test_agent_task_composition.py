"""Real operational registry/presence; no simulated deployments in Personal."""
from datetime import timedelta
from types import SimpleNamespace
from django.test import TestCase
from django.utils import timezone
from rest_framework import exceptions
from apps.agents import services, tasks
from apps.agents.models import AgentDeployment, AgentLog, EdgeNode
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalAgentTaskCompositionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email='task-owner@example.test', password=PASSWORD)

    def test_legacy_dispatch_fails_before_creating_deployment_or_log(self):
        request = SimpleNamespace(user=self.row.owner)
        with self.assertRaises(exceptions.NotFound):
            services.deploy_agent(request=request, agent_id='unavailable', env='production')
        self.assertFalse(AgentDeployment.objects.exists())
        self.assertFalse(AgentLog.objects.exists())
        self.assertFalse(hasattr(tasks, 'simulate_agent_deployment'))
        self.assertFalse(hasattr(tasks, 'release_stale_agent_billing_reservations'))

    def test_actual_presence_task_materializes_expiry_and_is_idempotent(self):
        node = EdgeNode.objects.create(tenant=self.row.tenant, project=self.row.project,
            registered_by=self.row.owner, router_id='personal-presence', domain_id='fixture.invalid',
            device_token_hash='c' * 64, presence_protocol_version=1,
            last_presence_at=timezone.now() - timedelta(minutes=5),
            presence_expires_at=timezone.now() - timedelta(minutes=1), connection_status='online')
        self.assertEqual(tasks.expire_edge_node_presence_task(), 1)
        node.refresh_from_db()
        self.assertEqual(node.connection_status, EdgeNode.CONNECTION_OFFLINE)
        self.assertEqual(node.connection_status_reason, EdgeNode.PRESENCE_REASON_EXPIRED)
        self.assertEqual(tasks.expire_edge_node_presence_task(), 0)
