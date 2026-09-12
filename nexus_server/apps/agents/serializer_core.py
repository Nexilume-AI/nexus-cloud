"""Shared Agent serializer data and validation, without commercial presentation."""
from __future__ import annotations
from rest_framework import serializers
from apps.common.models import SoftDeleteModel
from apps.common.resource_catalog import ResourceCatalogListSerializer, ResourceOwnershipInputSerializer, resource_context_payload
from apps.agents.models import (Agent, AgentComputerBinding, AgentDeployment, AgentDisplayRun,
    AgentEndpoint, AgentLog, AgentMemoryItem, AgentMobileBinding, AgentMobileGrant,
    AgentOutputArtifact, AgentResourceConfig, AgentRuntimeDeployment, AgentRuntimeInvocation,
    AgentVersion, AgentWorkspaceGrant, EdgeAgentRegistration)
from .tool_catalog import effective_mcp_tools
from .validators import validate_agent_name
from .device_contracts import WORKSPACE_CAPABILITIES, normalize_workspace_capabilities, MOBILE_CAPABILITIES, normalize_mobile_capabilities

class AgentBaseSerializer(serializers.ModelSerializer):
    catalog_resource_type = "agent"
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    project_id = serializers.UUIDField(source="project.id", read_only=True, allow_null=True)
    ownership = serializers.SerializerMethodField()
    access = serializers.SerializerMethodField()
    version_count = serializers.SerializerMethodField()
    latest_version_status = serializers.SerializerMethodField()
    deployment_count = serializers.SerializerMethodField()
    active_deployment_count = serializers.SerializerMethodField()
    runtime_deployment_count = serializers.SerializerMethodField()
    active_runtime_deployment_count = serializers.SerializerMethodField()
    runtime_status = serializers.SerializerMethodField()
    runtime_health_status = serializers.SerializerMethodField()
    runtime_kind = serializers.SerializerMethodField()
    edge_router_id = serializers.SerializerMethodField()
    edge_binding_mode = serializers.SerializerMethodField()
    edge_endpoint_url = serializers.SerializerMethodField()
    current_image_id = serializers.SerializerMethodField()
    current_image_ref = serializers.SerializerMethodField()
    current_image_digest = serializers.SerializerMethodField()
    current_image_version = serializers.SerializerMethodField()
    deployed_image_id = serializers.SerializerMethodField()
    deployed_image_ref = serializers.SerializerMethodField()
    configuration_drift = serializers.SerializerMethodField()
    lifecycle_status = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()
    computer_declared_by_sdk = serializers.SerializerMethodField()
    mobile_sdk_requirement = serializers.SerializerMethodField()
    mobile_sdk_capabilities = serializers.SerializerMethodField()
    mobile_can_restore_sdk = serializers.SerializerMethodField()

    class Meta:
        model = Agent
        list_serializer_class = ResourceCatalogListSerializer
        fields = [
            "id",
            "tenant_id",
            "project_id",
            "ownership",
            "access",
            "name",
            "status",
            "visibility",
            "computer_requirement",
            "workspace_capabilities",
            "computer_declared_by_sdk",
            "mobile_requirement",
            "mobile_capabilities",
            "mobile_policy_source",
            "mobile_sdk_requirement",
            "mobile_sdk_capabilities",
            "mobile_can_restore_sdk",
            "repo_metadata",
            "current_version",
            "version_count",
            "latest_version_status",
            "deployment_count",
            "active_deployment_count",
            "runtime_deployment_count",
            "active_runtime_deployment_count",
            "runtime_status",
            "runtime_health_status",
            "runtime_kind",
            "edge_router_id",
            "edge_binding_mode",
            "edge_endpoint_url",
            "current_image_id",
            "current_image_ref",
            "current_image_digest",
            "current_image_version",
            "deployed_image_id",
            "deployed_image_ref",
            "configuration_drift",
            "lifecycle_status",
            "allowed_actions",
            "created_at",
            "updated_at",
        ]

    def get_version_count(self, obj) -> int:
        return len(active_related_items(obj, "versions"))

    def get_latest_version_status(self, obj) -> str:
        versions = sorted(active_related_items(obj, "versions"), key=lambda item: item.created_at, reverse=True)
        return versions[0].status if versions else "not_published"

    def get_ownership(self, obj: Agent) -> dict:
        return resource_context_payload(request=self.context.get("request"), resource_type="agent", obj=obj)["ownership"]

    def get_access(self, obj: Agent) -> dict:
        return resource_context_payload(request=self.context.get("request"), resource_type="agent", obj=obj)["access"]



    def get_deployment_count(self, obj) -> int:
        return len(active_related_items(obj, "deployments"))

    def get_active_deployment_count(self, obj) -> int:
        return len([item for item in active_related_items(obj, "deployments") if item.status == AgentDeployment.STATUS_ACTIVE])

    def get_runtime_deployment_count(self, obj) -> int:
        return len(active_related_items(obj, "runtime_deployments"))

    def get_active_runtime_deployment_count(self, obj) -> int:
        return len([
            item
            for item in active_related_items(obj, "runtime_deployments")
            if item.effective_status() == AgentRuntimeDeployment.STATUS_ACTIVE
        ])

    def get_runtime_status(self, obj) -> str:
        deployments = active_related_items(obj, "runtime_deployments")
        active = [
            item
            for item in deployments
            if item.effective_status() == AgentRuntimeDeployment.STATUS_ACTIVE
        ]
        if active:
            return AgentRuntimeDeployment.STATUS_ACTIVE
        deployments = sorted(deployments, key=lambda item: item.updated_at, reverse=True)
        return deployments[0].effective_status() if deployments else "not_deployed"

    def get_runtime_health_status(self, obj) -> str:
        deployment = latest_runtime_deployment(obj)
        return deployment.effective_health_status() if deployment else AgentRuntimeDeployment.HEALTH_UNKNOWN

    def get_runtime_kind(self, obj) -> str:
        deployment = latest_runtime_deployment(obj)
        return deployment.runtime_kind if deployment else ""

    def get_edge_router_id(self, obj) -> str:
        deployment = latest_runtime_deployment(obj)
        if not deployment or not deployment.edge_registration_id:
            return ""
        return deployment.edge_registration.node.router_id

    def get_edge_binding_mode(self, obj) -> str:
        deployment = latest_runtime_deployment(obj)
        if not deployment or not deployment.edge_registration_id:
            return ""
        return deployment.edge_registration.binding_mode

    def get_edge_endpoint_url(self, obj) -> str:
        deployment = latest_runtime_deployment(obj)
        if not deployment or not deployment.edge_registration_id:
            return ""
        return deployment.edge_registration.endpoint_url

    def get_current_image_id(self, obj) -> str:
        image = current_agent_image(obj)
        return str(image.id) if image else ""

    def get_current_image_ref(self, obj) -> str:
        image = current_agent_image(obj)
        return image.image_ref if image else ""

    def get_current_image_digest(self, obj) -> str:
        image = current_agent_image(obj)
        return image.image_digest if image else ""

    def get_current_image_version(self, obj) -> str:
        image = current_agent_image(obj)
        if not image:
            return ""
        return image.version.version if image.version else ""

    def get_deployed_image_id(self, obj) -> str:
        deployment = latest_runtime_deployment(obj)
        return str(deployment.image_id) if deployment and deployment.image_id else ""

    def get_deployed_image_ref(self, obj) -> str:
        deployment = latest_runtime_deployment(obj)
        return deployment.image.image_ref if deployment and deployment.image_id else ""

    def get_configuration_drift(self, obj) -> bool:
        deployment = latest_runtime_deployment(obj)
        if deployment is None or not deployment.image_id or not obj.current_image_id:
            return False
        return str(deployment.image_id) != str(obj.current_image_id)

    def get_lifecycle_status(self, obj) -> str:
        if obj.status in {Agent.STATUS_DISABLED, Agent.STATUS_ARCHIVED}:
            return obj.status
        if not configured_runtime(obj):
            return "draft"
        return "configured"


    def get_computer_declared_by_sdk(self, obj) -> bool:
        registration = getattr(obj, "edge_registration", None)
        return bool(
            registration is not None
            and registration.binding_mode == EdgeAgentRegistration.BINDING_MANAGED
            and registration.computer_requirement
        )

    def _mobile_sdk_declaration(self, obj) -> tuple[str, list[str]] | None:
        registration = getattr(obj, "edge_registration", None)
        if (
            registration is None
            or registration.binding_mode != EdgeAgentRegistration.BINDING_MANAGED
        ):
            return None
        from .device_contracts import sdk_mobile_declaration

        return sdk_mobile_declaration(registration)

    def get_mobile_sdk_requirement(self, obj) -> str | None:
        declaration = self._mobile_sdk_declaration(obj)
        return declaration[0] if declaration else None

    def get_mobile_sdk_capabilities(self, obj) -> list[str]:
        declaration = self._mobile_sdk_declaration(obj)
        return declaration[1] if declaration else []

    def get_mobile_can_restore_sdk(self, obj) -> bool:
        return self._mobile_sdk_declaration(obj) is not None


    def to_representation(self, instance):
        payload = super().to_representation(instance)
        if self.context.get("request") is None or payload["access"]["can_read"]:
            return payload
        payload.update({
            "status": "access_required",
            "allowed_actions": [],
            "repo_metadata": {},
            "workspace_capabilities": [],
            "mobile_capabilities": [],
            "edge_router_id": "",
            "edge_endpoint_url": "",
            "current_image_ref": "",
            "current_image_digest": "",
            "deployed_image_ref": "",
            "runtime_health_status": "unknown",
        })
        return payload


