from __future__ import annotations

from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import AuditLogSerializer
from .services import get_audit_log, list_audit_logs


class AuditLogListView(APIView):
    def get(self, request):
        return Response(AuditLogSerializer(list_audit_logs(request=request), many=True).data)


class AuditLogDetailView(APIView):
    def get(self, request, log_id):
        return Response(AuditLogSerializer(get_audit_log(request=request, log_id=log_id)).data)
