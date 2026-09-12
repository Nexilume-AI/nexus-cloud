from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.db import connection
from django.http import Http404, HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .runtime_health import runtime_readiness


class HealthCheckView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return Response({"status": "ok"})


class ReadinessCheckView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        ready, payload = runtime_readiness()
        return Response(payload, status=200 if ready else 503)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class PublicBootstrapView(APIView):
    """Safe, tenant-independent metadata for the anonymous console shell."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    def get(self, request):
        django_user = request.user
        response = Response(
            {
                "product_name": "Nexus Console",
                "anonymous_access": True,
                "authentication_mode": "on_demand",
                "session_authenticated": bool(django_user and django_user.is_authenticated),
                "authentication": {
                    "google": {
                        "enabled": _google_oauth_available(),
                        "signup_enabled": settings.NEXUS_GOOGLE_OAUTH_SIGNUP_MODE == "open",
                    },
                    "github": {
                        "enabled": _github_oauth_available(),
                        "signup_enabled": settings.NEXUS_GITHUB_OAUTH_SIGNUP_MODE == "open",
                    },
                },
                "capabilities": [
                    {
                        "id": "ai_resources",
                        "label": "AI resources",
                        "description": "Build agents, organize data assets, and connect execution devices.",
                    },
                    {
                        "id": "model_services",
                        "label": "Model services",
                        "description": "Connect providers, publish capacity, and route model traffic.",
                    },
                    {
                        "id": "operations",
                        "label": "Operations",
                        "description": "Monitor reliability, billing, and financial workflows after sign-in.",
                    },
                ],
            }
        )
        # This response also establishes the CSRF cookie used by the browser
        # session. Do not allow an intermediary to cache and replay Set-Cookie.
        response["Cache-Control"] = "no-store"
        return response


def _google_oauth_available() -> bool:
    # Import lazily so the anonymous shell remains available even when the
    # optional Google OAuth dependency or secret is not configured.
    from apps.accounts.google_oauth import google_oauth_available

    return google_oauth_available()


def _github_oauth_available() -> bool:
    from apps.accounts.github_oauth import github_oauth_available

    return github_oauth_available()


@ensure_csrf_cookie
def nexus_web_app(request, path: str = ""):
    index_path = Path(settings.NEXUS_WEB_INDEX_PATH)
    if not index_path.exists():
        raise Http404("Nexus web console has not been built. Run `npm run build` in nexus_web.")
    return HttpResponse(index_path.read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")


def nexus_service_worker(request):
    worker_path = Path(settings.NEXUS_WEB_INDEX_PATH).parent / "nexus-service-worker.js"
    if not worker_path.exists():
        raise Http404("Nexus service worker is not available.")
    response = HttpResponse(worker_path.read_text(encoding="utf-8"), content_type="application/javascript; charset=utf-8")
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Service-Worker-Allowed"] = "/"
    return response
