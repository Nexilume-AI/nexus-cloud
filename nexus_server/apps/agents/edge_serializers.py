from __future__ import annotations

import json
import re
from urllib.parse import quote

from rest_framework import serializers
from apps.common.resource_catalog import ResourceOwnershipInputSerializer, resource_context_payload
from .edge_policy import edge_policy

from .models import AgentRuntimeDeployment, EdgeAgentRegistration, EdgeNode
from .runtime_services import public_mcp_url
from .workspace_grants import WORKSPACE_CAPABILITIES, normalize_workspace_capabilities
from .mobile_access import MOBILE_CAPABILITIES, normalize_mobile_capabilities


class EdgePairingCodeCreateSerializer(serializers.Serializer):
    expires_in_seconds = serializers.IntegerField(min_value=60, max_value=1800, default=600)
    project_id = serializers.UUIDField(required=False, allow_null=True)
    ownership = ResourceOwnershipInputSerializer(required=False)

    def validate(self, attrs):
        ownership = attrs.get("ownership")
        if ownership is not None and "project_id" in attrs:
            legacy_id = str(attrs.get("project_id") or "")
            explicit_id = str(ownership.get("project_id") or "")
            if ownership["scope"] == "organization" and legacy_id:
                raise serializers.ValidationError({"ownership": "Ownership conflicts with project_id."})
            if ownership["scope"] == "project" and legacy_id != explicit_id:
                raise serializers.ValidationError({"ownership": "Ownership conflicts with project_id."})
        return attrs


class EdgeNodeEnrollSerializer(serializers.Serializer):
    pairing_code = serializers.CharField(max_length=256)
    router_id = serializers.RegexField(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    domain_id = serializers.CharField(max_length=253)
    display_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    ipv6_mode = serializers.ChoiceField(choices=EdgeNode.IPV6_MODE_CHOICES, default=EdgeNode.IPV6_ROUTED_PREFIX)
    software_version = serializers.CharField(max_length=64, required=False, allow_blank=True)
    device_cert_thumbprint = serializers.RegexField(r"^[0-9a-fA-F]{64}$", required=False, allow_blank=True)
    device_csr = serializers.CharField(max_length=16384, required=False, allow_blank=True, trim_whitespace=False)
    capabilities = serializers.JSONField(required=False, default=dict)
    connectivity_mode = serializers.ChoiceField(
        choices=EdgeNode.CONNECTIVITY_CHOICES,
        required=False,
        default=EdgeNode.CONNECTIVITY_DIRECT_IPV6,
    )

    def validate(self, attrs):
        if attrs.get("device_csr") and attrs.get("device_cert_thumbprint"):
            raise serializers.ValidationError(
                "Send either a device CSR for managed identity or a certificate thumbprint for manual identity, not both."
            )
        if attrs.get("connectivity_mode") == EdgeNode.CONNECTIVITY_RELAY:
            router_id = str(attrs.get("router_id") or "")
            domain_id = str(attrs.get("domain_id") or "").lower()
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?", router_id):
                raise serializers.ValidationError({"router_id": "Relay router IDs must use lowercase DNS-safe characters."})
            if len(domain_id) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in domain_id.split(".")):
                raise serializers.ValidationError({"domain_id": "Relay domain must be a valid lowercase DNS name."})
            attrs["domain_id"] = domain_id
        return attrs


class EdgeDeviceCertificateRenewSerializer(serializers.Serializer):
    device_csr = serializers.CharField(max_length=16384, trim_whitespace=False)


class EdgeIngressCertificateRenewSerializer(serializers.Serializer):
    ingress_csr = serializers.CharField(max_length=16384, trim_whitespace=False)


