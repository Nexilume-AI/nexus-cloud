"""Distribution-owned catalog policy; absence never grants resource access."""
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from rest_framework import serializers

from .resource_facts import (ResourceOwnershipInputSerializer, ownership_payload,
    provider_connection_project, project_for_resource, requested_view_scope, resolve_catalog_resource)


@lru_cache(maxsize=16)
def _backend(path):
    try:
        backend = import_string(path)()
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ImproperlyConfigured("Nexus resource catalog backend is not configured correctly.") from None
    if not all(callable(getattr(backend, name, None)) for name in (
        "resolve_ownership_project", "resource_access_payload", "resource_context_payload",
        "discoverable_resource_queryset", "prepare_catalog_rows",
    )):
        raise ImproperlyConfigured("Nexus resource catalog backend is incomplete.")
    return backend


def catalog_backend():
    return _backend(getattr(settings, "NEXUS_RESOURCE_CATALOG_BACKEND", ""))


class ResourceCatalogListSerializer(serializers.ListSerializer):
    def to_representation(self, data):
        rows = catalog_backend().prepare_catalog_rows(data=data, context=self.context, child=self.child)
        return super().to_representation(rows)


def resolve_ownership_project(*, request, tenant, ownership):
    return catalog_backend().resolve_ownership_project(request=request, tenant=tenant, ownership=ownership)


def resource_access_payload(*, request, resource_type, obj, project=None):
    return catalog_backend().resource_access_payload(request=request, resource_type=resource_type, obj=obj, project=project)


def resource_context_payload(*, request, resource_type, obj):
    return catalog_backend().resource_context_payload(request=request, resource_type=resource_type, obj=obj)


def discoverable_resource_queryset(queryset, *, request, tenant, resource_type,
                                   project_field="project", creator_field="created_by"):
    return catalog_backend().discoverable_resource_queryset(queryset, request=request, tenant=tenant,
        resource_type=resource_type, project_field=project_field, creator_field=creator_field)
