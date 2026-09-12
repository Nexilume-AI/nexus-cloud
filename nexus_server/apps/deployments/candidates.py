"""Shared Router/Model Pool candidate graph; no commercial Gateway import."""
from __future__ import annotations
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from apps.deployments.models import Deployment, ModelGroup
from apps.routers.models import RouterOutput
from apps.tenancy.models import Tenant


OPERATIONS = ("model_group_links", "resolve_model_source_deployment")


def source_candidates_backend():
    try:
        backend = import_string(getattr(settings, "NEXUS_SOURCE_CANDIDATES_BACKEND", ""))()
    except (ImportError, AttributeError, ValueError, TypeError):
        raise ImproperlyConfigured("Source candidate integration is not configured.") from None
    if not all(callable(getattr(backend, name, None)) for name in OPERATIONS):
        raise ImproperlyConfigured("Source candidate integration is incomplete.")
    return backend


def output_has_available_source(*, tenant: Tenant, output: RouterOutput) -> bool:
    if output.model_group_id is None or output.model_group is None:
        return False
    return bool(model_group_deployment_candidates(tenant=tenant, group=output.model_group))


def model_group_deployment_candidates(*, tenant: Tenant, group: ModelGroup, provider_name: str = "") -> list[Deployment]:
    links = source_candidates_backend().model_group_links(tenant=tenant, group=group, provider_name=provider_name)
    deployments = []
    for link in links:
        source = link.deployment
        deployment = resolve_model_source_deployment(tenant=tenant, source=source)
        if deployment is None:
            continue
        deployment._nexus_model_group_link_id = str(link.id)
        deployment._nexus_selected_model_group_id = str(group.id)
        deployment._nexus_selected_model_group_name = group.name
        deployment._nexus_consumer_source_id = str(source.id)
        deployment._nexus_source_priority = link.priority
        deployment._nexus_source_weight = link.weight
        deployment._nexus_fallback_order = link.fallback_order
        deployments.append(deployment)
    return deployments


def resolve_model_source_deployment(*, tenant: Tenant, source: Deployment) -> Deployment | None:
    return source_candidates_backend().resolve_model_source_deployment(tenant=tenant, source=source)
