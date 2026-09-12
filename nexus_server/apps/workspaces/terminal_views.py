from __future__ import annotations
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from .serializers import WorkspaceTerminalSessionSerializer, WorkspaceTerminalSessionCreateSerializer
from .execution import (
    list_terminal_sessions, get_terminal_session, create_terminal_session,
    close_terminal_session, issue_terminal_websocket_ticket, refresh_terminal_tool_status,
)

class WorkspaceTerminalSessionListCreateView(APIView):
    def get(self, request):
        return Response(WorkspaceTerminalSessionSerializer(list_terminal_sessions(request=request), many=True).data)

    def post(self, request):
        serializer = WorkspaceTerminalSessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session, created = create_terminal_session(request=request, data=serializer.validated_data)
        response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(WorkspaceTerminalSessionSerializer(session).data, status=response_status)


class WorkspaceTerminalSessionDetailView(APIView):
    def get(self, request, session_id):
        return Response(WorkspaceTerminalSessionSerializer(get_terminal_session(request=request, session_id=session_id)).data)

    def delete(self, request, session_id):
        session = close_terminal_session(request=request, session_id=session_id)
        return Response(WorkspaceTerminalSessionSerializer(session).data)


class WorkspaceTerminalSessionCloseView(APIView):
    def post(self, request, session_id):
        session = close_terminal_session(request=request, session_id=session_id)
        return Response(WorkspaceTerminalSessionSerializer(session).data)


class WorkspaceTerminalSessionTicketView(APIView):
    def post(self, request, session_id):
        return Response(issue_terminal_websocket_ticket(request=request, session_id=str(session_id)))


class WorkspaceTerminalSessionToolStatusView(APIView):
    def post(self, request, session_id):
        session = refresh_terminal_tool_status(request=request, session_id=session_id)
        return Response(WorkspaceTerminalSessionSerializer(session).data)
