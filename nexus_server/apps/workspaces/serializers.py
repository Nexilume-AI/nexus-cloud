from __future__ import annotations

from rest_framework import serializers
from django.utils import timezone

from .models import WorkspaceConnection, WorkspaceTerminalSession


class WorkspaceConnectionSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    project_id = serializers.UUIDField(source="project.id", read_only=True, allow_null=True)
    created_by = serializers.IntegerField(source="created_by.id", read_only=True, allow_null=True)
    runtime = serializers.SerializerMethodField()
    availability = serializers.SerializerMethodField()
    ssh_host = serializers.SerializerMethodField()
    ssh_port = serializers.SerializerMethodField()
    ssh_user = serializers.SerializerMethodField()
    auth_mode = serializers.SerializerMethodField()

    class Meta:
        model = WorkspaceConnection
        fields = [
            "id",
            "tenant_id",
            "project_id",
            "name",
            "connection_type",
            "ssh_host",
            "ssh_port",
            "ssh_user",
            "auth_mode",
            "workspace_root",
            "status",
            "last_test_status",
            "last_test_error",
            "last_test_at",
            "metadata",
            "runtime",
            "availability",
            "created_by",
            "created_at",
            "updated_at",
        ]

    def get_runtime(self, obj):
        try:
            device = obj.runtime_device
        except Exception:
            device = None
        if device is None:
            return None
        facts = device.facts if isinstance(device.facts, dict) else {}
        return {
            "device_id": str(device.id),
            "online": device.online,
            "platform": device.platform,
            "protocol_version": device.protocol_version,
            "capabilities": device.capabilities,
            "browser_available": facts.get("browser_available"),
            "browser_name": str(facts.get("browser_name") or ""),
            "last_seen_at": device.last_seen_at,
            "revoked": device.revoked_at is not None,
        }

    def get_ssh_host(self, obj):
        return ""

    def get_ssh_port(self, obj):
        return None

    def get_ssh_user(self, obj):
        return ""

    def get_auth_mode(self, obj):
        return "disabled" if obj.connection_type == WorkspaceConnection.TYPE_SSH else "device_key"

    def get_availability(self, obj):
        if obj.connection_type == WorkspaceConnection.TYPE_SSH:
            return {
                "available": False,
                "code": "LEGACY_SSH_DISABLED",
                "message": "Cloud-initiated SSH Computers are disabled. Pair Nexus Computer Runtime instead.",
            }
        runtime = self.get_runtime(obj)
        if runtime is None:
            try:
                enrollment = obj.runtime_enrollment
            except Exception:
                enrollment = None
            if enrollment is not None and enrollment.used_at is None and enrollment.expires_at <= timezone.now():
                return {
                    "available": False,
                    "code": "COMPUTER_RUNTIME_PAIRING_EXPIRED",
                    "message": "The pairing link expired. Delete this entry and create a new pairing link.",
                }
            return {
                "available": False,
                "code": "COMPUTER_RUNTIME_PAIRING",
                "message": "Waiting for Nexus Computer Runtime to finish pairing.",
            }
        if runtime["revoked"]:
            return {
                "available": False,
                "code": "COMPUTER_RUNTIME_REVOKED",
                "message": "This Computer Runtime was revoked.",
            }
        if not runtime["online"]:
            return {
                "available": False,
                "code": "COMPUTER_RUNTIME_OFFLINE",
                "message": "Start Nexus Computer Runtime on this Computer.",
            }
        return {"available": True, "code": "", "message": ""}


class ComputerRuntimePairingCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128, default="My Computer", required=False)
    project_id = serializers.UUIDField(required=False, allow_null=True)
    workspace_root = serializers.CharField(max_length=1024, default="~/.nexus", required=False)


