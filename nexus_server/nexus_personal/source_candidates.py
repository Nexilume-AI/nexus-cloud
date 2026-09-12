"""Owner-scoped, live Source eligibility without Marketplace or Gateway imports."""
from django.contrib.auth import get_user_model
from apps.deployments.models import ModelGroupDeployment
from apps.providers.quota_services import provider_account_quota_available
from .deployment_integration import PersonalDeploymentIntegration
from .services import installation_context


class PersonalSourceCandidates:
    def _scope(self, tenant):
        owner_id, _, _ = installation_context()
        owner = get_user_model().objects.get(pk=owner_id)
        policy = PersonalDeploymentIntegration()
        # Revalidates installation ownership and tenant even for empty catalogs.
        visible = policy.visible_deployments(user=owner, tenant=tenant)
        return owner, policy, visible

    def model_group_links(self, *, tenant, group, provider_name=""):
        owner, policy, visible = self._scope(tenant)
        current = policy.visible_model_groups(user=owner, tenant=tenant).filter(pk=group.pk).first()
        if current is None:
            return ModelGroupDeployment.objects.none()
        links = ModelGroupDeployment.objects.filter(model_group=current, status="active", enabled=True,
            deployment__in=visible, deployment__status="active", deployment__project_id=current.project_id,
            deployment__canonical_model_id=current.canonical_model_id, deployment__provider_account__status="active"
        ).select_related("deployment", "deployment__provider", "deployment__provider_account",
                         "deployment__provider_runtime", "deployment__runtime_model_offer").order_by("priority", "fallback_order", "created_at")
        if provider_name and provider_name != "*":
            links = links.filter(deployment__provider__name=provider_name)
        return links

    def resolve_model_source_deployment(self, *, tenant, source):
        owner, _, visible = self._scope(tenant)
        current = visible.filter(pk=source.pk, status="active", provider_account__status="active").first()
        if current is None or current.health_status == "unhealthy" or not provider_account_quota_available(current.provider_account):
            return None
        runtime, offer = current.provider_runtime, current.runtime_model_offer
        if runtime is None or runtime.status != "active" or offer is None or offer.status != "confirmed":
            return None
        if (offer.runtime_account_id != runtime.pk or offer.canonical_model_id != current.canonical_model_id or
                offer.upstream_model_id != current.upstream_model_id or offer.health_status not in {"healthy", "degraded"}):
            return None
        for account in (runtime.provider_account, runtime.source_provider_account):
            if account is not None and (account.tenant_id != current.tenant_id or account.status != "active" or
                    account.created_by_id not in (None, owner.pk)):
                return None
        if runtime.provider_account_id != current.provider_account_id:
            return None
        return current
