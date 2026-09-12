from __future__ import annotations

from django.conf import settings
from django.urls import include, path, re_path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework.permissions import AllowAny

from apps.common.views import HealthCheckView, PublicBootstrapView, ReadinessCheckView, nexus_service_worker, nexus_web_app


urlpatterns = [
    path("nexus-service-worker.js", nexus_service_worker, name="nexus-service-worker"),
    path("api/v1/health/", HealthCheckView.as_view(), name="health"),
    path("api/v1/health/readiness/", ReadinessCheckView.as_view(), name="readiness"),
    path("api/v1/public/bootstrap/", PublicBootstrapView.as_view(), name="public-bootstrap"),
    path("api/v1/schema/", SpectacularAPIView.as_view(permission_classes=[AllowAny]), name="schema"),
    path(
        "api/v1/docs/swagger/",
        SpectacularSwaggerView.as_view(url_name="schema", permission_classes=[AllowAny]),
        name="swagger-ui",
    ),
    path(
        "api/v1/docs/redoc/",
        SpectacularRedocView.as_view(url_name="schema", permission_classes=[AllowAny]),
        name="redoc",
    ),
    *[path(prefix, include(module)) for prefix, module in settings.NEXUS_API_ROUTES],
    re_path(r"^(?!api/|static/)(?P<path>.*)$", nexus_web_app, name="nexus-web"),
]
