"""Bounded, field-level model metadata. No inferred model aliases or credentials."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

LIMITS = ("context_window", "max_input_tokens", "max_output_tokens")
EFFORTS = {"off", "minimal", "low", "medium", "high", "xhigh", "max"}


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
        if isinstance(info.get(key), bool):
            result[key] = info[key]
    if isinstance(info.get("reasoning_efforts"), list) and all(isinstance(v, str) and v in EFFORTS for v in info["reasoning_efforts"]):
        result["reasoning_efforts"] = sorted(set(info["reasoning_efforts"]))
    reasoning = info.get("reasoning", mapping(info.get("capabilities")).get("reasoning"))
    if isinstance(reasoning, bool):
        result["reasoning_supported"] = reasoning
    elif isinstance(reasoning, dict):
        if isinstance(reasoning.get("supported"), bool):
            result["reasoning_supported"] = reasoning["supported"]
        efforts = reasoning.get("efforts", reasoning.get("effort"))
        if isinstance(efforts, list) and all(isinstance(v, str) and v in EFFORTS for v in efforts):
            result["reasoning_efforts"] = sorted(set(efforts))
        if isinstance(reasoning.get("included_in_output"), bool):
            result["reasoning_in_output"] = reasoning["included_in_output"]
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
            is_stale = source != "administrator" and (stale(updated_at) if updated_at else True)
            result[key] = {"value": value, "source": source, "updated_at": updated_at,
                           "stale": is_stale, "upstream_confirmed": confirmed and not is_stale}
    return result


def aggregate(candidates):
    """A fallback route must accommodate every eligible candidate, not the best."""
    result = {}
    for key in (*LIMITS, "reasoning_supported", "reasoning_efforts", "reasoning_in_output"):
        fields = [candidate.get(key) for candidate in candidates]
        known = bool(fields) and all(field is not None for field in fields)
        values = [field["value"] for field in fields if field is not None]
        if not known:
            value = None
        elif key in LIMITS:
            value = min(values)
        elif key == "reasoning_efforts":
            value = sorted(set.intersection(*(set(v) for v in values)))
        else:
            value = values[0] if all(v == values[0] for v in values) else None
        dates = [field["updated_at"] for field in fields if field and field.get("updated_at")]
        result[key] = {"value": value,
                       "sources": sorted({field["source"] for field in fields if field} | ({"unknown"} if not known else set())),
                       "updated_at": min(dates) if dates else None,
                       "stale": any(field["stale"] for field in fields if field),
                       "upstream_confirmed": known and value is not None and all(field["upstream_confirmed"] for field in fields)}
    return result
