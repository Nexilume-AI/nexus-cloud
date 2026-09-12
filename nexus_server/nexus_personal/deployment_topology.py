"""Owner-only topology over real local records, with no commerce schema."""
from django.db.models import Q
from apps.providers.models import ProviderRuntimeAccount
from .provider_runtime_integration import PersonalProviderRuntimeIntegration
from .resource_catalog import PersonalResourceCatalog


class PersonalDeploymentTopology:
    def sources(self, queryset):
        return queryset

    def owned_runtimes(self, *, request, tenant):
        user, tenant_id, _ = PersonalResourceCatalog()._context(request, tenant)
        rows = ProviderRuntimeAccount.objects.filter(
            PersonalProviderRuntimeIntegration().runtime_filter(request=request, tenant=tenant)
        ).exclude(status="deleted")
        # Runtime ownership alone cannot make someone else's stored credential
        # or context visible. Re-read both possible credential relationships.
        for field in ("source_provider_account", "provider_account"):
            rows = rows.filter(Q(**{field + "__isnull": True}) | (
                Q(**{field + "__tenant_id": tenant_id}) & (
                    Q(**{field + "__created_by_id": user.pk}) | Q(**{field + "__created_by__isnull": True}))))
        return rows.select_related("tenant", "project").prefetch_related("model_offers__canonical_model")

    def additional_runtimes(self, *, sources, visible_source_ids):
        return ()

    def runtime_context(self, runtime):
        return None

    def offer_fields(self, offer, context):
        return {}

    def source_runtime(self, source):
        return source.provider_runtime, source.runtime_model_offer

    def source_fields(self, source):
        return {}

    def source_links(self, queryset, *, visible_pool_ids):
        return queryset.filter(model_group_id__in=visible_pool_ids or ())

    def pool_links(self, queryset):
        return queryset

    def metric_aggregations(self):
        return {}

    def metric_fields(self, metrics):
        # Do not invent a zero settlement total when no ledger is installed.
        return {}
