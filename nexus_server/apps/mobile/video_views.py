from rest_framework.response import Response
from rest_framework.views import APIView
from . import video


class MobileVideoStartView(APIView):
    def post(self, request, device_id):
        return Response(video.start_session(request, device_id), status=201, headers={"Cache-Control": "private, no-store"})


class MobileVideoViewerView(APIView):
    def get(self, request, session_id):
        return Response(video.poll_viewer(request, session_id, request.query_params.get("after", 0)), headers={"Cache-Control": "private, no-store"})

    def post(self, request, session_id):
        return Response(video.signal_session(request, session_id, request.data, role="viewer"), headers={"Cache-Control": "private, no-store"})


class MobileVideoDevicePollView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, device_id):
        if not isinstance(request.data, dict):
            raise video.MobileVideoError("Invalid video poll.")
        return Response(video.poll_device(request, device_id, request.data.get("session_id"), request.data.get("after", 0)), headers={"Cache-Control": "private, no-store"})


class MobileVideoDeviceSignalView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, session_id):
        return Response(video.signal_session(request, session_id, request.data, role="device"), headers={"Cache-Control": "private, no-store"})
