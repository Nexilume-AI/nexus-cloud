from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.tenancy.models import Membership, Tenant
from apps.workspaces.models import WorkspaceConnection


class WorkspaceLegacyClaimRegressionTests(TestCase):
    """Regression: ISSUE-001 — caller-owned legacy Computers disappeared after ownership isolation."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="legacy-owner", password="password")
        self.other = get_user_model().objects.create_user(username="other-owner", password="password")
        self.tenant = Tenant.objects.create(name="Legacy Computer Tenant", slug="legacy-computer-tenant")
        Membership.objects.create(tenant=self.tenant, user=self.user, role=Membership.ROLE_OWNER)
        Membership.objects.create(tenant=self.tenant, user=self.other, role=Membership.ROLE_OWNER)

    def test_list_claims_only_connections_created_by_current_user(self):
        own = WorkspaceConnection.objects.create(
            tenant=self.tenant,
            name="My Windows PC",
            ssh_host="127.0.0.1",
            ssh_user="caller",
            created_by=self.user,
        )
        other = WorkspaceConnection.objects.create(
            tenant=self.tenant,
            name="Other Windows PC",
            ssh_host="127.0.0.1",
            ssh_user="other",
            created_by=self.other,
        )
        client = APIClient()
        client.force_authenticate(self.user)

        response = client.get("/api/v1/workspace-connections/", HTTP_X_NEXUS_TENANT=str(self.tenant.id))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([item["id"] for item in response.json()["data"]], [str(own.id)])
        own.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(own.owner_subject_hash)
        self.assertFalse(other.owner_subject_hash)
