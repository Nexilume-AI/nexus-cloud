"""Owner-only management of Router capability credentials."""
from rest_framework.response import Response
from rest_framework.views import APIView
from .router_credentials import list_credentials, revoke_credential


class RouterCredentialListView(APIView):
    def get(self, request, router_id):
        return Response(list_credentials(request=request, router_id=router_id))


class RouterCredentialRevokeView(APIView):
    def post(self, request, router_id, credential_id):
        revoke_credential(request=request, router_id=router_id, credential_id=credential_id)
        return Response(status=204)
