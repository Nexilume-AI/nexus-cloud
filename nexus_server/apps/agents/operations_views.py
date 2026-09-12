"""Owned Agent operational history and artifact management."""
from copy import copy
from rest_framework.response import Response
from rest_framework.views import APIView
from .serializers import (
    AgentSerializer, AgentDeploymentSerializer, AgentDisplayRunSerializer,
    AgentLogSerializer, AgentOutputArtifactSerializer,
)
from .services import (
    get_logs, get_status, list_display_runs, list_observability_events,
    public_display_event, redact_display_run, list_output_artifacts, scan_output_artifact, export_mcp,
)


def operational_display_event(event):
    # Keep caller Private Display and immutable original evidence unchanged.
    # Operational history uses the saved redaction plus current secret rules.
    from apps.common.redaction import redact_payload
    projected = copy(event)
    projected.payload_json, _ = redact_payload(event.redacted_payload_json or event.payload_json)
    return public_display_event(projected)


class AgentLogsView(APIView):
    def get(self, request, agent_id):
        tail = int(request.query_params.get("tail") or 100)
        return Response(AgentLogSerializer(get_logs(request=request, agent_id=agent_id, tail=tail), many=True).data)


class AgentStatusView(APIView):
    def get(self, request, agent_id):
        result = get_status(request=request, agent_id=agent_id)
        return Response(
            {
                "agent": AgentSerializer(result["agent"], context={"request": request}).data,
                "deployments": AgentDeploymentSerializer(result["deployments"], many=True).data,
            }
        )


class AgentDisplayRunView(APIView):
    def get(self, request, agent_id):
        return Response(AgentDisplayRunSerializer(list_display_runs(request=request, agent_id=agent_id), many=True).data)


class AgentDisplayRunEventsView(APIView):
    def get(self, request, agent_id, run_id):
        events = list_observability_events(
            request=request,
            agent_id=str(agent_id),
            run_id=str(run_id),
            cursor=int(request.query_params.get("cursor") or 0),
            limit=int(request.query_params.get("limit") or 200),
        )
        return Response([operational_display_event(event) for event in events])


class AgentDisplayRunRedactView(APIView):
    def post(self, request, agent_id, run_id):
        run = redact_display_run(request=request, agent_id=agent_id, run_id=str(run_id))
        return Response(AgentDisplayRunSerializer(run).data)


class AgentOutputArtifactView(APIView):
    def get(self, request, agent_id, run_id):
        return Response(AgentOutputArtifactSerializer(list_output_artifacts(request=request, agent_id=agent_id, run_id=str(run_id)), many=True).data)


class AgentOutputArtifactScanView(APIView):
    def post(self, request, agent_id, run_id, artifact_id):
        artifact = scan_output_artifact(request=request, agent_id=agent_id, run_id=str(run_id), artifact_id=str(artifact_id))
        return Response(AgentOutputArtifactSerializer(artifact).data)


class AgentMCPExportView(APIView):
    def get(self, request, agent_id):
        return Response(export_mcp(request=request, agent_id=agent_id))
