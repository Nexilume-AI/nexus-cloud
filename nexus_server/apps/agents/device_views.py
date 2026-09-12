"""Caller-owned Agent device grants, attachments and Run Computer operations."""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from .serializers import (
    AgentComputerBindingSerializer, AgentComputerBindingCreateSerializer,
    AgentWorkspaceGrantSerializer, AgentWorkspaceGrantSetSerializer,
    AgentMobileBindingSerializer, AgentMobileBindingCreateSerializer,
    AgentMobileBindingUpdateSerializer, AgentMobileGrantSerializer, AgentMobileGrantSetSerializer,
)
from .services import (
    list_computer_bindings, create_computer_binding, delete_computer_binding,
    private_display_payload, private_run_computer_directories,
    update_private_run_workspace_cwd, issue_private_terminal_ticket,
)
from .workspace_grants import (
    grant_agent_for_request, get_workspace_grant, set_workspace_grant, revoke_workspace_grant,
)
from .mobile_access import (
    list_mobile_bindings, create_mobile_binding, update_mobile_binding, delete_mobile_binding,
    published_mobile_declaration, get_mobile_grant, set_mobile_grant, revoke_mobile_grant,
)


class AgentComputerBindingView(APIView):
    def get(self, request, agent_id):
        bindings = list_computer_bindings(request=request, agent_id=str(agent_id))
        return Response(AgentComputerBindingSerializer(bindings, many=True).data)

    def post(self, request, agent_id):
        serializer = AgentComputerBindingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        binding = create_computer_binding(
            request=request,
            agent_id=str(agent_id),
            connection_id=str(serializer.validated_data["connection_id"]),
            is_default=serializer.validated_data.get("is_default", True),
        )
        return Response(AgentComputerBindingSerializer(binding).data, status=status.HTTP_201_CREATED)


class AgentComputerBindingDetailView(APIView):
    def delete(self, request, agent_id, binding_id):
        delete_computer_binding(request=request, agent_id=str(agent_id), binding_id=str(binding_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


class AgentWorkspaceGrantView(APIView):
    def get(self, request, agent_id):
        agent = grant_agent_for_request(request=request, agent_id=str(agent_id))
        grant = get_workspace_grant(request=request, agent=agent)
        if grant is None:
            return Response({
                "agent_id": str(agent.id),
                "declared_scopes": list(agent.workspace_capabilities or []),
                "scopes": [],
                "status": "not_granted",
            })
        data = AgentWorkspaceGrantSerializer(grant).data
        data["declared_scopes"] = list(agent.workspace_capabilities or [])
        return Response(data)

    def put(self, request, agent_id):
        serializer = AgentWorkspaceGrantSetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agent = grant_agent_for_request(request=request, agent_id=str(agent_id))
        grant = set_workspace_grant(
            request=request,
            agent=agent,
            scopes=serializer.validated_data["scopes"],
        )
        data = AgentWorkspaceGrantSerializer(grant).data
        data["declared_scopes"] = list(agent.workspace_capabilities or [])
        return Response(data)

    def delete(self, request, agent_id):
        agent = grant_agent_for_request(request=request, agent_id=str(agent_id))
        revoke_workspace_grant(request=request, agent=agent)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AgentMobileBindingView(APIView):
    def get(self, request, agent_id):
        bindings = list_mobile_bindings(request=request, agent_id=str(agent_id))
        return Response(AgentMobileBindingSerializer(bindings, many=True).data)

    def post(self, request, agent_id):
        serializer = AgentMobileBindingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        binding = create_mobile_binding(
            request=request,
            agent_id=str(agent_id),
            device_id=str(serializer.validated_data["device_id"]),
            is_default=serializer.validated_data.get("is_default", True),
        )
        return Response(AgentMobileBindingSerializer(binding).data, status=status.HTTP_201_CREATED)


class AgentMobileBindingDetailView(APIView):
    def patch(self, request, agent_id, binding_id):
        serializer = AgentMobileBindingUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        binding = update_mobile_binding(
            request=request,
            agent_id=str(agent_id),
            binding_id=str(binding_id),
            is_default=serializer.validated_data["is_default"],
        )
        return Response(AgentMobileBindingSerializer(binding).data)

    def delete(self, request, agent_id, binding_id):
        delete_mobile_binding(request=request, agent_id=str(agent_id), binding_id=str(binding_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


class AgentMobileGrantView(APIView):
    def get(self, request, agent_id):
        agent = grant_agent_for_request(request=request, agent_id=str(agent_id))
        _, declared_scopes = published_mobile_declaration(agent)
        grant = get_mobile_grant(request=request, agent=agent)
        if grant is None:
            return Response({
                "agent_id": str(agent.id),
                "declared_scopes": declared_scopes,
                "scopes": [],
                "status": "not_granted",
            })
        data = AgentMobileGrantSerializer(grant).data
        data["declared_scopes"] = declared_scopes
        return Response(data)

    def put(self, request, agent_id):
        serializer = AgentMobileGrantSetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agent = grant_agent_for_request(request=request, agent_id=str(agent_id))
        grant = set_mobile_grant(request=request, agent=agent, scopes=serializer.validated_data["scopes"])
        _, declared_scopes = published_mobile_declaration(agent)
        data = AgentMobileGrantSerializer(grant).data
        data["declared_scopes"] = declared_scopes
        return Response(data)

    def delete(self, request, agent_id):
        agent = grant_agent_for_request(request=request, agent_id=str(agent_id))
        revoke_mobile_grant(request=request, agent=agent)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PrivateAgentRunComputerView(APIView):
    def post(self, request, run_id):
        from rest_framework import serializers
        from .computer_switching import switch_private_run_computer
        class SwitchInput(serializers.Serializer):
            connection_id = serializers.UUIDField()
            expected_revision = serializers.IntegerField(min_value=0)
        data = SwitchInput(data=request.data)
        data.is_valid(raise_exception=True)
        run = switch_private_run_computer(request=request, run_id=str(run_id), **data.validated_data)
        return Response(private_display_payload(run=run))

    def get(self, request, run_id):
        return Response(
            private_run_computer_directories(
                request=request,
                run_id=str(run_id),
                path=str(request.query_params.get("path") or ""),
            )
        )

    def patch(self, request, run_id):
        return Response(
            update_private_run_workspace_cwd(
                request=request,
                run_id=str(run_id),
                path=str(request.data.get("path") or "."),
            )
        )


class PrivateAgentRunTerminalTicketView(APIView):
    def post(self, request, run_id):
        return Response(issue_private_terminal_ticket(request=request, run_id=str(run_id)))
