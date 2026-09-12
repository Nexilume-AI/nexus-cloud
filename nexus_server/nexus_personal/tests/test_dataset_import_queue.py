"""Real personal owner, storage and HTTP/worker paths; no admission bypass."""
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.agents.models import Agent, AgentDisplayRun, AgentDisplayEvent
from apps.common.subjects import request_subject
from apps.datasets.import_jobs import enqueue
from apps.datasets.models import Dataset
from nexus_personal.services import provision_owner
from tests.dataset_import_queue_guards import DatasetImportQueueGuards
from .test_installation import PASSWORD


class PersonalDatasetImportQueueTests(DatasetImportQueueGuards, TestCase):
    def monitoring_operations(self):
        from apps.metrics.models import MetricSnapshot
        from nexus_personal import monitoring_workers

        def capture_metric_snapshot(*, tenant):
            self.assertEqual(tenant.pk, self.tenant.pk)
            before = set(MetricSnapshot.objects.values_list("pk", flat=True))
            self.assertEqual(monitoring_workers.collect_snapshots(), 1)
            # Return exactly the newly persisted snapshot, never a stale row
            # or a synthetic success when the collector rejected its lease.
            return MetricSnapshot.objects.exclude(pk__in=before).get(tenant=tenant)

        return monitoring_workers.evaluate_rules, capture_metric_snapshot

    def import_payload(self, response):
        return response.json()

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = override_settings(
            NEXUS_DATASET_STORAGE_BACKEND="local",
            NEXUS_DATASET_STORAGE_ROOT=self.temp.name,
            # Match the real production disk guard; the minimal identity test
            # host does not configure export spooling. Never disable this guard.
            NEXUS_DATASET_SPOOL_MIN_FREE_BYTES=512 * 1024**2,
        )
        settings.enable()
        self.addCleanup(settings.disable)
        row = provision_owner(email="owner@example.test", password=PASSWORD)
        self.user, self.tenant = row.owner, row.tenant
        self.request = SimpleNamespace(user=row.owner, tenant_id=str(row.tenant.pk),
            project_id=str(row.project.pk), headers={}, query_params={}, META={}, method="POST")
        subject = request_subject(self.request)
        self.dataset = Dataset.objects.create(tenant=row.tenant, project=row.project,
            name="Imports", created_by=row.owner)
        self.agent = Agent.objects.create(tenant=row.tenant, project=row.project,
            name="Importer", created_by=row.owner)
        self.run = AgentDisplayRun.objects.create(tenant=row.tenant, consumer_project=row.project,
            consumer_tenant=row.tenant, agent=self.agent,
            caller_subject_hash=subject.subject_hash, caller_principal_type=subject.principal_type,
            caller_principal_id=subject.principal_id, status="completed", redaction_status="passed")
        AgentDisplayEvent.objects.create(tenant=row.tenant,
            agent=self.agent, run=self.run, seq=1, event_type="STEP_STARTED",
            payload_json={"stepName": "safe"}, redacted_payload_json={"stepName": "safe"})
        self.inputs = {"agent_id": str(self.agent.pk), "run_id": str(self.run.pk)}
        self.client = APIClient()
        self.token = Token.objects.create(user=row.owner)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token.key)
        self.url = f"/api/v1/datasets/{self.dataset.pk}/imports/"
        self.headers = {"HTTP_X_NEXUS_TENANT": str(row.tenant.pk),
                        "HTTP_X_NEXUS_PROJECT": str(row.project.pk)}

    def new_job(self, key="first"):
        return enqueue(self.request, self.dataset.pk, "trace", self.inputs, key)
