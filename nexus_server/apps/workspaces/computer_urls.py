"""Computer Runtime protocol routes independent of the Tool Setup host."""
from django.urls import path
from .computer_views import (
    ComputerListView, ComputerPairingCodeView, ComputerRevokeView,
    ComputerRuntimeEnrollView, ComputerRuntimeSessionView,
    ComputerRuntimeCommandUploadView, ComputerRuntimeUnpairView,
)

urlpatterns = [
    path("computers/", ComputerListView.as_view(), name="computers"),
    path("computers/pairing-codes/", ComputerPairingCodeView.as_view(), name="computer-pairing-codes"),
    path("computers/<uuid:connection_id>/revoke/", ComputerRevokeView.as_view(), name="computer-revoke"),
    path("computer-runtime/v1/enroll/", ComputerRuntimeEnrollView.as_view(), name="computer-runtime-enroll"),
    path("computer-runtime/v1/sessions/", ComputerRuntimeSessionView.as_view(), name="computer-runtime-session"),
    path("computer-runtime/v1/unpair/", ComputerRuntimeUnpairView.as_view(), name="computer-runtime-unpair"),
    path("computer-runtime/v1/commands/<uuid:command_id>/upload/", ComputerRuntimeCommandUploadView.as_view(), name="computer-runtime-command-upload"),
]
