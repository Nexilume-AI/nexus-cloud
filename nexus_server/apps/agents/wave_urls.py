from django.urls import path
from .wave_views import InternalWaveView, PageView, RedeemView, StateView, EventsView, WaveFileView, AssetView, CanvasView

urlpatterns = [
    path('internal/agent-runs/<uuid:run_id>/interactions/wave-views/', InternalWaveView.as_view()),
    path('internal/agent-runs/<uuid:run_id>/interactions/wave-views/<uuid:view_id>/', InternalWaveView.as_view()),
    path('agent-wave-views/<uuid:view_id>/', PageView.as_view()),
    path('agent-wave-views/<uuid:view_id>/redeem/', RedeemView.as_view()),
    path('agent-wave-views/<uuid:view_id>/state/', StateView.as_view()),
    path('agent-wave-views/<uuid:view_id>/events/', EventsView.as_view()),
    path('agent-wave-views/<uuid:view_id>/files/<int:index>/', WaveFileView.as_view()),
    path('agent-wave-views/<uuid:view_id>/assets/<str:name>', AssetView.as_view()),
    path('agent-wave-views/<uuid:view_id>/canvas/', CanvasView.as_view()),
]
