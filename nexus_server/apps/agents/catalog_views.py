"""Shared, authenticated Agent catalog and memory views.

The distribution selects services and presentation; invocation/delegate views
remain separate until their execution dependencies are independently composed.
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from .serializers import (
    AgentSerializer, AgentCreateSerializer, AgentUpdateSerializer,
    AgentCloneSerializer, AgentMemoryItemSerializer, AgentMemoryItemCreateSerializer,
)
from .services import (
    list_agents, create_agent, get_agent, update_agent, delete_agent,
    agent_capabilities, clone_agent, list_memory_items, create_memory_item,
)

class AgentListCreateView(APIView):
    def get(self, request):
        from apps.common import catalog_pagination
        from .console_catalog import filter_catalog
        agents = list_agents(request=request)
        serialize = lambda rows: AgentSerializer(rows, many=True, context={"request": request}).data
        if catalog_pagination.requested(request):
            agents, ordering = filter_catalog(agents, request)
            return Response(catalog_pagination.page(request=request, queryset=agents, serialize=serialize, ordering=ordering))
        return Response(serialize(agents))

    def post(self, request):
        serializer = AgentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agent = create_agent(request=request, **serializer.validated_data)
        return Response(AgentSerializer(agent, context={"request": request}).data, status=status.HTTP_201_CREATED)


class AgentDetailView(APIView):
    def get(self, request, agent_id):
        return Response(AgentSerializer(get_agent(request=request, agent_id=agent_id), context={"request": request}).data)

    def patch(self, request, agent_id):
        serializer = AgentUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        agent = update_agent(request=request, agent_id=agent_id, data=serializer.validated_data)
        return Response(AgentSerializer(agent, context={"request": request}).data)

    def delete(self, request, agent_id):
        delete_agent(request=request, agent_id=agent_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AgentCapabilitiesView(APIView):
    def get(self, request):
        return Response(agent_capabilities(request=request))


class AgentCloneView(APIView):
    def post(self, request, agent_id):
        serializer = AgentCloneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agent = clone_agent(
            request=request,
            agent_id=str(agent_id),
            name=serializer.validated_data.get("name", ""),
        )
        return Response(AgentSerializer(agent, context={"request": request}).data, status=status.HTTP_201_CREATED)


class AgentMemoryView(APIView):
    def get(self, request, agent_id):
        return Response(AgentMemoryItemSerializer(list_memory_items(request=request, agent_id=agent_id), many=True).data)

    def post(self, request, agent_id):
        serializer = AgentMemoryItemCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = create_memory_item(request=request, agent_id=agent_id, data=serializer.validated_data)
        return Response(AgentMemoryItemSerializer(item).data, status=status.HTTP_201_CREATED)

