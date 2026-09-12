"""Resource identity and ownership facts, without IAM roles or sharing policy."""
from __future__ import annotations

import uuid
from typing import Any
from django.db.models import QuerySet
from rest_framework import exceptions, serializers
from rest_framework.exceptions import NotFound
from apps.common.models import SoftDeleteModel
from apps.tenancy.models import Project, Tenant

OWNERSHIP_ORGANIZATION = "organization"
OWNERSHIP_PROJECT = "project"
OWNERSHIP_CHOICES = (OWNERSHIP_ORGANIZATION, OWNERSHIP_PROJECT)


class ResourceOwnershipInputSerializer(serializers.Serializer):
    scope = serializers.ChoiceField(choices=OWNERSHIP_CHOICES)
    project_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        scope = attrs["scope"]
        project_id = attrs.get("project_id")
        if scope == OWNERSHIP_PROJECT and not project_id:
            raise serializers.ValidationError({"project_id": "Select a Project for Project ownership."})
        if scope == OWNERSHIP_ORGANIZATION and project_id:
            raise serializers.ValidationError({"project_id": "Organization shared resources cannot specify a Project."})
        return attrs


def ownership_payload(*, project: Project | None, repair_required: bool = False) -> dict[str, Any]:
    if repair_required:
        return {
            "scope": "unknown",
            "project_id": None,
            "project_name": None,
            "label": "Ownership needs repair",
            "repair_required": True,
        }
    if project is None:
        return {
            "scope": OWNERSHIP_ORGANIZATION,
            "project_id": None,
            "project_name": None,
            "label": "Organization shared",
            "repair_required": False,
        }
    return {
        "scope": OWNERSHIP_PROJECT,
        "project_id": str(project.id),
        "project_name": project.name,
        "label": f"Project: {project.name}",
        "repair_required": False,
    }


def provider_connection_project(account) -> tuple[Project | None, bool]:
    prefetched = getattr(account, "prefetched_connection_runtimes", None)
    runtimes = list(prefetched) if prefetched is not None else list(
        account.source_runtime_accounts
        .exclude(status=SoftDeleteModel.STATUS_DELETED)
        .select_related("project")
        .order_by("created_at")
    )
    if len(runtimes) != 1:
        return None, True
    return runtimes[0].project, False


def project_for_resource(resource_type: str, obj) -> tuple[Project | None, bool]:
    if resource_type == "provider_connection":
        return provider_connection_project(obj)
    return getattr(obj, "project", None), False


def requested_view_scope(request) -> str:
    explicit = str(request.query_params.get("view_scope") or "").strip()
    if explicit and explicit not in {"all", "current"}:
        raise exceptions.ValidationError({"view_scope": "Use all or current."})
    project_id = str(getattr(request, "project_id", "") or "").strip()
    value = explicit or ("current" if project_id else "all")
    if value == "current" and not project_id:
        raise exceptions.ValidationError({"view_scope": "Current Project view requires X-Nexus-Project."})
    return value


def resolve_catalog_resource(*, tenant: Tenant, resource_type: str, resource_id: str):
    model = None
    if resource_type == "agent":
        from apps.agents.models import Agent as model
    elif resource_type == "dataset":
        from apps.datasets.models import Dataset as model
    elif resource_type == "router":
        from apps.routers.models import Router as model
    elif resource_type == "provider_connection":
        from apps.providers.models import ProviderAccount as model
    elif resource_type == "deployment":
        from apps.deployments.models import Deployment as model
    elif resource_type == "model_group":
        from apps.deployments.models import ModelGroup as model
    elif resource_type == "edge_node":
        from apps.agents.models import EdgeNode as model
    if model is None:
        raise exceptions.ValidationError({"resource_type": "This resource type does not support sharing."})
    queryset = model.objects.filter(tenant=tenant).exclude(status=SoftDeleteModel.STATUS_DELETED)
    try:
        uuid.UUID(str(resource_id))
        obj = queryset.filter(id=resource_id).first()
    except (TypeError, ValueError):
        obj = queryset.filter(deployment_id=resource_id).first() if resource_type == "deployment" else None
    if obj is None:
        raise NotFound("Resource not found.")
    return obj
