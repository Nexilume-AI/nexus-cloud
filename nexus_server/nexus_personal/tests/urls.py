from django.middleware.csrf import get_token
from django.urls import include, path
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.services import login_user
from apps.common.context import get_tenant_context


class OwnerProbe(APIView):
    def get(self, request):
        context = get_tenant_context()
        return Response({"user": str(request.user.pk), "tenant": context.tenant_id,
                         "project": context.project_id, "csrf": get_token(request)})

    def post(self, request):
        return self.get(request)


class LoginProbe(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        return Response(login_user(request=request, **request.data))


# Test-only auth introspection surrounds the real personal URL composition.
urlpatterns = [path("owner/", OwnerProbe.as_view()), path("login/", LoginProbe.as_view()),
               path("", include("nexus_personal.urls"))]
