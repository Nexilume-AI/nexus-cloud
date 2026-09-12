"""Bind real Mobile devices to the durable personal owner and local Project."""
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from rest_framework import exceptions
from apps.common.subjects import request_subject
from apps.tenancy.models import Project
from .authentication import validate_owner
from .services import installation_context


class PersonalMobileContext:
    def visible_devices(self, *, request, queryset):
        owner, tenant_id, project_id = validate_owner(request, request.user)
        return queryset.filter(tenant_id=tenant_id, project_id=project_id,
            created_by=owner, owner_principal_type="user",
            owner_subject_hash=request_subject(request).subject_hash)

    def resolve_project(self, *, request, tenant, project_id):
        _, tenant_id, local_project = validate_owner(request, request.user)
        if str(tenant.pk) != tenant_id or (project_id is not None and str(project_id) != local_project):
            raise exceptions.ValidationError({"project_id": "Use this personal instance's Project."})
        return Project.objects.get(pk=local_project, tenant_id=tenant_id, status="active")

    def device_context_valid(self, device):
        try:
            owner_id, tenant_id, project_id = installation_context()
            owner = get_user_model().objects.get(pk=owner_id, is_active=True)
            request = SimpleNamespace(user=owner, META={}, headers={})
            validate_owner(request, owner)
        except (ImproperlyConfigured, exceptions.APIException, get_user_model().DoesNotExist):
            return False
        subject = request_subject(request)
        return (str(device.tenant_id) == tenant_id and str(device.project_id) == project_id
            and str(device.created_by_id) == owner_id and device.owner_principal_type == "user"
            and device.owner_subject_hash == subject.subject_hash)
