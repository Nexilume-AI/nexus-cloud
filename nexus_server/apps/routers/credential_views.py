"""Shared credential export and invocation handlers; policy belongs to the host."""
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.gateway.serializers import ChatCompletionRequestSerializer
from apps.gateway.services import chat_completions
from .services import export_router_credentials
from .managed_credentials import api_access


class RouterAPIAccessView(APIView):
    def get(self, request, router_id):
        return Response(api_access(request=request, router_id=router_id), headers={"Cache-Control": "no-store"})

    def post(self, request, router_id):
        return Response(api_access(request=request, router_id=router_id,
                                   action=request.data.get("action", "ensure"),
                                   expected_id=request.data.get("expected_id"),
                                   confirmed=request.data.get("confirm_rotation") is True),
                        headers={"Cache-Control": "no-store"})


class RouterCredentialsExportView(APIView):
    def post(self, request, router_id):
        return Response(export_router_credentials(request=request, router_id=router_id),
                        headers={"Cache-Control": "no-store"})


class RouterInvokeView(APIView):
    def post(self, request, router_id):
        serializer = ChatCompletionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = dict(serializer.validated_data)
        payload["router_id"] = str(router_id)
        return Response(chat_completions(request=request, payload=payload))
