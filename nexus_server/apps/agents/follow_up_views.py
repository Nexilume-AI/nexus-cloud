from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import ValidationError
from . import follow_ups
from .delegate_tokens import interaction_token
from .services import get_private_display_run


class PrivateRunFollowUpsView(APIView):
    def get(self, request, run_id):
        run = get_private_display_run(request=request, run_id=str(run_id))
        # Read-only recovery for a lost POST response. Never identify delivery by
        # message text: two equal instructions can be separate caller requests.
        key = request.query_params.get("idempotency_key")
        if key is not None:
            if not 1 <= len(key) <= 128:
                raise ValidationError("A valid idempotency_key is required.")
            row = run.follow_ups.filter(idempotency_key=key).first()
            return Response({"submission": follow_ups.serialize(row) if row else None})
        return Response(follow_ups.payload(run))

    def post(self, request, run_id):
        return Response(follow_ups.submit(request=request, run_id=str(run_id), data=request.data), status=202)

    def patch(self, request, run_id):
        return Response(follow_ups.edit_queue(request=request, run_id=str(run_id), data=request.data))


class PrivateRunFollowUpCancelView(APIView):
    def patch(self, request, run_id, message_id):
        return Response(follow_ups.edit_queue(request=request, run_id=str(run_id), message_id=message_id, data=request.data))

    def delete(self, request, run_id, message_id):
        return Response(follow_ups.cancel(request=request, run_id=str(run_id), message_id=message_id))


class InternalRunInboxView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, run_id):
        return Response(follow_ups.inbox(run_id=str(run_id), token=interaction_token(request), data=request.data))
