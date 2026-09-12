from __future__ import annotations

import json

from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import (
    MobileCommandCreateSerializer,
    MobileCommandResultSerializer,
    MobileCommandSerializer,
    MobileDeviceCreateSerializer,
    MobileDeviceHeartbeatSerializer,
    MobileDeviceSerializer,
    MobileDeviceUpdateSerializer,
)
from .services import (
    approve_mobile_command,
    aggregate_mobile_status,
    cancel_mobile_command,
    complete_device_command,
    create_mobile_command,
    create_mobile_device,
    delete_mobile_command,
    delete_mobile_device,
    device_heartbeat,
    export_mobile_mcp,
    get_mobile_device,
    get_mobile_screenshot,
    handle_mobile_mcp,
    list_mobile_commands,
    list_mobile_devices,
    next_device_command,
    reject_mobile_command,
    rotate_mobile_device_token,
    update_mobile_device,
)


class MobileAggregateStatusView(APIView):
    def get(self, request):
        return Response(aggregate_mobile_status(request=request))


class MobileDeviceListCreateView(APIView):
    def get(self, request):
        return Response(MobileDeviceSerializer(list_mobile_devices(request=request), many=True).data)

    def post(self, request):
        serializer = MobileDeviceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device, token = create_mobile_device(request=request, data=serializer.validated_data)
        data = MobileDeviceSerializer(device).data
        data["pairing_token"] = token
        return Response(data, status=status.HTTP_201_CREATED)


class MobileDeviceDetailView(APIView):
    def get(self, request, device_id):
        return Response(MobileDeviceSerializer(get_mobile_device(request=request, device_id=device_id)).data)

    def patch(self, request, device_id):
        serializer = MobileDeviceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device = update_mobile_device(request=request, device_id=device_id, data=serializer.validated_data)
        return Response(MobileDeviceSerializer(device).data)

    def delete(self, request, device_id):
        device = delete_mobile_device(request=request, device_id=device_id)
        return Response(MobileDeviceSerializer(device).data)


class MobileDeviceScreenshotView(APIView):
    def get(self, request, device_id):
        content, content_type = get_mobile_screenshot(request=request, device_id=device_id)
        response = HttpResponse(content, content_type=content_type)
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Disposition"] = 'inline; filename="nexus-mobile-screen"'
        return response


class MobileDeviceTokenRotateView(APIView):
    def post(self, request, device_id):
        device, token = rotate_mobile_device_token(request=request, device_id=device_id)
        data = MobileDeviceSerializer(device).data
        data["pairing_token"] = token
        return Response(data)


class MobileCommandListCreateView(APIView):
    def get(self, request, device_id):
        return Response(MobileCommandSerializer(list_mobile_commands(request=request, device_id=device_id), many=True).data)

    def post(self, request, device_id):
        serializer = MobileCommandCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        command = create_mobile_command(request=request, device_id=device_id, data=serializer.validated_data)
        return Response(MobileCommandSerializer(command).data, status=status.HTTP_201_CREATED)


class MobileCommandApproveView(APIView):
    def post(self, request, command_id):
        return Response(MobileCommandSerializer(approve_mobile_command(request=request, command_id=command_id)).data)


class MobileCommandRejectView(APIView):
    def post(self, request, command_id):
        return Response(MobileCommandSerializer(reject_mobile_command(request=request, command_id=command_id)).data)


class MobileCommandCancelView(APIView):
    def post(self, request, command_id):
        return Response(MobileCommandSerializer(cancel_mobile_command(request=request, command_id=command_id)).data)


class MobileCommandDetailView(APIView):
    def delete(self, request, command_id):
        return Response(MobileCommandSerializer(delete_mobile_command(request=request, command_id=command_id)).data)


class MobileDeviceHeartbeatView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, device_id):
        serializer = MobileDeviceHeartbeatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device = device_heartbeat(request=request, device_id=device_id, data=serializer.validated_data)
        return Response(MobileDeviceSerializer(device).data)


class MobileDeviceNextCommandView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, device_id):
        command = next_device_command(request=request, device_id=device_id)
        return Response(MobileCommandSerializer(command).data if command else {"command": None})


class MobileDeviceCommandResultView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, command_id):
        serializer = MobileCommandResultSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        command = complete_device_command(request=request, command_id=command_id, data=serializer.validated_data)
        return Response(MobileCommandSerializer(command).data)


class MobileMCPExportView(APIView):
    def get(self, request, device_id):
        return Response(export_mobile_mcp(request=request, device_id=device_id))


class MobileMCPView(APIView):
    def post(self, request, device_id):
        try:
            status_code, body = handle_mobile_mcp(request=request, device_id=device_id, body=request.body)
        except json.JSONDecodeError:
            body = b'{"jsonrpc":"2.0","error":{"code":-32700,"message":"Parse error"},"id":null}'
            status_code = 400
        return HttpResponse(body, status=status_code, content_type="application/json")
