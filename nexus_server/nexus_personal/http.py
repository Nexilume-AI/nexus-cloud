"""Personal browser bootstrap. Identity creation remains a local CLI operation."""
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import PersonalSessionAuthentication
from .services import installation_context
from .models import PersonalInstallation
from rest_framework.exceptions import NotFound


@method_decorator(ensure_csrf_cookie, name="dispatch")
class PersonalBootstrapView(APIView):
    authentication_classes = [PersonalSessionAuthentication]
    permission_classes = [AllowAny]

    def get(self, request):
        response = Response({
            "product_name": "Nexus Console",
            "distribution": "community",
            "anonymous_access": False,
            "authentication_mode": "single_owner",
            "session_authenticated": bool(request.user and request.user.is_authenticated),
            "authentication": {
                "password": {"enabled": True, "signup_enabled": False},
                "google": {"enabled": False, "signup_enabled": False},
                "github": {"enabled": False, "signup_enabled": False},
            },
        })
        response["Cache-Control"] = "no-store"
        return response


class PersonalContextView(APIView):
    """Read-only owner context, not a tenancy directory or membership API."""
    def get(self, request):
        owner_id, _, _ = installation_context()
        if str(request.user.pk) != owner_id:
            raise NotFound('Personal context not found.')
        row = PersonalInstallation.objects.select_related('tenant', 'project').get(slot=1)
        tenant, project = row.tenant, row.project
        response = Response({
            'tenants': [{'id': str(tenant.pk), 'name': tenant.name, 'slug': tenant.slug, 'status': tenant.status}],
            'projects': [{'id': str(project.pk), 'tenant_id': str(tenant.pk), 'team_id': None,
                'name': project.name, 'status': project.status,
                'instructions_markdown': project.instructions_markdown,
                'instructions_revision': project.instructions_revision,
                'instructions_updated_at': project.instructions_updated_at}],
        })
        response['Cache-Control'] = 'no-store'
        return response
