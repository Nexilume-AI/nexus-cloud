from django.urls import path
from . import relay_devices as v
from .relay_page import page

urlpatterns = [
    path("page/", page),
    path("start/", v.RelayStart.as_view()),
    path("token/", v.RelayToken.as_view()),
    path("approval/", v.RelayApproval.as_view()),
    path("models/", v.RelayModels.as_view()),
    path("revoke/", v.RelayRevoke.as_view()),
    path("devices/", v.RelayDevices.as_view()),
    path("devices/<uuid:device_id>/revoke/", v.RelayBrowserRevoke.as_view()),
]
