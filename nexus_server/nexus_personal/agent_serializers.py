"""Personal Agent data presentation with no financial or publishing fields."""
from rest_framework import serializers
from apps.agents.serializer_core import AgentBaseSerializer, AgentDisplayRunBaseSerializer, latest_runtime_deployment
from apps.agents.models import Agent, AgentRuntimeDeployment
from apps.common.resource_catalog import resource_access_payload


class AgentSerializer(AgentBaseSerializer):
    def get_allowed_actions(self, obj):
        request = self.context.get("request")
        if request is None:
            return []
        resource_access_payload(request=request, resource_type="agent", obj=obj)
        actions = ["view", "rename", "clone", "configure_runtime", "settings"]
        runtime = latest_runtime_deployment(obj)
        if obj.current_image_id and obj.status not in {Agent.STATUS_DISABLED, Agent.STATUS_ARCHIVED}:
            actions.append("deploy")
        if runtime is not None:
            actions.append("health_check")
            if runtime.status in {AgentRuntimeDeployment.STATUS_ACTIVE, AgentRuntimeDeployment.STATUS_DEPLOYING}:
                actions.append("stop")
        actions.extend(["enable"] if obj.status in {Agent.STATUS_DISABLED, Agent.STATUS_ARCHIVED} else ["disable", "archive"])
        if runtime is None or runtime.status not in {AgentRuntimeDeployment.STATUS_ACTIVE, AgentRuntimeDeployment.STATUS_DEPLOYING}:
            actions.append("delete")
        return actions


class AgentDisplayRunSerializer(AgentDisplayRunBaseSerializer):
    turn_index = serializers.SerializerMethodField()

    class Meta(AgentDisplayRunBaseSerializer.Meta):
        fields = [*AgentDisplayRunBaseSerializer.Meta.fields, "turn_index"]

    def get_turn_index(self, obj):
        if hasattr(obj, "_obs_turn_index"):
            return obj._obs_turn_index
        return obj.runtime_invocations.order_by("-turn_index", "-created_at").values_list("turn_index", flat=True).first()


class AgentVisibilitySerializer(serializers.Serializer):
    visibility = serializers.ChoiceField(choices=["private"])
