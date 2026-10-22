from django.urls import path
from . import interactive_views as v

urlpatterns = [
    path('internal/agent-runs/<uuid:run_id>/interactions/views/', v.InternalView.as_view()),
    path('internal/agent-runs/<uuid:run_id>/interactions/views/<uuid:view_id>/', v.InternalView.as_view()),
    path('agent-interactive-views/<uuid:view_id>/', v.PageView.as_view()),
    path('agent-interactive-views/<uuid:view_id>/redeem/', v.RedeemView.as_view()),
    path('agent-interactive-views/<uuid:view_id>/state/', v.StateView.as_view()),
    path('agent-interactive-views/<uuid:view_id>/custom/', v.CustomView.as_view()),
    path('agent-interactive-views/<uuid:view_id>/actions/', v.ActionView.as_view()),
    path('agent-interactive-views/<uuid:view_id>/events/', v.EventsView.as_view()),
    path('agent-interactive-views/<uuid:view_id>/runtime/', v.RuntimeView.as_view()),
    path('agent-interactive-views/<uuid:view_id>/runtime/events/', v.EventsView.as_view(runtime=True)),
]
