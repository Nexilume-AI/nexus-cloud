"""Personal catalog policy: fixed owner/context, no roles, shares or purchase."""
from django.db.models import Q
from rest_framework import exceptions

from apps.common.resource_facts import (ownership_payload, project_for_resource,
    requested_view_scope, resolve_catalog_resource)
from .authentication import validate_owner


class PersonalResourceCatalog:
    def _context(self, request, tenant):
        if request is None or not getattr(request.user, "is_authenticated", False):
            raise exceptions.NotAuthenticated("Sign in as the personal instance owner.")
        user, tenant_id, project_id = validate_owner(request, request.user)
        if str(getattr(tenant, "pk", tenant)) != tenant_id:
            raise exceptions.NotFound("Resource not found.")
        return user, tenant_id, project_id

    def resolve_ownership_project(self, *, request, tenant, ownership):
        _, tenant_id, project_id = self._context(request, tenant)
        if ownership is not None and (ownership.get("scope") != "project"
                                      or str(ownership.get("project_id", "")) != project_id):
            raise exceptions.ValidationError({"ownership": "Use this personal instance's Project."})
        from apps.tenancy.models import Project
        return Project.objects.get(pk=project_id, tenant_id=tenant_id, status="active"), ownership is None

    def _resource(self, *, request, resource_type, obj):
        user, _, project_id = self._context(request, obj.tenant_id)
        # A caller-provided/stale object's tenant, Project or creator is not an
        # authorization source. Resolve the current database row first.
        current = resolve_catalog_resource(tenant=obj.tenant_id, resource_type=resource_type, resource_id=str(obj.pk))
        project, repair_required = project_for_resource(resource_type, current)
        creator_id = getattr(current, "created_by_id", None) or getattr(current, "registered_by_id", None)
        if (repair_required or (project is not None and str(project.pk) != project_id)
                or (creator_id is not None and str(creator_id) != str(user.pk))):
            raise exceptions.NotFound("Resource not found.")
        return current, project

    def resource_access_payload(self, *, request, resource_type, obj, project=None):
        self._resource(request=request, resource_type=resource_type, obj=obj)
        return {"can_discover": True, "can_read": True, "can_use": True,
                "can_edit": True, "can_manage": True, "sources": ["personal_owner"]}

    def resource_context_payload(self, *, request, resource_type, obj):
        _, project = self._resource(request=request, resource_type=resource_type, obj=obj)
        return {"ownership": ownership_payload(project=project),
                "access": {"can_discover": True, "can_read": True, "can_use": True,
                           "can_edit": True, "can_manage": True, "sources": ["personal_owner"]}}

    def discoverable_resource_queryset(self, queryset, *, request, tenant, resource_type,
                                      project_field="project", creator_field="created_by"):
        user, tenant_id, project_id = self._context(request, tenant)
        requested_view_scope(request)  # all is a view choice, never a grant.
        # Provider's runtime-derived ownership has a separate query path; do
        # not silently interpret an absent Project relation as personal access.
        if resource_type not in {"agent", "dataset", "router", "deployment", "model_group", "edge_node"}:
            raise exceptions.ValidationError({"resource_type": "This catalog requires its own ownership resolver."})
        labels = {"agent": "agents.agent", "dataset": "datasets.dataset", "router": "routers.router",
                  "deployment": "deployments.deployment", "model_group": "deployments.modelgroup", "edge_node": "agents.edgenode"}
        if queryset.model._meta.label_lower != labels[resource_type] or project_field != "project":
            raise exceptions.ValidationError({"resource_type": "Resource catalog model mismatch."})
        queryset = queryset.filter(tenant_id=tenant_id).exclude(status="deleted").filter(
            Q(project_id=project_id) | Q(project__isnull=True))
        fields = {field.name for field in queryset.model._meta.fields}
        for name in ("created_by", "registered_by"):
            if name in fields:
                queryset = queryset.filter(Q(**{name + "_id": user.pk}) | Q(**{name + "__isnull": True}))
        return queryset

    def prepare_catalog_rows(self, *, data, context, child):
        rows = list(data.all() if hasattr(data, "all") else data)
        request = context.get("request")
        for row in rows:
            self._resource(request=request, resource_type=getattr(child, "catalog_resource_type", ""), obj=row)
        return rows
