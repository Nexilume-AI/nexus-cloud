"""Real local-owner Computer context; no roles or shared devices."""
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from rest_framework import exceptions
from apps.common.subjects import request_subject
from apps.tenancy.models import Project
from apps.workspaces.models import WorkspaceConnection, WorkspaceTerminalSession
from .authentication import PersonalBearerAuthentication, validate_owner
from .services import installation_context


class PersonalWorkspaceContext:
    def resolve_project(self, *, request, tenant, project_id):
        _, tenant_id, local_project = validate_owner(request, request.user)
        if str(tenant.pk) != tenant_id or (project_id is not None and str(project_id) != local_project):
            raise exceptions.ValidationError({"project_id": "Use this personal instance's Project."})
        return Project.objects.get(pk=local_project, tenant_id=tenant_id, status="active")

    def visible_connections(self, *, request, queryset):
        owner, tenant_id, project_id = validate_owner(request, request.user)
        return queryset.filter(tenant_id=tenant_id, project_id=project_id, created_by=owner,
            owner_subject_type="user", owner_subject_hash=request_subject(request).subject_hash)

    def connection_valid(self, connection):
        try:
            owner_id, _, _ = installation_context()
            owner = get_user_model().objects.get(pk=owner_id, is_active=True)
            request = SimpleNamespace(user=owner, META={}, headers={})
            return self.visible_connections(request=request, queryset=WorkspaceConnection.objects.filter(
                pk=connection.pk, status="active")).exists()
        except (ImproperlyConfigured, exceptions.APIException, get_user_model().DoesNotExist):
            return False

    def visible_terminals(self, *, request, queryset):
        owner, tenant_id, project_id = validate_owner(request, request.user)
        connections = self.visible_connections(request=request, queryset=WorkspaceConnection.objects.filter(
            status="active", connection_type=WorkspaceConnection.TYPE_RUNTIME))
        return queryset.filter(tenant_id=tenant_id, project_id=project_id, created_by=owner,
            connection_id__in=connections.values("pk"))

    def terminal_valid(self, session):
        try:
            owner_id, _, _ = installation_context()
            owner = get_user_model().objects.get(pk=owner_id, is_active=True)
            request = SimpleNamespace(user=owner, META={}, headers={})
            return self.visible_terminals(request=request, queryset=WorkspaceTerminalSession.objects.filter(
                pk=session.pk, status__in=[WorkspaceTerminalSession.STATUS_CREATED, WorkspaceTerminalSession.STATUS_ACTIVE],
                connection__runtime_device__revoked_at__isnull=True,
                connection__runtime_device__isnull=False)).exists()
        except (ImproperlyConfigured, exceptions.APIException, get_user_model().DoesNotExist):
            return False

    def terminal_identity(self, *, scope, user, token):
        headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in scope.get("headers", [])}
        request = SimpleNamespace(user=user, headers=headers, META={
            "HTTP_X_NEXUS_TENANT": headers.get("x-nexus-tenant", ""),
            "HTTP_X_NEXUS_PROJECT": headers.get("x-nexus-project", ""),
        })
        if token:
            authenticated, _ = PersonalBearerAuthentication().authenticate_owner_jwt(request, token)
            if authenticated.pk != user.pk:
                raise exceptions.AuthenticationFailed("Terminal identity mismatch.")
        else:
            validate_owner(request, user)
