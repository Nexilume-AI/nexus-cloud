from rest_framework import exceptions, status
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from .callback_views import bearer_token
from .services import ingest_agui_event, public_display_event


class InternalDisplayEventIngestView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, run_id):
        token = (
            request.headers.get("X-Nexus-AGUI-Token")
            or request.headers.get("X-Nexus-Display-Token")
            or bearer_token(request.headers.get("Authorization", ""))
        )
        event_body = request.data.copy()
        visibility = str(event_body.pop("visibility", "public"))
        if "type" not in event_body and "event_type" in event_body:
            event_body["type"] = event_body.pop("event_type")
        try:
            event = ingest_agui_event(
                run_id=str(run_id),
                token=token,
                event=dict(event_body),
                visibility=visibility,
                client_event_id=request.headers.get("X-Nexus-AGUI-Event-Id"),
            )
        except exceptions.ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
        return Response(public_display_event(event), status=status.HTTP_201_CREATED)