class EdgePresenceSerializer(serializers.Serializer):
    state = serializers.ChoiceField(
        choices=(EdgeNode.CONNECTION_ONLINE, EdgeNode.CONNECTION_DEGRADED),
        default=EdgeNode.CONNECTION_ONLINE,
    )
    connectivity_mode = serializers.ChoiceField(choices=EdgeNode.CONNECTIVITY_CHOICES)
    capabilities = serializers.DictField(
        child=serializers.BooleanField(),
        required=False,
        default=dict,
    )

    def validate_capabilities(self, value):
        allowed = {
            "runtime_context_v1",
            "invoke_interactions_v1",
            "mcp_stream_v1",
            "mcp_tasks_v1",
            "caller_mobile_v1",
            "durable_recovery_v1",
        }
        unsupported = set(value) - allowed
        if unsupported:
            raise serializers.ValidationError(
                f"Unsupported Router capability: {sorted(unsupported)[0]}"
            )
        return value


class EdgeNodeSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    project_id = serializers.UUIDField(source="project.id", read_only=True, allow_null=True)
    registration_count = serializers.SerializerMethodField()
    ownership = serializers.SerializerMethodField()
    access = serializers.SerializerMethodField()
    owner_relation = serializers.SerializerMethodField()
    owner_label = serializers.SerializerMethodField()
    is_legacy = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()
    connection_status = serializers.SerializerMethodField()
    connection_status_reason = serializers.SerializerMethodField()
    registration_diagnostic = serializers.SerializerMethodField()
    presence_supported = serializers.SerializerMethodField()
    capabilities = serializers.SerializerMethodField()
    enrollment_generation = serializers.SerializerMethodField()

    class Meta:
        model = EdgeNode
        fields = [
            "id",
            "tenant_id",
            "project_id",
            "router_id",
            "domain_id",
            "display_name",
            "connection_status",
            "connection_status_reason",
            "registration_diagnostic",
            "presence_supported",
            "presence_expires_at",
            "last_presence_at",
            "ipv6_mode",
            "connectivity_mode",
            "software_version",
            "capabilities",
            "enrollment_generation",
            "last_seen_at",
            "registration_count",
            "ownership",
            "access",
            "owner_relation",
            "owner_label",
            "is_legacy",
            "allowed_actions",
            "status",
            "created_at",
            "updated_at",
        ]

    def get_registration_count(self, obj) -> int:
        return obj.agent_registrations.exclude(status="deleted").count()

    def get_connection_status(self, obj) -> str:
        return obj.effective_connection_status()

    def get_connection_status_reason(self, obj) -> str:
        return obj.effective_connection_status_reason()

    def get_presence_supported(self, obj) -> bool:
        return obj.presence_supported

    def get_registration_diagnostic(self, obj):
        if obj.effective_connection_status() != EdgeNode.CONNECTION_DEGRADED:
            return None
        return obj.registration_diagnostic or None

    def get_capabilities(self, obj) -> dict:
        capabilities = obj.capabilities if isinstance(obj.capabilities, dict) else {}
        return {key: value for key, value in capabilities.items() if not str(key).startswith("_nexus_")}

    def get_enrollment_generation(self, obj) -> int:
        capabilities = obj.capabilities if isinstance(obj.capabilities, dict) else {}
        try:
            return max(0, int(capabilities.get("_nexus_enrollment_generation") or 0))
        except (TypeError, ValueError):
            return 0

    def _is_owner(self, obj) -> bool:
        request = self.context.get("request")
        if request is None or not obj.owner_subject_hash:
            return False
        from apps.common.subjects import request_subject

        return request_subject(request).subject_hash == obj.owner_subject_hash

    def get_owner_relation(self, obj) -> str:
        if not obj.owner_subject_hash:
            return "legacy"
        return "own" if self._is_owner(obj) else "other"

    def get_ownership(self, obj) -> dict:
        return edge_policy().node_context(request=self.context.get("request"), resource_type="edge_node", obj=obj)["ownership"]

    def get_access(self, obj) -> dict:
        return edge_policy().node_context(request=self.context.get("request"), resource_type="edge_node", obj=obj)["access"]

    def get_owner_label(self, obj) -> str:
        owner_relation = self.get_owner_relation(obj)
        if owner_relation == "own":
            return "You"
        if owner_relation == "legacy":
            return "Legacy workspace router"
        email = str(getattr(obj.registered_by, "email", "") or "")
        if "@" in email:
            local, domain = email.split("@", 1)
            return f"{local[:1]}***@{domain}"
        labels = {
            "user": "Workspace member",
            "service_account": "Service account",
            "api_key": "API key owner",
        }
        return labels.get(obj.owner_subject_type, "Workspace member")

    def get_is_legacy(self, obj) -> bool:
        return not bool(obj.owner_subject_hash)

    def get_allowed_actions(self, obj) -> list[str]:
        capabilities = self.context.get("capabilities") or {}
        if self._is_owner(obj):
            actions = []
            if capabilities.get("update_own"):
                actions.append("update")
            if capabilities.get("revoke_own"):
                actions.append("revoke")
            if capabilities.get("use_own"):
                actions.append("use")
            return actions
        if capabilities.get("revoke_any"):
            return ["revoke"]
        return []

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        if self.context.get("request") is None or payload["access"]["can_read"]:
            return payload
        for field in ("router_id", "domain_id", "capabilities", "last_seen_at", "last_presence_at", "presence_expires_at", "registration_diagnostic"):
            payload.pop(field, None)
        payload["registration_count"] = 0
        payload["allowed_actions"] = []
        payload["connection_status"] = "access_required"
        payload["connection_status_reason"] = "Access required to view Router network and health details."
        return payload


class EdgeNodeUpdateSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=255, required=False, allow_blank=False)
    project_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide a display name or project.")
        return attrs


class EdgeNodeDetailSerializer(EdgeNodeSerializer):
    registrations = serializers.SerializerMethodField()

    class Meta(EdgeNodeSerializer.Meta):
        fields = [*EdgeNodeSerializer.Meta.fields, "registrations"]

    def get_registrations(self, obj) -> list[dict]:
        from django.utils import timezone
        access = resource_context_payload(request=self.context.get("request"), resource_type="edge_node", obj=obj)["access"]
        if not access["can_read"]:
            return []

        registrations = (
            obj.agent_registrations.exclude(status="deleted")
            .select_related("agent", "node", "runtime_deployment")
            .order_by("origin")
        )
        now = timezone.now()
        result = []
        for registration in registrations:
            runtime = getattr(registration, "runtime_deployment", None)
            if registration.binding_mode == EdgeAgentRegistration.BINDING_SUPPRESSED:
                provisioning_state = "suppressed"
            elif (
                registration.agent_id
                and runtime is not None
                and runtime.effective_status(now=now) == AgentRuntimeDeployment.STATUS_ACTIVE
            ):
                provisioning_state = "ready"
            elif not registration.is_effectively_available(now=now):
                provisioning_state = "unavailable"
            else:
                provisioning_state = "pending"
            result.append({
                "id": str(registration.id),
                "origin": registration.origin,
                "protocols": registration.protocols,
                "capabilities": registration.capabilities,
                "transport": registration.transport,
                "binding_mode": registration.binding_mode,
                "managed_agent_name": registration.managed_agent_name,
                "manifest_digest": registration.manifest_digest,
                "provisioning_state": provisioning_state,
                "runtime_id": str(runtime.id) if runtime is not None else None,
                "mcp_url": (
                    f"/api/v1/agents/{registration.agent_id}/mcp/"
                    if registration.agent_id else None
                ),
                "health_status": (
                    runtime.effective_health_status(now=now)
                    if runtime is not None
                    else (
                        registration.health_status
                        if registration.is_effectively_available(now=now)
                        else AgentRuntimeDeployment.HEALTH_UNHEALTHY
                    )
                ),
                "lease_active": registration.status == "active" and registration.lease_expires_at > now,
                "lease_expires_at": registration.lease_expires_at,
                "agent": (
                    {"id": str(registration.agent_id), "name": registration.agent.name}
                    if registration.agent_id
                    else None
                ),
            })
        return result


class EdgeManagedProvisioningSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=["managed"])
    agent_name = serializers.CharField(max_length=255, trim_whitespace=True)
    manifest_digest = serializers.RegexField(
        r"^[0-9a-f]{64}$", required=False, allow_blank=True, default=""
    )


