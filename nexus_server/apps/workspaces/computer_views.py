"""Existing Computer Runtime HTTP views, separated from Tool Setup imports."""
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from .serializers import (ComputerRuntimeEnrollSerializer, ComputerRuntimePairingCreateSerializer,
    ComputerRuntimeSessionSerializer, ComputerRuntimeUnpairSerializer)
from .computer_runtime import (create_pairing_code, create_runtime_session, enroll_runtime,
    list_computers, revoke_computer, store_runtime_upload, unpair_runtime)

class ComputerListView(APIView):
    def get(self, request):
        return Response(list_computers(request=request))


class ComputerPairingCodeView(APIView):
    def post(self, request):
        serializer = ComputerRuntimePairingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(create_pairing_code(request=request, data=serializer.validated_data), status=status.HTTP_201_CREATED)


class ComputerRevokeView(APIView):
    def post(self, request, connection_id):
        return Response(revoke_computer(request=request, connection_id=str(connection_id)))


class ComputerRuntimeEnrollView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = ComputerRuntimeEnrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(enroll_runtime(data=serializer.validated_data), status=status.HTTP_201_CREATED)


class ComputerRuntimeSessionView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = ComputerRuntimeSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(create_runtime_session(data=serializer.validated_data, request=request))


class ComputerRuntimeCommandUploadView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def put(self, request, command_id):
        authorization = str(request.META.get("HTTP_AUTHORIZATION") or "")
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        if not token:
            from .computer_runtime import ComputerRuntimeError

            raise ComputerRuntimeError("Computer Runtime upload token is required.")
        result = store_runtime_upload(
            command_id=str(command_id),
            token=token,
            content=bytes(request.body),
            content_type=str(request.META.get("CONTENT_TYPE") or "application/octet-stream"),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class ComputerRuntimeUnpairView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = ComputerRuntimeUnpairSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(unpair_runtime(ticket=serializer.validated_data["ticket"]))

