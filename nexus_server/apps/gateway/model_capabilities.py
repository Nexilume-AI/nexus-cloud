"""Scoped console overrides for the existing LiteLLM discovery contract."""
from rest_framework import serializers
from .model_metadata import LIMITS, aggregate, effort_list, effort_token, normalize, reasoning_info, token_count

OVERRIDE_FIELDS = {*LIMITS, "supports_reasoning", "reasoning_effort_levels", "default_reasoning_effort", "supported_openai_params"}


def validate_override(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - OVERRIDE_FIELDS:
        raise serializers.ValidationError("Use supported model-info capability fields, or null to restore inheritance.")
    result = {}
    for key, item in value.items():
        if item is None:
            continue  # Unknown fields inherit; false and [] remain explicit.
        if key in LIMITS:
            valid = token_count(item) is not None
        elif key == "supports_reasoning":
            valid = isinstance(item, bool)
        elif key == "reasoning_effort_levels":
            valid = effort_list(item) is not None
            if valid:
                item = effort_list(item)
        elif key == "default_reasoning_effort":
            valid = effort_token(item)
        else:
            valid = "supported_openai_params" in normalize({key: item})
            if valid:
                item = sorted(set(item))
        if not valid:
            raise serializers.ValidationError({key: "Invalid capability value."})
        result[key] = item
    levels, default = result.get("reasoning_effort_levels"), result.get("default_reasoning_effort")
    if default is not None and levels is not None and default not in levels:
        raise serializers.ValidationError({"default_reasoning_effort": "The default must be one of the configured levels."})
    if result.get("supports_reasoning") is False and (levels or default):
        raise serializers.ValidationError("Disabled reasoning cannot advertise levels or a default.")
    params = result.get("supported_openai_params")
    if params is not None and not {"reasoning", "reasoning_effort"}.intersection(params) and (levels or default):
        raise serializers.ValidationError("Configured parameters must expose reasoning to advertise effort levels.")
    return result or None


def parse_update(data):
    if not isinstance(data, dict) or set(data) != {"override", "expected_override"}:
        raise serializers.ValidationError("override and expected_override are required.")
    return validate_override(data["override"]), validate_override(data["expected_override"])


def capabilities_payload(*, override, fields, inherited, access):
    def info(candidate):
        combined = aggregate([candidate])
        return {**{key: combined[key]["value"] for key in LIMITS}, **reasoning_info(combined)}, combined
    effective_info, effective_fields = info(fields)
    inherited_info, inherited_fields = info(inherited)
    return {"override": override, "model_info": effective_info, "inherited_model_info": inherited_info,
            "fields": effective_fields, "inherited_fields": inherited_fields,
            "can_edit": bool(access.get("can_edit"))}
