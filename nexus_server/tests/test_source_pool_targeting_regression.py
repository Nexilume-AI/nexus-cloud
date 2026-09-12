from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.common.crypto import encrypt_secret
from apps.common.models import SoftDeleteModel
from apps.deployments.models import CanonicalModel, Deployment, ModelGroup, ModelGroupDeployment
from apps.providers.models import Provider, ProviderAccount, ProviderRuntimeAccount, ProviderRuntimeModelOffer
from apps.tenancy.models import Membership, Project, Tenant


class SourcePoolTargetingRegressionTests(TestCase):
    """Regression: only Provider Runtime Sources may be created and must honor model_group_id."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="source-pool-owner",
            email="source-pool-owner@example.com",
            password="password",
        )
        self.tenant = Tenant.objects.create(name="Source Pool Tenant", slug="source-pool-targeting")
        Membership.objects.create(tenant=self.tenant, user=self.user, role=Membership.ROLE_OWNER)
        self.project = Project.objects.create(tenant=self.tenant, name="Primary Project")
        self.other_project = Project.objects.create(tenant=self.tenant, name="Other Project")
        self.provider, _ = Provider.objects.get_or_create(name="openai", defaults={"display_name": "OpenAI"})
        self.account = ProviderAccount.objects.create(
            tenant=self.tenant,
            provider=self.provider,
            account_id="source-pool-account",
            url="https://api.example.com/v1",
            encrypted_key=encrypt_secret("test-secret"),
            status=SoftDeleteModel.STATUS_ACTIVE,
            created_by=self.user,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.gpt = CanonicalModel.objects.create(key="gpt-4o", display_name="GPT-4o")
        self.claude = CanonicalModel.objects.create(key="claude-3-7-sonnet", display_name="Claude 3.7 Sonnet")

    def post_source(self, payload: dict, *, project: Project | None = None):
        headers = {"HTTP_X_NEXUS_TENANT": str(self.tenant.id)}
        if project is not None:
            headers["HTTP_X_NEXUS_PROJECT"] = str(project.id)
        return self.client.post("/api/v1/deployments/", payload, format="json", **headers)

    def test_manual_source_creation_is_disabled_even_with_compatible_pool(self) -> None:
        pool = ModelGroup.objects.create(
            tenant=self.tenant,
            project=self.project,
            canonical_model=self.gpt,
            name="gpt-pool",
            display_name="GPT Pool",
            created_by=self.user,
        )

        response = self.post_source(
            {
                "source_type": "manual",
                "deployment_id": "manual-gpt-source",
                "provider": "openai",
                "canonical_model_id": str(self.gpt.id),
                "upstream_model_id": "gpt-4o",
                "endpoint": "https://manual.example.com/v1",
                "model_group_id": str(pool.id),
            },
            project=self.project,
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["error"]["code"], "MANUAL_SOURCE_CREATION_DISABLED")
        self.assertFalse(Deployment.objects.filter(deployment_id="manual-gpt-source").exists())

    def test_provider_runtime_source_joins_selected_compatible_pool(self) -> None:
        pool = ModelGroup.objects.create(
            tenant=self.tenant,
            project=self.project,
            canonical_model=self.gpt,
            name="runtime-gpt-pool",
            created_by=self.user,
        )
        runtime = ProviderRuntimeAccount.objects.create(
            tenant=self.tenant,
            project=self.project,
            owner=self.user,
            source_provider_account=self.account,
            name="Active GPT Runtime",
            runtime_type=ProviderRuntimeAccount.RUNTIME_DIRECT_API,
            status=ProviderRuntimeAccount.STATUS_ACTIVE,
            internal_api_url="https://api.example.com/v1",
        )
        offer = ProviderRuntimeModelOffer.objects.create(
            runtime_account=runtime,
            canonical_model=self.gpt,
            upstream_model_id="gpt-4o",
            status=ProviderRuntimeModelOffer.STATUS_CONFIRMED,
            health_status=ProviderRuntimeModelOffer.HEALTH_HEALTHY,
            confirmed_by=self.user,
        )

        response = self.post_source(
            {
                "source_type": "provider_runtime",
                "provider_runtime_id": str(runtime.id),
                "model_offer_id": str(offer.id),
                "model_group_id": str(pool.id),
            },
            project=self.project,
        )

        self.assertEqual(response.status_code, 201, response.content)
        runtime.refresh_from_db()
        source = runtime.sources.get(runtime_model_offer=offer)
        self.assertTrue(ModelGroupDeployment.objects.filter(model_group=pool, deployment=source).exists())
        self.assertFalse(ModelGroup.objects.filter(tenant=self.tenant, name="ignored-default-pool").exists())

    def test_manual_source_is_disabled_before_pool_targeting(self) -> None:
        other_project_pool = ModelGroup.objects.create(
            tenant=self.tenant,
            project=self.other_project,
            canonical_model=self.gpt,
            name="other-project-pool",
            created_by=self.user,
        )
        deleted_pool = ModelGroup.objects.create(
            tenant=self.tenant,
            project=self.project,
            canonical_model=self.gpt,
            name="deleted-pool",
            status=SoftDeleteModel.STATUS_DELETED,
            created_by=self.user,
        )
        incompatible_pool = ModelGroup.objects.create(
            tenant=self.tenant,
            project=self.project,
            canonical_model=self.claude,
            name="claude-pool",
            created_by=self.user,
        )
        claude_source = Deployment.objects.create(
            tenant=self.tenant,
            project=self.project,
            provider=self.provider,
            deployment_id="existing-claude-source",
            canonical_model=self.claude,
            upstream_model_id="claude-3-7-sonnet",
            created_by=self.user,
        )
        ModelGroupDeployment.objects.create(model_group=incompatible_pool, deployment=claude_source)

        for index, pool in enumerate((other_project_pool, deleted_pool, incompatible_pool), start=1):
            response = self.post_source(
                {
                    "source_type": "manual",
                    "deployment_id": f"rejected-gpt-source-{index}",
                    "provider": "openai",
                    "canonical_model_id": str(self.gpt.id),
                    "upstream_model_id": "gpt-4o",
                    "endpoint": "https://manual.example.com/v1",
                    "model_group_id": str(pool.id),
                },
                project=self.project,
            )
            self.assertEqual(response.status_code, 400, response.content)
            self.assertEqual(response.json()["error"]["code"], "MANUAL_SOURCE_CREATION_DISABLED")

        self.assertFalse(Deployment.objects.filter(deployment_id__startswith="rejected-gpt-source").exists())

    def test_legacy_manual_model_group_name_path_is_disabled(self) -> None:
        mini = CanonicalModel.objects.create(key="gpt-4o-mini", display_name="GPT-4o mini")
        response = self.post_source(
            {
                "source_type": "manual",
                "deployment_id": "legacy-name-source",
                "provider": "openai",
                "canonical_model_id": str(mini.id),
                "upstream_model_id": "gpt-4o-mini",
                "endpoint": "https://manual.example.com/v1",
                "model_group_name": "legacy-name-pool",
            },
            project=self.project,
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["error"]["code"], "MANUAL_SOURCE_CREATION_DISABLED")
        self.assertFalse(ModelGroup.objects.filter(tenant=self.tenant, name="legacy-name-pool").exists())