class EdgeComputerContractSerializer(serializers.Serializer):
    requirement = serializers.ChoiceField(choices=["disabled", "optional", "required"])
    workspace_capabilities = serializers.ListField(
        child=serializers.ChoiceField(choices=WORKSPACE_CAPABILITIES),
        allow_empty=True,
    )

    def validate_workspace_capabilities(self, value):
        return normalize_workspace_capabilities(value)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["requirement"] == "disabled" and attrs["workspace_capabilities"]:
            raise serializers.ValidationError(
                "Disabled Computer requirement cannot declare Workspace capabilities."
            )
        return attrs


class EdgeMobileContractSerializer(serializers.Serializer):
    requirement = serializers.ChoiceField(choices=["disabled", "optional", "required"])
    mobile_capabilities = serializers.ListField(
        child=serializers.ChoiceField(choices=MOBILE_CAPABILITIES),
        allow_empty=True,
    )

    def validate_mobile_capabilities(self, value):
        return normalize_mobile_capabilities(value)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        capabilities = attrs["mobile_capabilities"]
        if attrs["requirement"] == "disabled" and capabilities:
            raise serializers.ValidationError(
                "Disabled Mobile requirement cannot declare Mobile capabilities."
            )
        if attrs["requirement"] == "required" and not capabilities:
            raise serializers.ValidationError(
                "Required Mobile requirement must declare at least one capability."
            )
        return attrs


