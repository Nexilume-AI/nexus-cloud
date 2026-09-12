"""Real default rules belong to the instance and preserve deliberate changes."""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient
from apps.datasets.models import Dataset, DatasetTransfer
from apps.datasets.operations import ensure_operational_alerts, maintain_data_assets
from apps.metrics.models import AlertRule
from apps.tenancy.models import Tenant, Project
from nexus_personal.services import provision_owner
from nexus_personal.monitoring_facts import resolve_rule


@override_settings(ROOT_URLCONF="nexus_personal.urls")
class PersonalMonitoringDefaultsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        instance = provision_owner(email="defaults-owner@example.test", password="fixture-defaults-owner-62391!")
        cls.owner, cls.tenant, cls.project = instance.owner, instance.tenant, instance.project
        cls.foreign = Tenant.objects.create(name="Foreign")
        cls.foreign_project = Project.objects.create(tenant=cls.foreign, name="Foreign")
        cls.other_project = Project.objects.create(tenant=cls.tenant, name="Other project")
        cls.other = get_user_model().objects.create_user(username="defaults-other")

    def test_defaults_are_visible_evaluable_and_repeated_bootstrap_does_not_duplicate(self):
        ensure_operational_alerts(self.tenant)
        ensure_operational_alerts(self.tenant)
        rules = list(AlertRule.objects.all())
        self.assertEqual(len(rules), 5)
        for rule in rules:
            self.assertEqual(rule.project_id, self.project.pk)
            self.assertEqual(rule.created_by_id, self.owner.pk)
            self.assertIsInstance(resolve_rule(rule), Decimal)
        client = APIClient()
        client.force_login(self.owner)
        response = client.get("/api/v1/alerts/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual({str(row.pk) for row in rules}, {row["id"] for row in response.data})

    def test_disabled_deleted_muted_and_customized_rules_are_not_recreated(self):
        ensure_operational_alerts(self.tenant)
        rules = list(AlertRule.objects.order_by("metric"))
        states = ["disabled", "deleted", "active", "active", "active"]
        until = timezone.now()+timedelta(days=2)
        for rule, state in zip(rules, states):
            AlertRule.objects.filter(pk=rule.pk).update(status=state, threshold="42", threshold_value=42,
                operator="gt", muted_until=until)
        before = list(AlertRule.objects.order_by("pk").values())
        ensure_operational_alerts(self.tenant)
        self.assertEqual(list(AlertRule.objects.order_by("pk").values()), before)

    def test_no_adoption_of_foreign_owner_project_or_organization_rule(self):
        for attrs in ({"tenant": self.tenant, "project": None},
                      {"tenant": self.tenant, "project": self.other_project},
                      {"tenant": self.tenant, "project": self.project, "created_by": self.other}):
            AlertRule.objects.create(**attrs, metric="data_assets.stalled", resource_type="system",
                threshold="99", threshold_value=99, status="disabled")
        before = list(AlertRule.objects.order_by("pk").values())
        with self.assertRaises(PermissionDenied):
            ensure_operational_alerts(self.foreign)
        ensure_operational_alerts(self.tenant)
        self.assertEqual(AlertRule.objects.count(), 8)
        self.assertEqual(list(AlertRule.objects.filter(pk__in=[row["id"] for row in before]).order_by("pk").values()), before)

    def test_maintenance_cleans_only_expired_instance_transfers_and_creates_own_rules(self):
        datasets = [
            Dataset.objects.create(tenant=self.tenant, project=self.project, name="Own", created_by=self.owner),
            Dataset.objects.create(tenant=self.foreign, project=self.foreign_project, name="Foreign"),
            Dataset.objects.create(tenant=self.tenant, project=self.other_project, name="Other project"),
            Dataset.objects.create(tenant=self.tenant, project=self.project, name="Other owner", created_by=self.other),
        ]
        rows = [DatasetTransfer.objects.create(tenant=dataset.tenant, dataset=dataset, kind="write",
            identity=str(uuid4()), storage_backend="local", object_key=f"{uuid4().hex}.txt",
            size_bytes=4, expires_at=timezone.now()-timedelta(minutes=1)) for dataset in datasets]
        with TemporaryDirectory() as directory, override_settings(NEXUS_DATASET_STORAGE_ROOT=directory):
            for row in rows:
                Path(directory, row.object_key).write_bytes(b"test")
            self.assertEqual(maintain_data_assets(), {"cleaned": 1})
            self.assertEqual(maintain_data_assets(), {"cleaned": 0})
            self.assertFalse(Path(directory, rows[0].object_key).exists())
            for row in rows[1:]:
                self.assertEqual(Path(directory, row.object_key).read_bytes(), b"test")
        rows[0].refresh_from_db()
        self.assertNotEqual(rows[0].state, "active")
        for row in rows[1:]:
            row.refresh_from_db()
            self.assertEqual(row.state, "active")
        self.assertEqual(AlertRule.objects.count(), 5)
        self.assertFalse(AlertRule.objects.exclude(tenant=self.tenant, project=self.project).exists())
