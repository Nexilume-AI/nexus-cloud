"""Relay-only browser and native request boundaries, independent of Desktop."""
from django.core.exceptions import ImproperlyConfigured
from rest_framework import exceptions
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.settings import api_settings
from rest_framework.views import APIView


class NoStore(APIView):
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response


class Native(NoStore):
    authentication_classes = []
    permission_classes = [AllowAny]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.headers.get("Origin"):
            raise exceptions.PermissionDenied("Use the Relay SDK for this request.")


class BrowserApproval(NoStore):
    permission_classes = [IsAuthenticated]

    def get_authenticators(self):
        # Keep the host's session/owner checks and CSRF; reject bearer credentials.
        classes = [cls for cls in api_settings.DEFAULT_AUTHENTICATION_CLASSES
                   if issubclass(cls, SessionAuthentication)]
        if not classes:
            raise ImproperlyConfigured("Relay approval requires browser session authentication.")
        return [cls() for cls in classes]
