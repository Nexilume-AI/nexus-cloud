"""Caller-bound Display token issuance, independent of commercial views."""
from rest_framework.response import Response
from rest_framework.views import APIView
from .services import issue_private_display_token


class PrivateAgentRunDisplayTokenView(APIView):
    def post(self, request, run_id):
        run, token = issue_private_display_token(request=request, run_id=str(run_id))
        return Response(
            {
                "run_id": str(run.id),
                "display_token": token,
                "expires_at": run.display_token_expires_at,
            }
        )
