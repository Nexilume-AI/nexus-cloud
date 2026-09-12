from django.urls import path
from .callback_event_views import InternalDisplayEventIngestView

urlpatterns = [
    path("internal/ag-ui/runs/<uuid:run_id>/events/", InternalDisplayEventIngestView.as_view(), name="internal-agui-event-ingest"),
    path("internal/display/runs/<uuid:run_id>/events/", InternalDisplayEventIngestView.as_view(), name="internal-display-event-ingest"),
]