class ComputerRuntimeEnrollSerializer(serializers.Serializer):
    pairing_code = serializers.CharField(max_length=512)
    public_key_pem = serializers.CharField(max_length=4096, trim_whitespace=False)
    name = serializers.CharField(max_length=128, required=False, allow_blank=True)
    platform = serializers.ChoiceField(choices=["windows", "linux", "macos"])
    protocol_version = serializers.IntegerField(min_value=1, max_value=1, default=1, required=False)
    capabilities = serializers.DictField(required=False)
    facts = serializers.DictField(required=False)


class ComputerRuntimeSessionSerializer(serializers.Serializer):
    device_id = serializers.UUIDField()
    challenge = serializers.CharField(max_length=256, required=False)
    signature = serializers.CharField(max_length=1024, required=False)


class ComputerRuntimeUnpairSerializer(serializers.Serializer):
    ticket = serializers.CharField(max_length=1024)


class WorkspaceConnectionCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128)
    connection_type = serializers.ChoiceField(choices=[WorkspaceConnection.TYPE_SSH], default=WorkspaceConnection.TYPE_SSH, required=False)
    ssh_host = serializers.CharField(max_length=255)
    ssh_port = serializers.IntegerField(min_value=1, max_value=65535, default=22, required=False)
    ssh_user = serializers.CharField(max_length=128)
    auth_mode = serializers.ChoiceField(
        choices=[WorkspaceConnection.AUTH_PRIVATE_KEY, WorkspaceConnection.AUTH_PASSWORD],
        default=WorkspaceConnection.AUTH_PRIVATE_KEY,
        required=False,
    )
    private_key = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    password = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    project_id = serializers.UUIDField(required=False, allow_null=True)
    workspace_root = serializers.CharField(max_length=1024, default="~/.nexus", required=False)
    metadata = serializers.DictField(required=False)

    def validate(self, attrs):
        auth_mode = attrs.get("auth_mode", WorkspaceConnection.AUTH_PRIVATE_KEY)
        if auth_mode == WorkspaceConnection.AUTH_PRIVATE_KEY and not attrs.get("private_key"):
            raise serializers.ValidationError("private_key is required for private_key auth_mode.")
        if auth_mode == WorkspaceConnection.AUTH_PASSWORD and not attrs.get("password"):
            raise serializers.ValidationError("password is required for password auth_mode.")
        return attrs


class WorkspaceConnectionUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128, required=False)
    ssh_host = serializers.CharField(max_length=255, required=False)
    ssh_port = serializers.IntegerField(min_value=1, max_value=65535, required=False)
    ssh_user = serializers.CharField(max_length=128, required=False)
    auth_mode = serializers.ChoiceField(
        choices=[WorkspaceConnection.AUTH_PRIVATE_KEY, WorkspaceConnection.AUTH_PASSWORD],
        required=False,
    )
    private_key = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    password = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    metadata = serializers.DictField(required=False)
    workspace_root = serializers.CharField(max_length=1024, required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("At least one update field is required.")
        return attrs


class WorkspaceConnectionValidateSerializer(serializers.Serializer):
    connection_id = serializers.UUIDField(required=False)
    name = serializers.CharField(max_length=128)
    connection_type = serializers.ChoiceField(choices=[WorkspaceConnection.TYPE_SSH], default=WorkspaceConnection.TYPE_SSH, required=False)
    ssh_host = serializers.CharField(max_length=255)
    ssh_port = serializers.IntegerField(min_value=1, max_value=65535, default=22, required=False)
    ssh_user = serializers.CharField(max_length=128)
    auth_mode = serializers.ChoiceField(
        choices=[WorkspaceConnection.AUTH_PRIVATE_KEY, WorkspaceConnection.AUTH_PASSWORD],
        default=WorkspaceConnection.AUTH_PRIVATE_KEY,
        required=False,
    )
    private_key = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    password = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    project_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        if attrs.get("connection_id"):
            return attrs
        auth_mode = attrs.get("auth_mode", WorkspaceConnection.AUTH_PRIVATE_KEY)
        if auth_mode == WorkspaceConnection.AUTH_PRIVATE_KEY and not attrs.get("private_key"):
            raise serializers.ValidationError("private_key is required for private_key auth_mode.")
        if auth_mode == WorkspaceConnection.AUTH_PASSWORD and not attrs.get("password"):
            raise serializers.ValidationError("password is required for password auth_mode.")
        return attrs


class WorkspaceConnectionTestSerializer(serializers.Serializer):
    status = serializers.CharField()
    facts = serializers.DictField()
    checks = serializers.ListField(child=serializers.DictField())
    error = serializers.CharField(allow_blank=True)


class WorkspaceTerminalSessionSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    project_id = serializers.UUIDField(source="project.id", read_only=True, allow_null=True)
    connection_id = serializers.UUIDField(source="connection.id", read_only=True)
    connection_name = serializers.CharField(source="connection.name", read_only=True)
    created_by = serializers.IntegerField(source="created_by.id", read_only=True, allow_null=True)
    metadata = serializers.SerializerMethodField()

    def get_metadata(self, obj):
        return {key: value for key, value in (obj.metadata or {}).items() if not str(key).startswith("_")}

    class Meta:
        model = WorkspaceTerminalSession
        fields = [
            "id",
            "tenant_id",
            "project_id",
            "connection_id",
            "connection_name",
            "shell",
            "cols",
            "rows",
            "status",
            "last_error",
            "started_at",
            "ended_at",
            "metadata",
            "created_by",
            "created_at",
            "updated_at",
        ]


class WorkspaceTerminalSessionCreateSerializer(serializers.Serializer):
    connection_id = serializers.UUIDField()
    shell = serializers.ChoiceField(
        choices=[
            WorkspaceTerminalSession.SHELL_AUTO,
            WorkspaceTerminalSession.SHELL_POWERSHELL,
            WorkspaceTerminalSession.SHELL_SH,
            WorkspaceTerminalSession.SHELL_BASH,
        ],
        default=WorkspaceTerminalSession.SHELL_AUTO,
        required=False,
    )
    cols = serializers.IntegerField(min_value=20, max_value=400, default=100, required=False)
    rows = serializers.IntegerField(min_value=8, max_value=120, default=30, required=False)
    metadata = serializers.DictField(required=False)


class WorkspaceTerminalSessionCloseSerializer(serializers.Serializer):
    status = serializers.CharField()


class WorkspaceToolConfigApplySerializer(serializers.Serializer):
    OP_API_ONLY = "api_only"
    OP_MCP_ADD = "mcp_add"
    OP_MCP_REMOVE = "mcp_remove"
    OP_MCP_REPLACE = "mcp_replace"
    OP_MCP_CLEAR = "mcp_clear"
    OP_FULL_PROFILE = "full_profile"
    OPERATION_CHOICES = [
        OP_API_ONLY,
        OP_MCP_ADD,
        OP_MCP_REMOVE,
        OP_MCP_REPLACE,
        OP_MCP_CLEAR,
        OP_FULL_PROFILE,
    ]

    tool = serializers.ChoiceField(choices=["codex"], default="codex", required=False)
    operation = serializers.ChoiceField(choices=OPERATION_CHOICES)
    provider_runtime_id = serializers.UUIDField(required=False, allow_null=True)
    agent_id = serializers.UUIDField(required=False, allow_null=True)
    mcp_server_name = serializers.CharField(max_length=128, required=False, allow_blank=True)

    def validate(self, attrs):
        operation = attrs["operation"]
        if operation in {self.OP_API_ONLY, self.OP_FULL_PROFILE} and not attrs.get("provider_runtime_id"):
            raise serializers.ValidationError("provider_runtime_id is required for this operation.")
        if operation in {self.OP_MCP_ADD, self.OP_MCP_REPLACE} and not attrs.get("agent_id"):
            raise serializers.ValidationError("agent_id is required for this operation.")
        if operation == self.OP_MCP_REMOVE and not attrs.get("mcp_server_name"):
            raise serializers.ValidationError("mcp_server_name is required for mcp_remove.")
        return attrs


class WorkspaceToolConfigChangeSerializer(serializers.Serializer):
    SECTION_API = "api"
    SECTION_AGENTS = "agents"
    SECTION_ADVANCED = "advanced"
    ACTION_SET_ROUTER = "set_router"
    ACTION_SET_RUNTIME = "set_runtime"
    ACTION_ROTATE_API = "rotate_api_credential"
    ACTION_ADD_AGENT = "add_agent"
    ACTION_REMOVE_AGENT = "remove_agent"
    ACTION_ROTATE_AGENT = "rotate_agent_credential"
    ACTION_REPLACE_ALL = "replace_all_mcp"
    ACTION_CLEAR_ALL = "clear_all_mcp"

    tool = serializers.ChoiceField(choices=["codex"], default="codex", required=False)
    section = serializers.ChoiceField(choices=[SECTION_API, SECTION_AGENTS, SECTION_ADVANCED])
    action = serializers.ChoiceField(
        choices=[
            ACTION_SET_RUNTIME,
            ACTION_SET_ROUTER,
            ACTION_ROTATE_API,
            ACTION_ADD_AGENT,
            ACTION_REMOVE_AGENT,
            ACTION_ROTATE_AGENT,
            ACTION_REPLACE_ALL,
            ACTION_CLEAR_ALL,
        ]
    )
    provider_runtime_id = serializers.UUIDField(required=False, allow_null=True)
    router_id = serializers.UUIDField(required=False, allow_null=True)
    agent_id = serializers.UUIDField(required=False, allow_null=True)
    mcp_server_name = serializers.CharField(max_length=128, required=False, allow_blank=True)
    expected_revision = serializers.RegexField(r"^[0-9a-f]{64}$", required=False, allow_blank=True)
    confirm_destructive = serializers.BooleanField(default=False, required=False)

    def validate(self, attrs):
        action = attrs["action"]
        section = attrs["section"]
        allowed = {
            self.SECTION_API: {self.ACTION_SET_ROUTER, self.ACTION_SET_RUNTIME, self.ACTION_ROTATE_API},
            self.SECTION_AGENTS: {self.ACTION_ADD_AGENT, self.ACTION_REMOVE_AGENT, self.ACTION_ROTATE_AGENT},
            self.SECTION_ADVANCED: {self.ACTION_REPLACE_ALL, self.ACTION_CLEAR_ALL},
        }
        if action not in allowed[section]:
            raise serializers.ValidationError("Action does not belong to the selected setup section.")
        if action == self.ACTION_SET_ROUTER and not attrs.get("router_id"):
            raise serializers.ValidationError("router_id is required for API setup.")
        if action == self.ACTION_SET_RUNTIME and not attrs.get("provider_runtime_id"):
            raise serializers.ValidationError("provider_runtime_id is required for legacy API setup.")
        if action == self.ACTION_ROTATE_API and not (attrs.get("router_id") or attrs.get("provider_runtime_id")):
            raise serializers.ValidationError("router_id is required for API setup.")
        if action in {self.ACTION_ADD_AGENT, self.ACTION_REPLACE_ALL} and not attrs.get("agent_id"):
            raise serializers.ValidationError("agent_id is required for this Agent setup action.")
        if action == self.ACTION_REMOVE_AGENT and not attrs.get("mcp_server_name"):
            raise serializers.ValidationError("mcp_server_name is required when removing an Agent.")
        if action in {self.ACTION_REPLACE_ALL, self.ACTION_CLEAR_ALL} and not attrs.get("confirm_destructive"):
            raise serializers.ValidationError("confirm_destructive=true is required for this operation.")
        return attrs
