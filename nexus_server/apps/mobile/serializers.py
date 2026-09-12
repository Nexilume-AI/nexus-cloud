from __future__ import annotations

from rest_framework import serializers

from .models import MobileCommand, MobileDevice


class MobileDeviceSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    project_id = serializers.UUIDField(source="project.id", read_only=True, allow_null=True)
    created_by = serializers.IntegerField(source="created_by.id", read_only=True, allow_null=True)
    lifecycle_status = serializers.CharField(read_only=True)
    lifecycle_detail = serializers.CharField(read_only=True)
    recommended_action = serializers.CharField(read_only=True)
    paired_at = serializers.DateTimeField(read_only=True, allow_null=True)
    pairing_expires_at = serializers.DateTimeField(read_only=True, allow_null=True)
    screenshot_available = serializers.BooleanField(read_only=True)
    screenshot_captured_at = serializers.DateTimeField(source="last_screenshot_captured_at", read_only=True, allow_null=True)
    screenshot_expires_at = serializers.DateTimeField(read_only=True, allow_null=True)
    metadata = serializers.SerializerMethodField()

    class Meta:
        model = MobileDevice
        fields = [
            "id",
            "tenant_id",
            "project_id",
            "name",
            "platform",
            "device_identifier",
            "token_prefix",
            "approval_mode",
            "online_status",
            "lifecycle_status",
            "lifecycle_detail",
            "recommended_action",
            "paired_at",
            "pairing_expires_at",
            "screenshot_available",
            "screenshot_captured_at",
            "screenshot_expires_at",
            "capabilities",
            "metadata",
            "current_package",
            "current_activity",
            "last_observation",
            "last_seen_at",
            "status",
            "created_by",
            "created_at",
            "updated_at",
        ]

    def get_metadata(self, instance):
        hidden_keys = {"paired_at", "pairing_token_issued_at", "pairing_token_expires_at"}
        return {
            key: value
            for key, value in (instance.metadata or {}).items()
            if key not in hidden_keys
        }


class MobileDeviceCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128)
    platform = serializers.ChoiceField(choices=[MobileDevice.PLATFORM_ANDROID], default=MobileDevice.PLATFORM_ANDROID, required=False)
    device_identifier = serializers.CharField(max_length=255, required=False, allow_blank=True)
    approval_mode = serializers.ChoiceField(
        choices=[MobileDevice.APPROVAL_MANUAL, MobileDevice.APPROVAL_CONFIRM_HIGH_RISK, MobileDevice.APPROVAL_AUTO],
        default=MobileDevice.APPROVAL_CONFIRM_HIGH_RISK,
        required=False,
    )
    project_id = serializers.UUIDField(required=False, allow_null=True)
    capabilities = serializers.DictField(required=False)
    metadata = serializers.DictField(required=False)


class MobileDeviceUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128, required=False)
    approval_mode = serializers.ChoiceField(
        choices=[MobileDevice.APPROVAL_MANUAL, MobileDevice.APPROVAL_CONFIRM_HIGH_RISK, MobileDevice.APPROVAL_AUTO],
        required=False,
    )
    capabilities = serializers.DictField(required=False)
    metadata = serializers.DictField(required=False)
    status = serializers.ChoiceField(choices=[MobileDevice.STATUS_ACTIVE, MobileDevice.STATUS_DISABLED], required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("At least one update field is required.")
        return attrs


class MobileCommandSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    project_id = serializers.UUIDField(source="project.id", read_only=True, allow_null=True)
    device_id = serializers.UUIDField(source="device.id", read_only=True)
    created_by = serializers.IntegerField(source="created_by.id", read_only=True, allow_null=True)
    approved_by = serializers.IntegerField(source="approved_by.id", read_only=True, allow_null=True)

    class Meta:
        model = MobileCommand
        fields = [
            "id",
            "tenant_id",
            "project_id",
            "device_id",
            "action",
            "arguments",
            "status",
            "risk_level",
            "requires_approval",
            "result",
            "error",
            "approved_at",
            "dispatched_at",
            "completed_at",
            "expires_at",
            "created_by",
            "approved_by",
            "created_at",
            "updated_at",
        ]


class MobileCommandCreateSerializer(serializers.Serializer):
    client_request_id = serializers.UUIDField(required=False)
    action = serializers.ChoiceField(choices=[choice[0] for choice in MobileCommand.ACTION_CHOICES])
    arguments = serializers.DictField(required=False)
    risk_level = serializers.ChoiceField(
        choices=[MobileCommand.RISK_LOW, MobileCommand.RISK_MEDIUM, MobileCommand.RISK_HIGH],
        required=False,
    )
    requires_approval = serializers.BooleanField(required=False)
    ttl_seconds = serializers.IntegerField(min_value=5, max_value=3600, default=120, required=False)

    def validate(self, attrs):
        action = attrs["action"]
        arguments = attrs.get("arguments") or {}
        if action in {MobileCommand.ACTION_TAP_TEXT, MobileCommand.ACTION_TYPE_TEXT, MobileCommand.ACTION_WAIT_FOR_STATE}:
            if not str(arguments.get("text") or "").strip():
                raise serializers.ValidationError({"arguments": "Text is required for this action."})
        if action == MobileCommand.ACTION_OPEN_APP and not str(arguments.get("package") or "").strip():
            raise serializers.ValidationError({"arguments": "Android package name is required."})
        coordinate_space = str(arguments.get("coordinate_space") or "normalized").strip().lower()
        if coordinate_space not in {"normalized", "pixels"}:
            raise serializers.ValidationError({"arguments": "coordinate_space must be normalized or pixels."})
        max_coordinate = 1 if coordinate_space == "normalized" else 100000
        for key in coordinate_keys_for_action(action):
            try:
                value = float(arguments.get(key))
            except (TypeError, ValueError) as exc:
                raise serializers.ValidationError({"arguments": f"{key} must be a valid non-negative number."}) from exc
            if value < 0 or value > max_coordinate:
                suffix = "between 0 and 1" if coordinate_space == "normalized" else "a valid pixel coordinate"
                raise serializers.ValidationError({"arguments": f"{key} must be {suffix}."})
        if coordinate_keys_for_action(action):
            arguments["coordinate_space"] = coordinate_space
            attrs["arguments"] = arguments
        return attrs


class MobileDeviceHeartbeatSerializer(serializers.Serializer):
    online_status = serializers.ChoiceField(choices=[MobileDevice.ONLINE_ONLINE, MobileDevice.ONLINE_OFFLINE], required=False)
    current_package = serializers.CharField(max_length=255, required=False, allow_blank=True)
    current_activity = serializers.CharField(max_length=255, required=False, allow_blank=True)
    capabilities = serializers.DictField(required=False)
    observation = serializers.DictField(required=False)
    metadata = serializers.DictField(required=False)


class MobileCommandResultSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[MobileCommand.STATUS_SUCCEEDED, MobileCommand.STATUS_FAILED])
    result = serializers.DictField(required=False)
    error = serializers.CharField(max_length=1024, required=False, allow_blank=True)


class MobileMCPCallSerializer(serializers.Serializer):
    pass


def coordinate_keys_for_action(action: str) -> list[str]:
    if action == MobileCommand.ACTION_TAP_COORDINATES:
        return ["x", "y"]
    if action == MobileCommand.ACTION_SWIPE:
        return ["start_x", "start_y", "end_x", "end_y"]
    return []
