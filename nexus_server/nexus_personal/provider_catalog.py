"""Fixed-owner Provider discovery; runtime-derived ownership is never guessed."""
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Count, OuterRef, Q, Subquery
from rest_framework import exceptions

from apps.common.resource_facts import requested_view_scope
from .resource_catalog import PersonalResourceCatalog


class PersonalProviderCatalog:
    def _visible(self, *, request, tenant):
        user, tenant_id, project_id = PersonalResourceCatalog()._context(request, tenant)
        requested_view_scope(request)
        # Require the real product models. Do not substitute stubs or interpret
        # a missing Provider schema as a successful empty catalog.
        try:
            account = apps.get_model("providers", "ProviderAccount")
            runtime = apps.get_model("providers", "ProviderRuntimeAccount")
        except LookupError:
            raise ImproperlyConfigured("Personal Provider schema is not installed.") from None
        runtimes = runtime.objects.filter(source_provider_account_id=OuterRef("pk")).exclude(status="deleted")
        visible = account.objects.filter(tenant_id=tenant_id).exclude(status="deleted").filter(
            Q(created_by_id=user.pk) | Q(created_by__isnull=True))
        # Re-read actual rows, not supplied catalog annotations or stale prefetched
        # objects. A missing/ambiguous runtime must never imply shared ownership.
        visible = visible.annotate(
            personal_runtime_count=Count("source_runtime_accounts", filter=~Q(source_runtime_accounts__status="deleted"), distinct=True),
            personal_project_id=Subquery(runtimes.values("project_id")[:1]),
            personal_runtime_tenant_id=Subquery(runtimes.values("tenant_id")[:1]),
            personal_runtime_owner_id=Subquery(runtimes.values("owner_id")[:1]),
        ).filter(personal_runtime_count=1, personal_runtime_tenant_id=tenant_id).filter(
            Q(personal_project_id=project_id) | Q(personal_project_id__isnull=True)).filter(
            Q(personal_runtime_owner_id=user.pk) | Q(personal_runtime_owner_id__isnull=True))
        return account, visible

    def filter_queryset(self, *, queryset, request, tenant):
        account, visible = self._visible(request=request, tenant=tenant)
        if queryset.model is not account:
            raise exceptions.ValidationError({"resource_type": "Provider catalog model mismatch."})
        return queryset.filter(pk__in=visible.values("pk"))

    def filter_rows(self, *, accounts, request, tenant):
        account, visible = self._visible(request=request, tenant=tenant)
        rows = list(accounts)
        if any(not isinstance(row, account) for row in rows):
            raise exceptions.ValidationError({"resource_type": "Provider catalog model mismatch."})
        ids = set(visible.filter(pk__in=[row.pk for row in rows]).values_list("pk", flat=True))
        return [row for row in rows if row.pk in ids]
