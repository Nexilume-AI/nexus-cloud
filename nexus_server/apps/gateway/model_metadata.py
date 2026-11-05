"""Bounded, field-level model metadata. No inferred model aliases or credentials."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

LIMITS = ("context_window", "max_input_tokens", "max_output_tokens")
EFFORTS = ("none", "off", "minimal", "low", "medium", "high", "xhigh", "max")
# LiteLLM declares these independently. Missing flags are not false and do not
# imply the common low/medium/high set. 'none' is an API value, not Pi's 'off'.
EFFORT_FLAGS = tuple(f"supports_{level}_reasoning_effort" for level in ("none", "minimal", "low", "xhigh", "max"))
REASONING_FIELDS = ("reasoning_supported", "reasoning_efforts", "reasoning_in_output",
                    "reasoning_default", "supported_openai_params", *EFFORT_FLAGS)


def effort_list(value):
    if isinstance(value, list) and len(value) <= 64 and all(effort_token(v) for v in value):
        return [level for level in EFFORTS if level in value] + sorted(set(value) - set(EFFORTS))
    return None


def effort_token(value):
    # Public discovery preserves future upstream levels without interpreting
    # them. Runtime clients separately decide which values they can send.
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is not None


def mapping(value):
    return value if isinstance(value, dict) else {}


def token_count(value):
    return value if type(value) is int and 0 < value <= 100_000_000 else None


def normalize(row):
    row = mapping(row)
    info = mapping(row.get("model_info")) or row
    limit = mapping(info.get("limit"))
    top = mapping(info.get("top_provider"))
    values = {
        "context_window": [info.get("context_window"), info.get("context_length"), limit.get("context"), top.get("context_length")],
        "max_input_tokens": [info.get("max_input_tokens"), limit.get("input")],
        "max_output_tokens": [info.get("max_output_tokens"), info.get("max_completion_tokens"), limit.get("output"), top.get("max_completion_tokens")],
    }
    # Anthropic explicitly defines max_tokens as its output maximum. Do not
    # reinterpret LiteLLM's ambiguous generic max_tokens as a context window.
    if info.get("type") == "model" and "max_input_tokens" in info:
        values["max_output_tokens"].append(info.get("max_tokens"))
    result = {key: next((token_count(v) for v in candidates if token_count(v) is not None), None)
              for key, candidates in values.items()}
    if isinstance(info.get("supports_reasoning"), bool):
        result["reasoning_supported"] = info["supports_reasoning"]
    # Normalized snapshots are also inputs; retain only the same typed fields.
    for key in ("reasoning_supported", "reasoning_in_output"):
        if key not in result and isinstance(info.get(key), bool):
            result[key] = info[key]
    for key in ("reasoning_effort_levels", "reasoning_efforts"):
        efforts = effort_list(info.get(key))
        if efforts is not None:
            result["reasoning_efforts"] = efforts
            break
    reasoning = info.get("reasoning", mapping(info.get("capabilities")).get("reasoning"))
    if isinstance(reasoning, bool) and "reasoning_supported" not in result:
        result["reasoning_supported"] = reasoning
    elif isinstance(reasoning, dict):
        if "reasoning_supported" not in result and isinstance(reasoning.get("supported"), bool):
            result["reasoning_supported"] = reasoning["supported"]
        efforts = reasoning.get("efforts", reasoning.get("effort"))
        if "reasoning_efforts" not in result and effort_list(efforts) is not None:
            result["reasoning_efforts"] = effort_list(efforts)
        if isinstance(reasoning.get("included_in_output"), bool):
            result["reasoning_in_output"] = reasoning["included_in_output"]
    params = info.get("supported_openai_params", row.get("supported_openai_params"))
    if isinstance(params, list) and len(params) <= 128 and all(
            isinstance(v, str) and 0 < len(v) <= 64 and v.replace("_", "").isalnum() for v in params):
        result["supported_openai_params"] = sorted(set(params))
    for key in EFFORT_FLAGS:
        if isinstance(info.get(key), bool):
            result[key] = info[key]
    if "reasoning_efforts" not in result:
        reported = [level for level in EFFORTS if f"supports_{level}_reasoning_effort" in EFFORT_FLAGS
                    and info.get(f"supports_{level}_reasoning_effort") is True]
        if reported:
            result["reasoning_efforts"] = reported
    default = info.get("default_reasoning_effort", info.get("reasoning_default",
                       mapping(reasoning).get("default_effort")))
    if effort_token(default):
        result["reasoning_default"] = default
    # A reasoning flag says the model can reason, not that its API accepts an
    # effort parameter. Explicit negative contracts must beat fallback metadata.
    if result.get("reasoning_supported") is False or ("supported_openai_params" in result
            and not {"reasoning_effort", "reasoning"}.intersection(result["supported_openai_params"])):
        result["reasoning_efforts"] = []
        result.pop("reasoning_default", None)
    elif "reasoning_efforts" in result:
        result["reasoning_efforts"] = [level for level in result["reasoning_efforts"]
                                       if result.get(f"supports_{level}_reasoning_effort") is not False]
        if result.get("reasoning_default") not in result["reasoning_efforts"]:
            result.pop("reasoning_default", None)
    return result


def endpoint_hash(url):
    return hashlib.sha256((url or "").rstrip("/").encode()).hexdigest()


def stale(updated_at, max_age=86400):
    try:
        date = datetime.fromisoformat(updated_at)
        if date.tzinfo is None:
            return True
        age = (datetime.now(timezone.utc) - date).total_seconds()
        return age < -300 or age > max_age
    except (ValueError, TypeError):
        return True


def merge_fields(sources):
    """Priority applies per field, so a partial upstream record keeps fallbacks."""
    result = {}
    for values, source, updated_at, confirmed in sources:
        for key, value in values.items():
            if value is None or key in result:
                continue
            is_stale = source not in {"administrator", "provider_override", "source_override"} and (stale(updated_at) if updated_at else True)
            result[key] = {"value": value, "source": source, "updated_at": updated_at,
                           "stale": is_stale, "upstream_confirmed": confirmed and not is_stale}
    return result


def reasoning_info(fields):
    """One LiteLLM representation for the console and downstream clients."""
    values = {key: fields[key]["value"] for key in REASONING_FIELDS}
    default = values["reasoning_default"]
    return {
        "supports_reasoning": values["reasoning_supported"],
        "reasoning_effort_levels": values["reasoning_efforts"],
        "default_reasoning_effort": default if default in {"none", "minimal", "low", "medium", "high", "xhigh"} else None,
        "supported_openai_params": values["supported_openai_params"],
        **{key: values[key] for key in EFFORT_FLAGS},
        "reasoning": {"supported": values["reasoning_supported"], "efforts": values["reasoning_efforts"],
                      "default_effort": default, "included_in_output": values["reasoning_in_output"]},
    }


def aggregate(candidates):
    """A fallback route must accommodate every eligible candidate, not the best."""
    result = {}
    for key in (*LIMITS, *REASONING_FIELDS):
        fields = [candidate.get(key) for candidate in candidates]
        known = bool(fields) and all(field is not None for field in fields)
        values = [field["value"] for field in fields if field is not None]
        if not known:
            value = None
        elif key in LIMITS:
            value = min(values)
        elif key in ("reasoning_efforts", "supported_openai_params"):
            value = sorted(set.intersection(*(set(v) for v in values)))
            if key == "reasoning_efforts":
                value = effort_list(value)
        else:
            value = values[0] if all(v == values[0] for v in values) else None
        dates = [field["updated_at"] for field in fields if field and field.get("updated_at")]
        result[key] = {"value": value,
                       "sources": sorted({field["source"] for field in fields if field} | ({"unknown"} if not known else set())),
                       "updated_at": min(dates) if dates else None,
                       "stale": any(field["stale"] for field in fields if field),
                       "upstream_confirmed": known and value is not None and all(field["upstream_confirmed"] for field in fields)}
    # A unanimous default is only safe when it is accepted by the whole route.
    levels = result["reasoning_efforts"]["value"]
    params = result["supported_openai_params"]["value"]
    unsupported_field = "reasoning_supported" if result["reasoning_supported"]["value"] is False else (
        "supported_openai_params" if params is not None and not {"reasoning", "reasoning_effort"}.intersection(params) else None)
    if unsupported_field:
        result["reasoning_efforts"] = {**result[unsupported_field], "value": []}
        levels = []
    if levels is None or result["reasoning_default"]["value"] not in levels:
        result["reasoning_default"] = {**result["reasoning_default"], "value": None, "upstream_confirmed": False}
    return result
