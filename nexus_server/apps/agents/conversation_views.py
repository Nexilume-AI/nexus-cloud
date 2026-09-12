"""Shared formal invocation and conversation lifecycle HTTP interfaces."""
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from .services import get_private_display_run
from .runtime_services import (
    start_agent_invocation, get_agent_interactor, list_private_runs,
    start_private_run, serialize_private_run, cancel_private_run, resume_private_run,
)


class AgentInvocationView(APIView):
    def post(self, request, agent_id):
        tool_name = str(request.data.get("tool") or "").strip()
        arguments = request.data.get("arguments", {})
        if not tool_name:
            raise exceptions.ValidationError({"tool": "This field is required."})
        if not isinstance(arguments, dict):
            raise exceptions.ValidationError({"arguments": "Must be a JSON object."})
        run = start_agent_invocation(
            request=request,
            agent_id=str(agent_id),
            tool_name=tool_name,
            arguments=arguments,
        )
        return Response(
            {
                "run_id": str(run.id),
                "display_url": f"/agent-runs/{run.id}/display",
                "status": run.status,
            },
            status=status.HTTP_202_ACCEPTED,
        )



class AgentInteractorView(APIView):
    def get(self, request, agent_id):
        return Response(get_agent_interactor(request=request, agent_id=str(agent_id)))



class AgentPrivateRunListCreateView(APIView):
    def get(self, request, agent_id):
        counts = {}
        runs, next_cursor = list_private_runs(
            request=request,
            agent_id=str(agent_id),
            query=str(request.query_params.get("q") or ""),
            limit=int(request.query_params.get("limit") or 30),
            cursor=str(request.query_params.get("cursor") or ""),
            attention=str(request.query_params.get("attention") or "all"),
            counts=counts,
        )
        return Response({
            "results": [serialize_private_run(run, include_messages=False) for run in runs],
            "next_cursor": next_cursor,
            "counts": counts,
        })

    def post(self, request, agent_id):
        content = str(request.data.get("content") or request.data.get("message") or "").strip()
        arguments = request.data.get("arguments") if "arguments" in request.data else None
        if arguments is not None and not isinstance(arguments, dict):
            raise exceptions.ValidationError({"arguments": "Arguments must be a JSON object."})
        run = start_private_run(
            request=request,
            agent_id=str(agent_id),
            content=content,
            tool_name=str(request.data.get("tool_name") or request.data.get("tool") or ""),
            arguments=arguments,
            attachments=request.data.get("attachments"),
            files=request.data.get("files"),
            audio=request.data.get("audio"),
            execution_profile_id=str(request.data.get("execution_profile_id") or ""),
            reasoning_effort=str(request.data.get("reasoning_effort") or ""),
        )
        return Response(
            {
                "run": serialize_private_run(run),
                "run_id": str(run.id),
                "display_url": f"/agents/{agent_id}/private-display?run={run.id}",
                "status": "working",
            },
            status=status.HTTP_202_ACCEPTED,
        )



class PrivateAgentRunCancelView(APIView):
    def post(self, request, run_id):
        get_private_display_run(request=request, run_id=str(run_id))
        return Response(serialize_private_run(cancel_private_run(request=request, run_id=str(run_id))))



class PrivateAgentRunRecoveryView(APIView):
    def post(self, request, run_id):
        run = get_private_display_run(request=request, run_id=str(run_id))
        from .task_execution import recovery_decision

        return Response(recovery_decision(run=run, action=str(request.data.get("action") or "")))



class PrivateAgentRunResumeView(APIView):
    def post(self, request, run_id):
        content = str(request.data.get("content") or request.data.get("message") or "").strip()
        arguments = request.data.get("arguments") if "arguments" in request.data else None
        if arguments is not None and not isinstance(arguments, dict):
            raise exceptions.ValidationError({"arguments": "Arguments must be a JSON object."})
        run = resume_private_run(
            request=request,
            run_id=str(run_id),
            content=content,
            arguments=arguments,
            attachments=request.data.get("attachments"),
            files=request.data.get("files"),
            audio=request.data.get("audio"),
            execution_profile_id=str(request.data.get("execution_profile_id") or ""),
            reasoning_effort=str(request.data.get("reasoning_effort") or ""),
        )
        return Response(
            {
                "run": serialize_private_run(run),
                "run_id": str(run.id),
                "display_url": f"/agents/{run.agent_id}/private-display?run={run.id}",
                "status": "working",
                "resumed": True,
            },
            status=status.HTTP_202_ACCEPTED,
        )

