"""Only this installation's owner's jobs; no role or membership grants."""
from types import SimpleNamespace
from apps.jobs.models import Job
from .resource_catalog import PersonalResourceCatalog


class PersonalJobAccess:
    def require_read(self, *, user, tenant):
        PersonalResourceCatalog()._context(SimpleNamespace(user=user, META={}, query_params={}), tenant)

    def scope(self, queryset, request):
        from django.core.exceptions import ImproperlyConfigured
        if queryset.model is not Job:
            raise ImproperlyConfigured('Personal job scope only accepts Jobs.')
        from .authentication import validate_owner
        owner, tenant_id, project_id = validate_owner(request, request.user)
        return queryset.filter(tenant_id=tenant_id, project_id=project_id, created_by_id=owner.pk)