class EdgeAgentRegistrationUpsertSerializer(serializers.Serializer):
    origin = serializers.CharField(max_length=512)
    route_id = serializers.CharField(max_length=128)
    protocols = serializers.ListField(child=serializers.ChoiceField(choices=["mcp", "a2a", "http"]), allow_empty=False)
    capabilities = serializers.ListField(child=serializers.CharField(max_length=255), allow_empty=False)
    transport = serializers.ChoiceField(
        choices=EdgeAgentRegistration.TRANSPORT_CHOICES,
        default=EdgeAgentRegistration.TRANSPORT_DIRECT_IPV6,
    )
    mcp_tools = serializers.ListField(child=serializers.JSONField(), required=False, default=list, max_length=256)
    ipv6_address = serializers.IPAddressField(protocol="IPv6", required=False)
    port = serializers.IntegerField(min_value=1, max_value=65535, required=False)
    path = serializers.RegexField(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$", required=False, allow_blank=True, default="")
    scheme = serializers.ChoiceField(choices=["https"], required=False, allow_blank=True, default="")
    tls_server_name = serializers.RegexField(
        r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))+$",
        required=False,
        allow_blank=True,
    )
    ca_bundle_id = serializers.RegexField(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$", required=False, allow_blank=True)
    relay_id = serializers.RegexField(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$", required=False, allow_blank=True)
    relay_router_id = serializers.RegexField(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$", required=False, allow_blank=True)
    relay_assignment_id = serializers.RegexField(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", required=False, allow_blank=True)
    generation = serializers.IntegerField(min_value=1)
    lease_seconds = serializers.IntegerField(min_value=30, max_value=3600, default=300)
    provisioning = EdgeManagedProvisioningSerializer(required=False)
    computer = EdgeComputerContractSerializer(required=False)
    mobile = EdgeMobileContractSerializer(required=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        transport = attrs["transport"]
        if transport == EdgeAgentRegistration.TRANSPORT_DIRECT_IPV6:
            required = ["ipv6_address", "port", "tls_server_name", "ca_bundle_id"]
            missing = [name for name in required if not attrs.get(name)]
            if missing:
                raise serializers.ValidationError({name: "This field is required for direct IPv6." for name in missing})
            attrs["scheme"] = "https"
        else:
            required = ["relay_id", "relay_router_id", "relay_assignment_id"]
            missing = [name for name in required if not attrs.get(name)]
            if missing:
                raise serializers.ValidationError({name: "This field is required for Relay transport." for name in missing})
            forbidden = ["ipv6_address", "port", "tls_server_name", "ca_bundle_id"]
            if any(attrs.get(name) for name in forbidden):
                raise serializers.ValidationError("Relay registrations must not publish a direct network endpoint.")
            attrs["path"] = ""
            attrs["scheme"] = ""
        if "mcp" in attrs.get("protocols", []) and transport == EdgeAgentRegistration.TRANSPORT_DIRECT_IPV6:
            expected = f"/mcp/{quote(attrs['origin'], safe='-._~')}"
            if len(expected.encode("ascii")) > 255:
                raise serializers.ValidationError(
                    {"origin": "Percent-encoded MCP origin exceeds the 255-byte path limit."}
                )
            if attrs.get("path") != expected:
                raise serializers.ValidationError(
                    {"path": "MCP path must be /mcp/<percent-encoded origin>."}
                )
        names = set()
        chat_tools = 0
        slash_commands: set[str] = set()
        normalized_tools = []
        declared_mobile = set((attrs.get("mobile") or {}).get("mobile_capabilities") or [])
        for index, tool in enumerate(attrs.get("mcp_tools") or []):
            if not isinstance(tool, dict):
                raise serializers.ValidationError({"mcp_tools": f"Tool {index} must be an object."})
            name = str(tool.get("name") or "")
            intent = str(tool.get("intent") or "")
            version = tool.get("intent_version", 1)
            schema = tool.get("input_schema")
            if schema is None and tool.get("input_schema_json"):
                try:
                    schema = json.loads(str(tool["input_schema_json"]))
                except (TypeError, ValueError) as exc:
                    raise serializers.ValidationError(
                        {"mcp_tools": f"Tool {index} has invalid input_schema_json."}
                    ) from exc
            if schema is None:
                schema = {"type": "object", "additionalProperties": True}
            if (
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,126}", name)
                or name in names
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,126}", intent)
            ):
                raise serializers.ValidationError({"mcp_tools": f"Tool {index} has an invalid or duplicate mapping."})
            if intent not in attrs.get("capabilities", []):
                raise serializers.ValidationError(
                    {"mcp_tools": f"Tool {index} intent must be present in the published capabilities."}
                )
            if not isinstance(version, int) or version < 1 or not isinstance(schema, dict):
                raise serializers.ValidationError({"mcp_tools": f"Tool {index} has an invalid version or schema."})
            task = bool(tool.get("task") or tool.get("chat"))
            continuable = bool(tool.get("continuable"))
            recovery_protocol = int(tool.get("recovery_protocol") or 0)
            if recovery_protocol not in {0, 1}:
                raise serializers.ValidationError({"mcp_tools": f"Tool {index} has an unsupported recovery protocol."})
            demo = bool(tool.get("demo"))
            chat = bool(tool.get("chat"))
            interactive = bool(tool.get("interactive") or chat)
            slash_command = str(tool.get("slash_command") or "").strip().removeprefix("/")
            slash_description = str(tool.get("slash_description") or "").strip()
            if slash_command:
                properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
                if (
                    not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", slash_command)
                    or slash_command in slash_commands
                    or not task
                    or not ({"content", "message"} & set(properties))
                ):
                    raise serializers.ValidationError({"mcp_tools": f"Tool {index} has an invalid or duplicate slash command."})
                if len(slash_description) > 160:
                    raise serializers.ValidationError({"mcp_tools": f"Tool {index} slash_description is too long."})
                slash_commands.add(slash_command)
            raw_profiles = tool.get("execution_profiles") or []
            if not isinstance(raw_profiles, list) or len(raw_profiles) > 8:
                raise serializers.ValidationError({"mcp_tools": f"Tool {index} execution_profiles must be a list of at most eight entries."})
            execution_profiles = []
            profile_ids: set[str] = set()
            default_profile_seen = False
            allowed_efforts = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
            for raw_profile in raw_profiles:
                if not isinstance(raw_profile, dict):
                    raise serializers.ValidationError({"mcp_tools": f"Tool {index} has an invalid execution profile."})
                profile_id = str(raw_profile.get("id") or "").strip()
                label = str(raw_profile.get("label") or "").strip()
                model = str(raw_profile.get("model") or "").strip()
                efforts = raw_profile.get("reasoning_efforts") or []
                default_effort = str(raw_profile.get("default_reasoning_effort") or "").strip().lower()
                context_window = raw_profile.get("context_window")
                raw_is_default = raw_profile.get("is_default", False)
                if (
                    not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", profile_id)
                    or profile_id in profile_ids or not label or len(label) > 80 or not model or len(model) > 128
                    or not isinstance(efforts, list) or any(str(value).lower() not in allowed_efforts for value in efforts)
                    or (default_effort and default_effort not in [str(value).lower() for value in efforts])
                    or not isinstance(raw_is_default, bool)
                    or (raw_is_default and default_profile_seen)
                    or (context_window is not None and (isinstance(context_window, bool) or not isinstance(context_window, int) or context_window <= 0))
                ):
                    raise serializers.ValidationError({"mcp_tools": f"Tool {index} has an invalid execution profile."})
                profile_ids.add(profile_id)
                normalized_efforts = list(dict.fromkeys(str(value).lower() for value in efforts))
                if normalized_efforts and not default_effort:
                    default_effort = normalized_efforts[0]
                execution_profiles.append({
                    "id": profile_id, "label": label, "model": model,
                    "is_default": raw_is_default,
                    "reasoning_efforts": normalized_efforts,
                    "default_reasoning_effort": default_effort,
                    "context_window": context_window,
                })
                default_profile_seen = default_profile_seen or raw_is_default
            if execution_profiles and not default_profile_seen:
                execution_profiles[0]["is_default"] = True
            raw_modalities = tool.get("input_modalities") or ["text"]
            if (
                not isinstance(raw_modalities, list)
                or not raw_modalities
                or any(str(value).lower() not in {"text", "image", "audio"} for value in raw_modalities)
            ):
                raise serializers.ValidationError({"mcp_tools": f"Tool {index} has invalid input_modalities."})
            input_modalities = list(dict.fromkeys(str(value).lower() for value in raw_modalities))
            properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
            if "audio" in input_modalities and not (
                isinstance(properties.get("audio"), dict) and properties["audio"].get("type") == "array"
            ):
                raise serializers.ValidationError({"mcp_tools": f"Tool {index} audio modality requires an audio array input."})
            if "image" in input_modalities and not (
                isinstance(properties.get("attachments"), dict)
                and properties["attachments"].get("type") == "array"
            ):
                raise serializers.ValidationError({"mcp_tools": f"Tool {index} image modality requires an attachments array input."})
            raw_mobile_scopes = tool.get("mobile_scopes") or []
            if not isinstance(raw_mobile_scopes, list):
                raise serializers.ValidationError(
                    {"mcp_tools": f"Tool {index} mobile_scopes must be a list."}
                )
            if any(str(value or "").strip() not in MOBILE_CAPABILITIES for value in raw_mobile_scopes):
                raise serializers.ValidationError(
                    {"mcp_tools": f"Tool {index} has invalid mobile_scopes."}
                )
            mobile_scopes = normalize_mobile_capabilities(raw_mobile_scopes)
            if set(mobile_scopes) - declared_mobile:
                raise serializers.ValidationError(
                    {"mcp_tools": f"Tool {index} mobile_scopes must be declared by the Agent mobile contract."}
                )
            if continuable and not task:
                raise serializers.ValidationError(
                    {"mcp_tools": f"Tool {index} continuable policy requires task."}
                )
            if chat:
                chat_tools += 1
                if chat_tools > 1:
                    raise serializers.ValidationError(
                        {"mcp_tools": "Only one MCP tool may be the chat entry."}
                    )
            names.add(name)
            normalized_tools.append({
                "name": name,
                "title": str(tool.get("title") or "")[:255],
                "description": str(tool.get("description") or intent)[:1024],
                "input_schema": schema,
                "intent": intent,
                "intent_version": version,
                "task": task,
                "continuable": continuable,
                "recovery_protocol": recovery_protocol,
                "demo": demo,
                "chat": chat,
                "interactive": interactive,
                "mobile_scopes": mobile_scopes,
                "slash_command": slash_command,
                "slash_description": slash_description,
                "execution_profiles": execution_profiles,
                "input_modalities": input_modalities,
            })
        attrs["mcp_tools"] = normalized_tools
        if normalized_tools and "mcp" not in attrs.get("protocols", []):
            raise serializers.ValidationError({"mcp_tools": "MCP tools require the mcp protocol."})
        if attrs.get("provisioning") and (
            "mcp" not in attrs.get("protocols", []) or not normalized_tools
        ):
            raise serializers.ValidationError({
                "provisioning": "Managed provisioning requires at least one MCP tool."
            })
        return attrs


class EdgeRelayAssignmentSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1, max_value=1, default=1)
    router_id = serializers.RegexField(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    domain_id = serializers.CharField(max_length=253)
    current_relay_id = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    failed_relay_id = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")


class EdgeAgentRegistrationSerializer(serializers.ModelSerializer):
    node_id = serializers.UUIDField(source="node.id", read_only=True)
    router_id = serializers.CharField(source="node.router_id", read_only=True)
    agent_id = serializers.UUIDField(source="agent.id", read_only=True, allow_null=True)
    endpoint_url = serializers.CharField(read_only=True)
    lease_active = serializers.SerializerMethodField()
    runtime_id = serializers.SerializerMethodField()
    mcp_url = serializers.SerializerMethodField()
    provisioning_state = serializers.SerializerMethodField()
    health_status = serializers.SerializerMethodField()

    class Meta:
        model = EdgeAgentRegistration
        fields = [
            "id",
            "node_id",
            "router_id",
            "agent_id",
            "origin",
            "route_id",
            "protocols",
            "capabilities",
            "mcp_tools",
            "binding_mode",
            "managed_agent_name",
            "manifest_digest",
            "computer_requirement",
            "workspace_capabilities",
            "mobile_requirement",
            "mobile_capabilities",
            "transport",
            "ipv6_address",
            "port",
            "path",
            "scheme",
            "tls_server_name",
            "ca_bundle_id",
            "relay_id",
            "relay_router_id",
            "relay_assignment_id",
            "endpoint_url",
            "generation",
            "lease_expires_at",
            "last_renewed_at",
            "last_probe_at",
            "health_status",
            "lease_active",
            "runtime_id",
            "mcp_url",
            "provisioning_state",
            "status",
            "created_at",
            "updated_at",
        ]

    def get_lease_active(self, obj) -> bool:
        from django.utils import timezone

        return obj.status == "active" and obj.lease_expires_at > timezone.now()

    @staticmethod
    def _runtime(obj):
        cached = getattr(obj, "_effective_runtime_cache", None)
        if cached is not None:
            return cached
        runtime = (
            AgentRuntimeDeployment.objects.select_related("edge_registration__node")
            .filter(edge_registration=obj)
            .order_by("created_at")
            .first()
        )
        obj._effective_runtime_cache = runtime
        return runtime

    def get_runtime_id(self, obj):
        runtime = self._runtime(obj)
        return str(runtime.id) if runtime is not None else None

    def get_mcp_url(self, obj):
        if not obj.agent_id:
            return None
        return public_mcp_url(request=self.context.get("request"), agent=obj.agent)

    def get_provisioning_state(self, obj):
        if obj.binding_mode == EdgeAgentRegistration.BINDING_SUPPRESSED:
            return "suppressed"
        if not obj.agent_id:
            return "pending"
        runtime = self._runtime(obj)
        if runtime is not None and runtime.effective_status() == AgentRuntimeDeployment.STATUS_ACTIVE:
            return "ready"
        if not obj.is_effectively_available():
            return "unavailable"
        return "pending"

    def get_health_status(self, obj):
        runtime = self._runtime(obj)
        if runtime is not None:
            return runtime.effective_health_status()
        if not obj.is_effectively_available():
            return AgentRuntimeDeployment.HEALTH_UNHEALTHY
        return obj.health_status


class EdgeAgentBindSerializer(serializers.Serializer):
    registration_id = serializers.UUIDField()
    env = serializers.CharField(max_length=64, default="prod")
