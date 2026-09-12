"""Shared Router trace HTTP handler; presentation selected by the host."""
from rest_framework.views import APIView
from rest_framework.response import Response
from .services import list_router_traces


class RouterTraceListView(APIView):
    def get(self, request, router_id):
        return Response(
            list_router_traces(
                request=request,
                router_id=str(router_id),
                limit=request.query_params.get("limit", 20),
                cursor=request.query_params.get("cursor", ""),
            )
        )
