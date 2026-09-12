"""Shared credential export and invocation handlers; policy belongs to the host."""
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.gateway.serializers import ChatCompletionRequestSerializer
from apps.gateway.services import chat_completions
from .services import export_router_credentials


class RouterCredentialsExportView(APIView):
    def post(self, request, router_id):
        return Response(export_router_credentials(request=request, router_id=router_id))


class RouterInvokeView(APIView):
    def post(self, request, router_id):
        serializer = ChatCompletionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = dict(serializer.validated_data)
        payload["router_id"] = str(router_id)
        return Response(chat_completions(request=request, payload=payload))
