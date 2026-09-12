from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from .tool_recovery import recover


class RecoveryRequest(serializers.Serializer):
    operation_id = serializers.UUIDField()
    expected_revision = serializers.RegexField(r"^[0-9a-f]{64}$")
    action = serializers.ChoiceField(choices=["recover", "restore_previous", "keep_local"])


class ToolRecoveryView(APIView):
    def post(self, request, session_id):
        serializer = RecoveryRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(recover(request=request, session_id=session_id, data=serializer.validated_data))