def active_related_items(obj, related_name: str) -> list:
    manager = getattr(obj, related_name)
    items = list(manager.all())
    return [item for item in items if item.status != "deleted"]


def current_agent_image(obj):
    image = getattr(obj, "current_image", None)
    if image and image.status != "deleted":
        return image
    images = sorted(active_related_items(obj, "runtime_images"), key=lambda item: item.created_at, reverse=True)
    return images[0] if images else None


def configured_runtime(obj) -> bool:
    deployment = latest_runtime_deployment(obj)
    if deployment and deployment.runtime_kind in {
        AgentRuntimeDeployment.RUNTIME_OPENWRT_IPV6,
        AgentRuntimeDeployment.RUNTIME_OPENWRT_RELAY,
    }:
        return bool(deployment.edge_registration_id)
    return current_agent_image(obj) is not None


class AgentCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)

    def validate_name(self, value: str) -> str:
        return validate_agent_name(value)

    ownership = ResourceOwnershipInputSerializer(required=False)


class AgentUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    status = serializers.ChoiceField(
        choices=[Agent.STATUS_ACTIVE, Agent.STATUS_DISABLED, Agent.STATUS_ARCHIVED],
        required=False,
    )
    project_id = serializers.UUIDField(required=False, allow_null=True)
    team_id = serializers.UUIDField(required=False, allow_null=True)
    computer_requirement = serializers.ChoiceField(choices=Agent.COMPUTER_REQUIREMENT_CHOICES, required=False)
    workspace_capabilities = serializers.ListField(
        child=serializers.ChoiceField(choices=WORKSPACE_CAPABILITIES),
        required=False,
        allow_empty=True,
    )
    mobile_requirement = serializers.ChoiceField(choices=Agent.MOBILE_REQUIREMENT_CHOICES, required=False)
    mobile_capabilities = serializers.ListField(
        child=serializers.ChoiceField(choices=MOBILE_CAPABILITIES),
        required=False,
        allow_empty=True,
    )
    mobile_policy_source = serializers.ChoiceField(
        choices=Agent.MOBILE_POLICY_SOURCE_CHOICES,
        required=False,
    )

    def validate_name(self, value: str) -> str:
        return validate_agent_name(value)

    def validate_workspace_capabilities(self, value):
        return normalize_workspace_capabilities(value)

    def validate_mobile_capabilities(self, value):
        return normalize_mobile_capabilities(value)


class AgentCloneSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def validate_name(self, value: str) -> str:
        return validate_agent_name(value) if value else value


class AgentVersionPublishSerializer(serializers.Serializer):
    release_notes = serializers.CharField(max_length=20_000, required=False, allow_blank=True, default="")


class AgentComputerBindingSerializer(serializers.ModelSerializer):
    agent_id = serializers.UUIDField(source="agent.id", read_only=True)
    connection_id = serializers.UUIDField(source="connection.id", read_only=True)
    connection_name = serializers.CharField(source="connection.name", read_only=True)

    class Meta:
        model = AgentComputerBinding
        fields = [
            "id",
            "agent_id",
            "connection_id",
            "connection_name",
            "is_default",
            "status",
            "last_used_at",
            "created_at",
            "updated_at",
        ]


class AgentComputerBindingCreateSerializer(serializers.Serializer):
    connection_id = serializers.UUIDField()
    is_default = serializers.BooleanField(default=True, required=False)


class AgentMobileBindingSerializer(serializers.ModelSerializer):
    agent_id = serializers.UUIDField(source="agent.id", read_only=True)
    device_id = serializers.UUIDField(source="device.id", read_only=True)
    device_name = serializers.CharField(source="device.name", read_only=True)
    device_status = serializers.CharField(source="device.lifecycle_status", read_only=True)

    class Meta:
        model = AgentMobileBinding
        fields = [
            "id",
            "agent_id",
            "device_id",
            "device_name",
            "device_status",
            "is_default",
            "status",
            "last_used_at",
            "created_at",
            "updated_at",
        ]


