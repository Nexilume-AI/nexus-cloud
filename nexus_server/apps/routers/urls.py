from __future__ import annotations

from django.urls import path

from .views import (
    RouterDeployView,
    RouterDetailView,
    RouterChildBindingDetailView,
    RouterChildBindingsView,
    RouterAggregationCandidatesView,
    RouterCredentialsExportView,
    RouterInvokeView,
    RouterListCreateView,
    RouterModelGroupsView,
    RouterOutputDetailView,
    RouterOutputsView,
    RouterPolicyView,
    RouterPricingView,
    RouterProviderPreferenceDetailView,
    RouterProviderPreferenceView,
    RouterSourceView,
    RouterTestView,
    RouterTraceListView,
    RouterUploadView,
)


urlpatterns = [
    path("routers/", RouterListCreateView.as_view(), name="routers"),
    path("routers/<uuid:router_id>/", RouterDetailView.as_view(), name="router-detail"),
    path("routers/<uuid:router_id>/source/", RouterSourceView.as_view(), name="router-source"),
    path("routers/<uuid:router_id>/upload/", RouterUploadView.as_view(), name="router-upload"),
    path("routers/<uuid:router_id>/deploy/", RouterDeployView.as_view(), name="router-deploy"),
    path("routers/<uuid:router_id>/export-credentials/", RouterCredentialsExportView.as_view(), name="router-export-credentials"),
    path("routers/<uuid:router_id>/invoke/", RouterInvokeView.as_view(), name="router-invoke"),
    path("routers/<uuid:router_id>/test/", RouterTestView.as_view(), name="router-test"),
    path("routers/<uuid:router_id>/traces/", RouterTraceListView.as_view(), name="router-traces"),
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
    path("routers/<uuid:router_id>/pricing/", RouterPricingView.as_view(), name="router-pricing"),
    path(
        "routers/<uuid:router_id>/provider-preferences/",
        RouterProviderPreferenceView.as_view(),
        name="router-provider-preferences",
    ),
    path(
        "routers/<uuid:router_id>/provider-preferences/<uuid:preference_id>/",
        RouterProviderPreferenceDetailView.as_view(),
        name="router-provider-preference-detail",
    ),
]
