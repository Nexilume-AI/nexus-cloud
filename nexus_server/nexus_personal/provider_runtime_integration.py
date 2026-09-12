"""Operational personal runtimes have no marketplace relations or cleanup."""
from django.db.models import Count, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Coalesce
from rest_framework import exceptions
from .resource_catalog import PersonalResourceCatalog
from .services import installation_context


class PersonalProviderRuntimeIntegration:
    def validate_import_existing(self, *, account, runtime):
        owner_id, tenant_id, project_id = installation_context()
        if (str(account.tenant_id) != tenant_id
                or (account.created_by_id is not None and str(account.created_by_id) != owner_id)
                or (runtime is not None and (str(runtime.tenant_id) != tenant_id
                    or str(runtime.project_id) != project_id
                    or (runtime.owner_id is not None and str(runtime.owner_id) != owner_id)))):
            raise exceptions.ValidationError("Identity unavailable in this scope; choose another api_ref.")

    def import_has_publication_dependencies(self, *, runtime):
        # No publication relation exists in the Personal Provider schema.
        # Shared validation still rejects active runtimes and local Sources.
        return False

    def import_failure_message(self, *, error):
        return "Configuration changed or is unavailable. Check the connection identity, local capacity and Source dependencies, then retry."

    def maintenance_filter(self):
        owner_id, tenant_id, project_id = installation_context()
        return Q(tenant_id=tenant_id) & (Q(owner_id=owner_id) | Q(owner__isnull=True)) & (
            Q(project_id=project_id) | Q(project__isnull=True))

    def maintenance_summary(self):
        # No accounting tables or fabricated financial statistics in Personal.
        return {}

    def connection_removal_metadata(self, *, impact):
        return {"model_count": impact["model_count"], "source_count": impact["source_count"]}

    def connection_runtime_queryset(self, *, summary=False):
        owner_id, tenant_id, project_id = installation_context()
        return self._connection_runtime_queryset(owner_id, tenant_id, project_id, summary=summary)

    def _connection_runtime_queryset(self, owner_id, tenant_id, project_id, *, summary=False):
        # Internal builder: callers must obtain these IDs from current owner
        # validation, never from request parameters or a prefetched account.
        from apps.deployments.models import Deployment
        from apps.providers.models import ProviderRuntimeAccount, ProviderRuntimeModelOffer
        sources = Deployment.objects.filter(tenant_id=tenant_id).exclude(status="deleted").filter(
            Q(created_by_id=owner_id) | Q(created_by__isnull=True)).filter(
            Q(project_id=project_id) | Q(project__isnull=True))
        runtimes = ProviderRuntimeAccount.objects.exclude(status="deleted").select_related("project")
        offers = ProviderRuntimeModelOffer.objects.exclude(status="deleted")
        if summary:
            def count_rows(queryset, group):
                return Coalesce(Subquery(queryset.order_by().values(group).annotate(n=Count("pk")).values("n")[:1]), 0)
            return runtimes.annotate(
                catalog_model_count=count_rows(offers.filter(runtime_account_id=OuterRef("pk")), "runtime_account_id"),
                catalog_source_count=count_rows(sources.filter(provider_runtime_id=OuterRef("pk")), "provider_runtime_id"))
        offers = offers.select_related("canonical_model").prefetch_related(
            Prefetch("sources", queryset=sources, to_attr="prefetched_sources")).order_by("upstream_model_id")
        return runtimes.prefetch_related(
            Prefetch("model_offers", queryset=offers, to_attr="prefetched_model_offers"),
            Prefetch("sources", queryset=sources, to_attr="prefetched_sources"))

    def connection_deletion_impact(self, *, request, account_id):
        from apps.providers.connection_services import get_provider_connection, connection_runtime
        account = get_provider_connection(request=request, account_id=account_id)
        runtime = connection_runtime(account)
        status = runtime.status if runtime is not None else "missing"
        model_count = runtime.model_offers.exclude(status="deleted").count() if runtime else 0
        source_count = len(runtime.prefetched_sources) if runtime and hasattr(runtime, "prefetched_sources") else 0
        return {"provider_id": str(account.pk), "provider_name": account.name or account.account_id,
            "runtime_status": status, "model_count": model_count, "source_count": source_count,
            "requires_name_confirmation": bool(source_count or status not in {"missing", "created", "stopped", "failed"}),
            "preserved_history": ["Audit log"]}

    def runtime_prefetches(self):
        return ()

    def runtime_filter(self, *, request, tenant):
        user, tenant_id, project_id = PersonalResourceCatalog()._context(request, tenant)
        return Q(tenant_id=tenant_id) & (Q(owner_id=user.pk) | Q(owner__isnull=True)) & (
            Q(project_id=project_id) | Q(project__isnull=True))

    def offer_has_contributions(self, *, offer):
        # The personal schema cannot contain published pool contributions.
        # Existing local Sources still lock canonical identity in shared code.
        return False

    def remove_contributions(self, *, runtime, now):
        # Nothing commercial exists to revoke. Shared Source/link cleanup and
        # the original stop-before-delete transaction remain mandatory.
        return None

    def validate_source_account(self, *, account, tenant):
        owner, tenant_id, _ = installation_context()
        if (str(getattr(tenant, "pk", tenant)) != tenant_id or str(account.tenant_id) != tenant_id
                or (account.created_by_id is not None and str(account.created_by_id) != owner)):
            raise exceptions.NotFound("Source provider account not found.")
