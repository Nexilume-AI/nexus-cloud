"""Operational Router URLs; excludes uncomposed Gateway and commerce actions."""
from django.urls import path
from .trace_views import RouterTraceListView
from .credential_views import RouterCredentialsExportView, RouterInvokeView
from .management_views import (RouterListCreateView, RouterDetailView, RouterUploadView, RouterSourceView, RouterDeployView, RouterModelGroupsView, RouterOutputsView, RouterOutputDetailView, RouterChildBindingsView, RouterAggregationCandidatesView, RouterChildBindingDetailView, RouterPolicyView)

urlpatterns = [
    path("routers/<uuid:router_id>/export-credentials/", RouterCredentialsExportView.as_view(), name="router-export-credentials"),
    path("routers/<uuid:router_id>/invoke/", RouterInvokeView.as_view(), name="router-invoke"),
    path("routers/<uuid:router_id>/traces/", RouterTraceListView.as_view(), name="router-traces"),
    path("routers/", RouterListCreateView.as_view(), name="routers"),
    path("routers/<uuid:router_id>/", RouterDetailView.as_view(), name="router-detail"),
    path("routers/<uuid:router_id>/source/", RouterSourceView.as_view(), name="router-source"),
    path("routers/<uuid:router_id>/upload/", RouterUploadView.as_view(), name="router-upload"),
    path("routers/<uuid:router_id>/deploy/", RouterDeployView.as_view(), name="router-deploy"),
    path("routers/<uuid:router_id>/model-groups/", RouterModelGroupsView.as_view(), name="router-model-groups"),
    path("routers/<uuid:router_id>/outputs/", RouterOutputsView.as_view(), name="router-outputs"),
    path(
        "routers/<uuid:router_id>/outputs/<uuid:output_id>/",
        RouterOutputDetailView.as_view(),
        name="router-output-detail",
    ),
    path(
        "routers/<uuid:router_id>/aggregation-candidates/",
        RouterAggregationCandidatesView.as_view(),
        name="router-aggregation-candidates",
    ),
    path(
        "routers/<uuid:router_id>/child-bindings/",
        RouterChildBindingsView.as_view(),
        name="router-child-bindings",
    ),
    path(
        "routers/<uuid:router_id>/child-bindings/<uuid:binding_id>/",
        RouterChildBindingDetailView.as_view(),
        name="router-child-binding-detail",
    ),
    path("routers/<uuid:router_id>/policy/", RouterPolicyView.as_view(), name="router-policy"),
]