class AgentMobileBindingCreateSerializer(serializers.Serializer):
    device_id = serializers.UUIDField()
    is_default = serializers.BooleanField(default=True, required=False)


class AgentMobileBindingUpdateSerializer(serializers.Serializer):
    is_default = serializers.BooleanField()


class AgentMobileGrantSerializer(serializers.ModelSerializer):
    agent_id = serializers.UUIDField(source="agent.id", read_only=True)
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    project_id = serializers.UUIDField(source="project.id", read_only=True, allow_null=True)

    class Meta:
        model = AgentMobileGrant
        fields = [
            "id",
            "agent_id",
            "tenant_id",
            "project_id",
            "scopes",
            "status",
            "granted_at",
            "revoked_at",
            "updated_at",
        ]


class AgentMobileGrantSetSerializer(serializers.Serializer):
    scopes = serializers.ListField(
        child=serializers.ChoiceField(choices=MOBILE_CAPABILITIES),
        allow_empty=True,
    )

    def validate_scopes(self, value):
        return normalize_mobile_capabilities(value)


class AgentWorkspaceGrantSerializer(serializers.ModelSerializer):
    agent_id = serializers.UUIDField(source="agent.id", read_only=True)
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    project_id = serializers.UUIDField(source="project.id", read_only=True, allow_null=True)

    class Meta:
        model = AgentWorkspaceGrant
        fields = [
            "id",
            "agent_id",
            "tenant_id",
            "project_id",
            "scopes",
            "status",
            "granted_at",
            "revoked_at",
            "updated_at",
        ]


class AgentWorkspaceGrantSetSerializer(serializers.Serializer):
    scopes = serializers.ListField(
        child=serializers.ChoiceField(choices=WORKSPACE_CAPABILITIES),
        allow_empty=True,
    )

    def validate_scopes(self, value):
        return normalize_workspace_capabilities(value)


class AgentVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentVersion
        fields = [
            "id",
            "version",
            "commit_id",
            "artifact_metadata",
            "workspace_capabilities",
            "mobile_requirement",
            "mobile_capabilities",
            "status",
            "created_at",
            "updated_at",
        ]


class AgentDeploymentSerializer(serializers.ModelSerializer):
    agent_id = serializers.UUIDField(source="agent.id", read_only=True)
    version = serializers.CharField(source="version.version", read_only=True, allow_null=True)

    class Meta:
        model = AgentDeployment
        fields = ["id", "agent_id", "version", "env", "status", "endpoint_url", "created_at", "updated_at"]


class AgentDeploySerializer(serializers.Serializer):
    env = serializers.CharField(max_length=64, default="prod")


class AgentLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentLog
        fields = ["id", "level", "message", "created_at"]


class AgentDisplayRunBaseSerializer(serializers.ModelSerializer):
    agent_id = serializers.UUIDField(read_only=True)
    runtime_id = serializers.UUIDField(read_only=True, allow_null=True)
    latest_seq = serializers.SerializerMethodField()
    caller = serializers.SerializerMethodField()
    computer_status = serializers.SerializerMethodField()
    mobile_status = serializers.SerializerMethodField()

    class Meta:
        model = AgentDisplayRun
        fields = [
            "id",
            "agent_id",
            "runtime_id",
            "run_kind",
            "status",
            "redaction_status",
            "redaction_metadata",
            "title",
            "started_at",
            "completed_at",
            "latest_seq",
            "caller",
            "computer_status",
            "mobile_status",
            "created_at",
            "updated_at",
        ]

    def get_latest_seq(self, obj) -> int:
        if hasattr(obj, "_obs_latest_seq"):
            return obj._obs_latest_seq or 0
        return obj.events.order_by("-seq").values_list("seq", flat=True).first() or 0

    def get_caller(self, obj) -> str:
        if not obj.caller_subject_hash:
            return "legacy"
        return f"{obj.caller_principal_type or 'caller'}:{obj.caller_subject_hash[:10]}"

    def get_computer_status(self, obj) -> str:
        if not obj.computer_binding_id:
            return "unavailable"
        if hasattr(obj, "_obs_terminal_status"):
            return obj._obs_terminal_status or "attached"
        session = getattr(obj, "terminal_session", None)
        return session.status if session else "attached"

    def get_mobile_status(self, obj) -> str:
        if not obj.mobile_binding_id:
            return "unavailable"
        failed = obj._obs_mobile_failed if hasattr(obj, "_obs_mobile_failed") else any(command.status == "failed" for command in obj.mobile_commands.all())
        if failed:
            return "failed"
        if (
            obj.mobile_binding.status != SoftDeleteModel.STATUS_ACTIVE
            or obj.mobile_binding.device.lifecycle_status != "online"
        ):
            return "unavailable"
        return "attached"



