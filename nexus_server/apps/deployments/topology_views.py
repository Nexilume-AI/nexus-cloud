"""Read-only topology HTTP surface, independently composable by either host."""
from rest_framework.response import Response
from rest_framework.views import APIView
from .topology import topology_payload


class TopologyView(APIView):
    def get(self, request):
        return Response(topology_payload(request=request))
