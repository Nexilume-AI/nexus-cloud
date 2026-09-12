"""Real fixed-owner Edge identity checks, without roles or commercial plans."""
from rest_framework import exceptions
from django.contrib.auth import get_user_model
from apps.tenancy.models import Project
from .services import installation_context
from .resource_limits import PersonalCapacityExceeded


class PersonalEdgePolicy:
    def can_manage_relay(self, request):
        owner_id, _, _ = installation_context()
        user = getattr(request, 'user', None)
        return bool(user and user.is_authenticated and user.is_active and str(user.pk) == owner_id)

    def node_context(self, *, request, resource_type, obj):
        if resource_type != 'edge_node':
            raise exceptions.NotFound('Edge node not found.')
        if request is not None:
            from apps.common.resource_catalog import resource_context_payload
            return resource_context_payload(request=request, resource_type=resource_type, obj=obj)
        # Enrollment is authorized by the consumed pairing code, not by a
        # browser session. This serializer context grants no console access.
        self.validate_node(obj)
        from apps.common.resource_facts import ownership_payload
        return {'ownership': ownership_payload(project=obj.project), 'access': {
            'can_discover': False, 'can_read': False, 'can_use': False,
            'can_edit': False, 'can_manage': False, 'sources': []}}

    def _identity(self, record, owner_id):
        fixed_owner_id, tenant_id, project_id = installation_context()
        if (str(record.tenant_id) != tenant_id or str(record.project_id) != project_id
                or str(owner_id) != fixed_owner_id):
            raise exceptions.AuthenticationFailed('This Edge identity is unavailable in the personal installation.')
        return get_user_model().objects.get(pk=fixed_owner_id, is_active=True)

    def validate_node(self, node):
        self._identity(node, node.registered_by_id)

    def validate_pairing(self, pairing):
        self._identity(pairing, pairing.created_by_id)

    def managed_agent_owner(self, node):
        return self._identity(node, node.registered_by_id)

    def resolve_router_project(self, *, tenant, project_id):
        _, tenant_id, fixed_project_id = installation_context()
        if str(tenant.pk) != tenant_id or project_id not in (None, '', fixed_project_id):
            raise exceptions.NotFound('Project not found.')
        return Project.objects.get(pk=fixed_project_id, tenant_id=tenant_id, status='active')

    def registration_failure(self, *, error, attempted_at):
        return {'code': 'PERSONAL_CAPACITY_EXCEEDED' if isinstance(error, PersonalCapacityExceeded) else 'REGISTRATION_REJECTED',
                'http_status': error.status_code, 'occurred_at': attempted_at.isoformat()}
