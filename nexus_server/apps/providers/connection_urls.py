from django.urls import path
from .connection_views import ProviderExecutionSetupView
from .connection_views import (
    ProviderConnectionListCreateView,
    ProviderConnectionDetailView,
    ProviderConnectionDeletionImpactView,
    ProviderConnectionRemoveView,
    ProviderConnectionStartView,
    ProviderConnectionRepairView,
    ProviderConnectionStopView,
    ProviderConnectionHealthView,
    ProviderConnectionLoginView,
    ProviderConnectionModelRefreshView,
    ProviderConnectionModelOfferRefreshView,
    ProviderConnectionModelDetailView,
)

urlpatterns = [
    path("provider-connections/execution-setup/", ProviderExecutionSetupView.as_view()),
    path("provider-connections/", ProviderConnectionListCreateView.as_view(), name="provider-connections"),
    path("provider-connections/<str:account_id>/", ProviderConnectionDetailView.as_view(), name="provider-connection-detail"),
    path("provider-connections/<str:account_id>/start/", ProviderConnectionStartView.as_view(), name="provider-connection-start"),
    path("provider-connections/<str:account_id>/repair/", ProviderConnectionRepairView.as_view(), name="provider-connection-repair"),
    path("provider-connections/<str:account_id>/stop/", ProviderConnectionStopView.as_view(), name="provider-connection-stop"),
    path("provider-connections/<str:account_id>/health/", ProviderConnectionHealthView.as_view(), name="provider-connection-health"),
    path("provider-connections/<str:account_id>/login/", ProviderConnectionLoginView.as_view(), name="provider-connection-login"),
    path("provider-connections/<str:account_id>/models/refresh/", ProviderConnectionModelRefreshView.as_view(), name="provider-connection-models-refresh"),
    path("provider-connections/<str:account_id>/models/<uuid:offer_id>/", ProviderConnectionModelDetailView.as_view(), name="provider-connection-model-detail"),
    path("provider-connections/<str:account_id>/models/<uuid:offer_id>/refresh/", ProviderConnectionModelOfferRefreshView.as_view(), name="provider-connection-model-refresh"),
    path("provider-connections/<str:account_id>/deletion-impact/", ProviderConnectionDeletionImpactView.as_view(), name="provider-connection-deletion-impact"),
    path("provider-connections/<str:account_id>/remove/", ProviderConnectionRemoveView.as_view(), name="provider-connection-remove"),
]