class AgentOutputArtifactSerializer(serializers.ModelSerializer):
    agent_id = serializers.UUIDField(read_only=True)
    run_id = serializers.UUIDField(read_only=True)
    runtime_id = serializers.UUIDField(read_only=True, allow_null=True)
    source_event_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = AgentOutputArtifact
        fields = [
            "id",
            "agent_id",
            "run_id",
            "turn_index",
            "runtime_id",
            "source_event_id",
            "workspace_path",
            "computer_revision",
            "original_file_name",
            "content_type",
            "size_bytes",
            "sha256",
            "producer_step",
            "license_status",
            "scan_status",
            "policy_status",
            "scan_metadata",
            "snapshot_status",
            "status",
            "created_at",
            "updated_at",
        ]


class AgentMemoryItemSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    project_id = serializers.UUIDField(source="project.id", read_only=True, allow_null=True)
    agent_id = serializers.UUIDField(source="agent.id", read_only=True)
    source_run_id = serializers.UUIDField(source="source_run.id", read_only=True, allow_null=True)

    class Meta:
        model = AgentMemoryItem
        fields = [
            "id",
            "tenant_id",
            "project_id",
            "agent_id",
            "memory_type",
            "scope",
            "content_text",
            "content_json",
            "source_run_id",
            "source_event_ids",
            "revision",
            "confidence",
            "sensitivity_level",
            "consent_status",
            "license_status",
            "status",
            "created_at",
            "updated_at",
        ]


class AgentMemoryItemCreateSerializer(serializers.Serializer):
    memory_type = serializers.ChoiceField(choices=AgentMemoryItem.TYPE_CHOICES, default=AgentMemoryItem.TYPE_FACT)
    scope = serializers.ChoiceField(choices=AgentMemoryItem.SCOPE_CHOICES, default=AgentMemoryItem.SCOPE_DEVELOPER_ONLY)
    content_text = serializers.CharField(required=False, allow_blank=True, default="")
    content_json = serializers.JSONField(required=False, default=dict)
    source_run_id = serializers.UUIDField(required=False, allow_null=True)
    source_event_ids = serializers.ListField(child=serializers.UUIDField(), required=False, default=list)
    confidence = serializers.DecimalField(max_digits=5, decimal_places=4, required=False, default="1.0000")
    sensitivity_level = serializers.ChoiceField(
        choices=AgentMemoryItem.SENSITIVITY_CHOICES,
        default=AgentMemoryItem.SENSITIVITY_INTERNAL,
    )
    consent_status = serializers.ChoiceField(choices=AgentMemoryItem.CONSENT_CHOICES, default=AgentMemoryItem.CONSENT_PENDING)
    license_status = serializers.ChoiceField(choices=AgentMemoryItem.LICENSE_CHOICES, default=AgentMemoryItem.LICENSE_UNKNOWN)

    def validate(self, attrs):
        if not str(attrs.get("content_text") or "").strip() and not attrs.get("content_json"):
            raise serializers.ValidationError("content_text or content_json is required.")
        return attrs


class AgentResourceConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentResourceConfig
        fields = ["id", "cpu", "memory", "status", "created_at", "updated_at"]


class AgentResourceSetSerializer(serializers.Serializer):
    cpu = serializers.CharField(max_length=32)
    memory = serializers.CharField(max_length=32)


class AgentEndpointSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentEndpoint
        fields = ["id", "env", "url", "status", "created_at", "updated_at"]


def latest_runtime_deployment(obj) -> AgentRuntimeDeployment | None:
    deployments = sorted(
        [item for item in (obj._catalog_runtimes if hasattr(obj, "_catalog_runtimes") else obj.runtime_deployments.all()) if item.status != "deleted"],
        key=lambda item: item.updated_at,
        reverse=True,
    )
    active = [
        item
        for item in deployments
        if item.effective_status() == AgentRuntimeDeployment.STATUS_ACTIVE
    ]
    return active[0] if active else (deployments[0] if deployments else None)

