from __future__ import annotations

from rest_framework import serializers

from .models import AgentRuntimeDeployment, AgentRuntimeImage, AgentRuntimeInvocation


class AgentRuntimeImageSerializer(serializers.ModelSerializer):
    usage = serializers.SerializerMethodField()
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    project_id = serializers.UUIDField(source="project.id", read_only=True, allow_null=True)
    agent_id = serializers.UUIDField(source="agent.id", read_only=True)
    version = serializers.CharField(source="version.version", read_only=True, allow_null=True)

    def get_usage(self, obj):
        from .runtime_images import image_usage, image_usage_snapshot
        snapshots = self.context.setdefault("image_usage_snapshots", {})
        if obj.agent_id not in snapshots:
            snapshots[obj.agent_id] = image_usage_snapshot(obj.agent_id)
        return image_usage(obj, snapshots[obj.agent_id])

    class Meta:
        model = AgentRuntimeImage
        fields = [
            "id",
            "tenant_id",
            "project_id",
            "agent_id",
            "version",
            "image_ref",
            "image_digest",
            "artifact_path",
            "status",
            "created_at",
            "updated_at",
            "usage",
        ]


class AgentRuntimeImageCreateSerializer(serializers.Serializer):
    image_ref = serializers.CharField(max_length=512, required=False, allow_blank=True)
    image_digest = serializers.CharField(max_length=128, required=False, allow_blank=True)
    version = serializers.CharField(max_length=32, required=False, allow_blank=True)
    registry_secret_ref = serializers.CharField(max_length=255, required=False, allow_blank=True, write_only=True)

    def validate(self, attrs):
        if not attrs.get("image_ref") and "file" not in self.initial_data:
            raise serializers.ValidationError("image_ref or file is required.")
        return attrs


class AgentRuntimeDeploymentSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    project_id = serializers.UUIDField(source="project.id", read_only=True, allow_null=True)
    agent_id = serializers.UUIDField(source="agent.id", read_only=True)
    image_id = serializers.UUIDField(source="image.id", read_only=True, allow_null=True)
    image_ref = serializers.CharField(source="image.image_ref", read_only=True, allow_null=True)
    edge_registration_id = serializers.UUIDField(source="edge_registration.id", read_only=True, allow_null=True)
    edge_router_id = serializers.CharField(source="edge_registration.node.router_id", read_only=True, allow_null=True)
    edge_endpoint_url = serializers.SerializerMethodField()
    edge_transport = serializers.CharField(source="edge_registration.transport", read_only=True, allow_null=True)
    edge_relay_id = serializers.CharField(source="edge_registration.relay_id", read_only=True, allow_null=True)
    edge_relay_router_id = serializers.CharField(source="edge_registration.relay_router_id", read_only=True, allow_null=True)
    edge_relay_assignment_id = serializers.CharField(source="edge_registration.relay_assignment_id", read_only=True, allow_null=True)
    edge_lease_expires_at = serializers.DateTimeField(source="edge_registration.lease_expires_at", read_only=True, allow_null=True)
    workspace_connection_id = serializers.UUIDField(source="workspace_connection.id", read_only=True, allow_null=True)
    status = serializers.SerializerMethodField()
    health_status = serializers.SerializerMethodField()

    class Meta:
        model = AgentRuntimeDeployment
        fields = [
            "id",
            "tenant_id",
            "project_id",
            "agent_id",
            "runtime_kind",
            "image_id",
            "image_ref",
            "edge_registration_id",
            "edge_router_id",
            "edge_endpoint_url",
            "edge_transport",
            "edge_relay_id",
            "edge_relay_router_id",
            "edge_relay_assignment_id",
            "edge_lease_expires_at",
            "env",
            "status",
            "container_id",
            "internal_mcp_url",
            "health_status",
            "last_error",
            "workspace_connection_id",
            "workspace_root",
            "workspace_access_mode",
            "created_at",
            "updated_at",
        ]

    def get_edge_endpoint_url(self, obj) -> str:
        return obj.edge_registration.endpoint_url if obj.edge_registration_id else ""

    def get_status(self, obj) -> str:
        return obj.effective_status()

    def get_health_status(self, obj) -> str:
        return obj.effective_health_status()


class AgentRuntimeDeploySerializer(serializers.Serializer):
    image_id = serializers.UUIDField(required=False)
    env = serializers.CharField(max_length=64, default="prod")
    workspace_connection_id = serializers.UUIDField(required=False, allow_null=True)
    workspace_root = serializers.CharField(max_length=1024, required=False, allow_blank=True, default="")
    workspace_access_mode = serializers.ChoiceField(
        choices=[AgentRuntimeDeployment.WORKSPACE_READ_ONLY, AgentRuntimeDeployment.WORKSPACE_READ_WRITE],
        required=False,
        default=AgentRuntimeDeployment.WORKSPACE_READ_ONLY,
    )


class AgentRuntimeStopSerializer(serializers.Serializer):
    env = serializers.CharField(max_length=64, default="prod")


class AgentRuntimeInvocationSerializer(serializers.ModelSerializer):
    agent_id = serializers.UUIDField(source="agent.id", read_only=True)
    deployment_id = serializers.UUIDField(source="deployment.id", read_only=True, allow_null=True)
    display_run_id = serializers.UUIDField(source="display_run.id", read_only=True, allow_null=True)

    class Meta:
        model = AgentRuntimeInvocation
        fields = [
            "id",
            "agent_id",
            "deployment_id",
            "display_run_id",
            "tool_name",
            "status",
            "error_code",
            "latency_ms",
            "cost",
            "currency",
            "request_id",
            "created_at",
        ]
