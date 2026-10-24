from django.urls import path
from . import file_bridge_views as v

urlpatterns = [
    path('internal/agent-runs/<uuid:run_id>/interactions/file-bridges/', v.CreateBridge.as_view()),
    path('agent-file-bridges/<uuid:bridge_id>/', v.Page.as_view()),
    path('agent-file-bridges/<uuid:bridge_id>/redeem/', v.Redeem.as_view()),
    path('agent-file-bridges/<uuid:bridge_id>/file/', v.File.as_view()),
    path('agent-file-bridges/<uuid:bridge_id>/runtime/', v.Runtime.as_view()),
    path('agent-file-bridges/<uuid:bridge_id>/runtime/<uuid:command_id>/', v.Command.as_view()),
]
