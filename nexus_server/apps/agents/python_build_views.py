from rest_framework.response import Response
from rest_framework import serializers
from rest_framework.views import APIView

from .models import AgentPythonBuild
from .python_builds import build_configuration, build_data, enqueue_build, delete_failed_builds
from .runtime_services import get_mutable_runtime_agent


class AgentPythonBuildsView(APIView):
    def get(self, request, agent_id):
        agent = get_mutable_runtime_agent(request=request, agent_id=agent_id)
        builds = AgentPythonBuild.objects.filter(agent=agent).exclude(status="deleted").select_related("image").order_by("-created_at")[:20]
        # The timeline is capped at 20, but older failed revisions must remain
        # reachable for cleanup when the 100-revision limit has been reached.
        failed = AgentPythonBuild.objects.filter(agent=agent, status="failed", image__isnull=True).order_by("created_at", "pk")
        response = Response({"configuration": build_configuration(), "results": [build_data(build) for build in builds],
                             "failed_builds": [{"id": str(pk), "filename": filename} for pk, filename in failed.values_list("pk", "filename")[:100]]})
        response["Cache-Control"] = "private, no-store"
        return response

    def post(self, request, agent_id):
        response = Response(build_data(enqueue_build(request=request, agent_id=agent_id)), status=202)
        response["Cache-Control"] = "private, no-store"
        return response


class AgentPythonBuildDetailView(APIView):
    def delete(self, request, agent_id, build_id):
        response = Response(delete_failed_builds(request=request, agent_id=agent_id, build_ids=[build_id]))
        response["Cache-Control"] = "private, no-store"
        return response


class FailedBuildSelection(serializers.Serializer):
    build_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False, max_length=100)


class AgentPythonBuildDeleteFailedView(APIView):
    def post(self, request, agent_id):
        selection = FailedBuildSelection(data=request.data)
        selection.is_valid(raise_exception=True)
        response = Response(delete_failed_builds(request=request, agent_id=agent_id, **selection.validated_data))
        response["Cache-Control"] = "private, no-store"
        return response
