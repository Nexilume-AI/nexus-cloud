"""Concrete local-owner authorization, never IAM roles or an allow-all backend."""
from types import SimpleNamespace
from django.core.exceptions import ValidationError
from rest_framework import exceptions
from apps.common.resource_facts import resolve_catalog_resource
from .resource_catalog import PersonalResourceCatalog


class PersonalAuthorizationBackend:
    def has_permission(self, user, tenant, action, resource_type=None, resource_id=None):
        if action.startswith('workspace.edge_router.'):
            if action not in {f'workspace.edge_router.{verb}_own' for verb in ('create', 'read', 'update', 'revoke', 'use')}:
                return False
            request = SimpleNamespace(user=user, META={}, query_params={})
            try:
                catalog = PersonalResourceCatalog()
                catalog._context(request, tenant)
                if resource_type is None and resource_id is None:
                    return True
                if resource_type != 'edge_node' or not resource_id:
                    return False
                node = resolve_catalog_resource(tenant=tenant, resource_type='edge_node', resource_id=resource_id)
                catalog._resource(request=request, resource_type='edge_node', obj=node)
                from .edge_policy import PersonalEdgePolicy
                PersonalEdgePolicy().validate_node(node)
                return True
            except (exceptions.APIException, ValidationError, ValueError, TypeError):
                return False
        if action == "provider.create":
            request = SimpleNamespace(user=user, META={}, query_params={})
            try:
                _, _, project_id = PersonalResourceCatalog()._context(request, tenant)
                return resource_type == "project" and str(resource_id) == project_id
            except (exceptions.APIException, ValidationError, ValueError, TypeError):
                return False
        own_actions = {f"workspace.connection.{verb}_own" for verb in ("create", "read", "update", "delete", "use")}
        if action in own_actions:
            request = SimpleNamespace(user=user, META={}, headers={}, query_params={})
            try:
                PersonalResourceCatalog()._context(request, tenant)
                if resource_type is None and resource_id is None:
                    return True
                if resource_type != "workspace_connection" or not resource_id:
                    return False
                from apps.workspaces.models import WorkspaceConnection
                from .workspace_policy import PersonalWorkspaceContext
                return PersonalWorkspaceContext().visible_connections(request=request,
                    queryset=WorkspaceConnection.objects.filter(pk=resource_id, status="active")).exists()
            except (exceptions.APIException, ValidationError, ValueError, TypeError):
                return False
        # Only the Agent management surface is composed here so far. Unknown
        # resources/actions remain unavailable until their real policy is wired.
        if action not in {"admin", "use", "agent.observability.read"}:
            return False
        if resource_type is None and resource_id is None:
            if action != "admin":
                return False
        elif resource_type != "agent" or not resource_id:
            return False
        request = SimpleNamespace(user=user, META={}, query_params={})
        catalog = PersonalResourceCatalog()
        try:
            catalog._context(request, tenant)
            if resource_type is not None:
                obj = resolve_catalog_resource(tenant=tenant, resource_type=resource_type, resource_id=resource_id)
                catalog._resource(request=request, resource_type=resource_type, obj=obj)
        except (exceptions.APIException, ValidationError, ValueError, TypeError):
            return False
        return True
