from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import exceptions

from apps.datasets.policy import OPERATIONS, invoke_dataset_policy
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class PersonalDatasetPolicyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="owner@example.test", password=PASSWORD)

    def request(self):
        return SimpleNamespace(user=self.row.owner, META={}, query_params={})

    def test_only_personal_owner_can_create_collections(self):
        self.assertIsNone(invoke_dataset_policy("require_dataset_admin", request=self.request(), tenant=self.row.tenant))
        request = self.request()
        request.user = get_user_model().objects.create_user(username="not-owner")
        with self.assertRaises(exceptions.AuthenticationFailed):
            invoke_dataset_policy("require_dataset_admin", request=request, tenant=self.row.tenant)

    def test_commercial_operations_are_explicitly_unavailable_not_fake_success(self):
        personal = {"visible_datasets", "can_read_dataset", "can_manage_dataset", "require_dataset_admin", "set_visibility", "dataset_related_fields", "dataset_retention_status",
                    "ensure_operational_alerts", "maintain_data_assets"}
        for name in set(OPERATIONS) - personal:
            with self.subTest(operation=name), self.assertRaises(exceptions.NotFound):
                invoke_dataset_policy(name)

    def test_operational_defaults_use_real_instance_state_not_commercial_fallback(self):
        from apps.metrics.models import AlertRule
        self.assertFalse(AlertRule.objects.exists())
        invoke_dataset_policy("ensure_operational_alerts", tenant=self.row.tenant)
        self.assertEqual(AlertRule.objects.filter(tenant=self.row.tenant, project=self.row.project,
            created_by=self.row.owner, metric__startswith="data_assets.").count(), 5)
        self.assertEqual(invoke_dataset_policy("maintain_data_assets"), {"cleaned": 0})
        self.assertEqual(AlertRule.objects.count(), 5)

    def test_public_visibility_is_rejected_before_reading_or_mutating_resource(self):
        with self.assertRaises(exceptions.NotFound):
            invoke_dataset_policy("set_visibility", request=self.request(), dataset_id="fixture", visibility="public")

    def test_anonymous_metadata_access_is_denied_without_loading_resource_apps(self):
        obj = SimpleNamespace(tenant_id=self.row.tenant_id)
        self.assertFalse(invoke_dataset_policy("can_read_dataset", request=None, dataset=obj))
