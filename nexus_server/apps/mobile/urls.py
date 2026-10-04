from __future__ import annotations

from django.urls import path
from .video_views import MobileVideoStartView, MobileVideoViewerView, MobileVideoDevicePollView, MobileVideoDeviceSignalView

from .views import (
    MobileAggregateStatusView,
    MobileCommandApproveView,
    MobileCommandCancelView,
    MobileCommandDetailView,
    MobileCommandListCreateView,
    MobileCommandRejectView,
    MobileDeviceCommandResultView,
    MobileDeviceDetailView,
    MobileDeviceHeartbeatView,
    MobileDeviceListCreateView,
    MobileDeviceNextCommandView,
    MobileDeviceScreenshotView,
    MobileDeviceTokenRotateView,
    MobileMCPExportView,
    MobileMCPView,
)


urlpatterns = [
    path("mobile-devices/<uuid:device_id>/video/", MobileVideoStartView.as_view()),
    path("mobile-devices/<uuid:device_id>/device/video/", MobileVideoDevicePollView.as_view()),
    path("mobile-video/<uuid:session_id>/", MobileVideoViewerView.as_view()),
    path("mobile-video/<uuid:session_id>/device/", MobileVideoDeviceSignalView.as_view()),
    path("mobile-devices/aggregate-status/", MobileAggregateStatusView.as_view(), name="mobile-device-aggregate-status"),
    path("mobile-devices/", MobileDeviceListCreateView.as_view(), name="mobile-devices"),
    path("mobile-devices/<uuid:device_id>/", MobileDeviceDetailView.as_view(), name="mobile-device-detail"),
    path("mobile-devices/<uuid:device_id>/screenshot/", MobileDeviceScreenshotView.as_view(), name="mobile-device-screenshot"),
    path("mobile-devices/<uuid:device_id>/rotate-token/", MobileDeviceTokenRotateView.as_view(), name="mobile-device-rotate-token"),
    path("mobile-devices/<uuid:device_id>/commands/", MobileCommandListCreateView.as_view(), name="mobile-device-commands"),
    path("mobile-devices/<uuid:device_id>/device/heartbeat/", MobileDeviceHeartbeatView.as_view(), name="mobile-device-heartbeat"),
    path("mobile-devices/<uuid:device_id>/device/commands/next/", MobileDeviceNextCommandView.as_view(), name="mobile-device-next-command"),
    path("mobile-devices/<uuid:device_id>/mcp/export/", MobileMCPExportView.as_view(), name="mobile-mcp-export"),
    path("mobile-devices/<uuid:device_id>/mcp/", MobileMCPView.as_view(), name="mobile-mcp"),
    path("mobile-commands/<uuid:command_id>/approve/", MobileCommandApproveView.as_view(), name="mobile-command-approve"),
    path("mobile-commands/<uuid:command_id>/reject/", MobileCommandRejectView.as_view(), name="mobile-command-reject"),
    path("mobile-commands/<uuid:command_id>/cancel/", MobileCommandCancelView.as_view(), name="mobile-command-cancel"),
    path("mobile-commands/<uuid:command_id>/", MobileCommandDetailView.as_view(), name="mobile-command-detail"),
    path("mobile-commands/<uuid:command_id>/device/result/", MobileDeviceCommandResultView.as_view(), name="mobile-command-result"),
]
