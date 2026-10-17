"""Versioned training trajectories, separate from raw display event archives."""
import copy
import hashlib
import json
import re

from rest_framework.exceptions import ValidationError
from apps.common.redaction import redact_payload, REDACTION_SECRET_RE

SCHEMA = "nexus.trajectory.v1"
MAX_BYTES = 1024 * 1024
MAX_MESSAGES = 1000
NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def encoded(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ValidationError("Supply bounded JSON with finite numeric values.") from None


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def content_digest(trajectory):
    messages = copy.deepcopy(trajectory["messages"])
    ids = {call["id"]: f"call_{i}_{j}" for i, message in enumerate(messages) for j, call in enumerate(message.get("tool_calls", []))}
    for message in messages:
        message.pop("weight", None)
        for call in message.get("tool_calls", []):
            call["id"] = ids[call["id"]]
        if "tool_call_id" in message:
            message["tool_call_id"] = ids.get(message["tool_call_id"], message["tool_call_id"])
    return digest({"messages": messages, "tools": trajectory["tools"]})


def redact_text(value):
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return redact_payload(value)[0]
    if isinstance(parsed, (dict, list)):
        return encoded(redact_payload(parsed)[0]).decode()
    return redact_payload(value)[0]


def redact_schema(value, sensitive=False):
    """Preserve JSON Schema property names, including legitimate password fields."""
    from apps.common.redaction import REDACTION_SENSITIVE_KEYS
    if isinstance(value, list):
        return [redact_schema(child, sensitive) for child in value]
    if not isinstance(value, dict):
        return redact_payload(value)[0]
    result = {}
    for key, child in value.items():
        if key == "properties" and isinstance(child, dict):
            result[key] = {name: redact_schema(schema, name.lower() in REDACTION_SENSITIVE_KEYS) for name, schema in child.items()}
        elif key in {"default", "const", "enum", "examples"}:
            result[key] = "[REDACTED]" if sensitive and key not in {"enum", "examples"} else ["[REDACTED]"] if sensitive else redact_payload(child)[0]
        else:
            result[key] = redact_schema(child, sensitive)
    return result


def normalize_trajectory(data):
    """Keep incomplete but well-formed episodes for review, never silently train them."""
    if not isinstance(data, dict) or len(encoded(data)) > MAX_BYTES:
        raise ValidationError("A trajectory must be an object no larger than 1 MiB.")
    if data.get("schema", SCHEMA) != SCHEMA:
        raise ValidationError("Unsupported trajectory schema.")
    messages = data.get("messages")
    tools = data.get("tools", [])
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
        raise ValidationError("Supply 1–1000 ordered messages.")
    if not isinstance(tools, list) or len(tools) > 256:
        raise ValidationError("Supply at most 256 tool definitions.")
    context = data.get("context", {})
    if not isinstance(context, dict):
        raise ValidationError("context must be an object.")
    for key in ("task_id", "session_id", "client", "model", "agent_version"):
        if key in context and (not isinstance(context[key], str) or len(context[key]) > 256):
            raise ValidationError(f"context.{key} must be text up to 256 characters.")
    context = {key: context[key] for key in ("task_id", "session_id", "client", "model", "agent_version") if context.get(key)}
    state = data.get("execution_status", "completed")
    if state not in {"completed", "failed", "cancelled"}:
        raise ValidationError("Only terminal trajectories can be collected.")
    clean_tools, names, warnings = [], set(), set()
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function" or not isinstance(tool.get("function"), dict):
            raise ValidationError("Tools must use the function definition format.")
        function = tool["function"]
        name = function.get("name", "")
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in names or REDACTION_SECRET_RE.search(name):
            raise ValidationError("Tool names must be valid and unique.")
        if not isinstance(function.get("parameters"), dict):
            raise ValidationError("Each tool requires its parameters JSON schema.")
        names.add(name)
        clean_tools.append({"type": "function", "function": {k: function[k] for k in ("name", "description", "parameters") if k in function}})
    clean_messages, pending, seen = [], set(), set()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant", "tool"}:
            raise ValidationError("Messages require system, user, assistant or tool roles.")
        role = message["role"]
        content = message.get("content", "")
        if content is not None and not isinstance(content, str):
            raise ValidationError("v1 supports text content; keep binary assets in source artifacts.")
        clean = {"role": role, "content": content or ""}
        if pending and role != "tool":
            warnings.add("missing_tool_result")
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or not NAME.fullmatch(call_id) or REDACTION_SECRET_RE.search(call_id):
                raise ValidationError("Tool results require a valid tool_call_id.")
            clean["tool_call_id"] = call_id
            if call_id not in pending:
                warnings.add("orphan_tool_result")
            pending.discard(call_id)
        calls = message.get("tool_calls", [])
        if not isinstance(calls, list) or len(calls) > 128 or (calls and role != "assistant"):
            raise ValidationError("Only assistant messages can contain up to 128 tool calls.")
        if calls:
            clean["tool_calls"] = []
        for call in calls:
            if not isinstance(call, dict) or call.get("type") != "function" or not isinstance(call.get("function"), dict):
                raise ValidationError("Invalid function call.")
            cid, function = call.get("id"), call["function"]
            name, arguments = function.get("name"), function.get("arguments")
            if not isinstance(cid, str) or not NAME.fullmatch(cid) or cid in seen or REDACTION_SECRET_RE.search(cid):
                raise ValidationError("Tool call ids must be valid and unique within a trajectory.")
            if not isinstance(name, str) or not NAME.fullmatch(name) or REDACTION_SECRET_RE.search(name) or not isinstance(arguments, str):
                raise ValidationError("Tool calls require a name and JSON arguments string.")
            try:
                arguments_object = json.loads(arguments)
                if not isinstance(arguments_object, dict):
                    raise ValueError()
            except (ValueError, TypeError):
                warnings.add("invalid_tool_arguments")
                arguments_object = None
            if name not in names:
                warnings.add("missing_tool_definition")
            if arguments_object is not None:
                arguments = encoded(redact_payload(arguments_object)[0]).decode()
            clean["tool_calls"].append({"id": cid, "type": "function", "function": {"name": name, "arguments": arguments}})
            seen.add(cid)
            pending.add(cid)
        if role == "assistant":
            weight = message.get("weight", 1)
            if type(weight) is not int or weight not in (0, 1):
                raise ValidationError("Assistant weight must be 0 or 1.")
            clean["weight"] = weight
        clean_messages.append(clean)
    if pending:
        warnings.add("missing_tool_result")
    if not any(m["role"] == "user" for m in clean_messages):
        warnings.add("missing_user_message")
    if not any(m["role"] == "assistant" and (m["content"] or m.get("tool_calls")) for m in clean_messages):
        warnings.add("missing_assistant_message")
    if clean_messages[-1]["role"] != "assistant" or clean_messages[-1].get("tool_calls") or not clean_messages[-1]["content"].strip():
        warnings.add("missing_final_answer")
    # Redact contents separately: secret/phone rules must never rewrite call IDs.
    for m in clean_messages:
        m["content"] = redact_text(m["content"])
        for c in m.get("tool_calls", []):
            c["function"]["arguments"] = redact_text(c["function"]["arguments"])
    for tool in clean_tools:
        function = tool["function"]
        if "description" in function:
            if not isinstance(function["description"], str):
                raise ValidationError("Tool descriptions must be text.")
            function["description"] = redact_payload(function["description"])[0]
        function["parameters"] = redact_schema(function["parameters"])
        from jsonschema import Draft202012Validator, SchemaError
        try:
            Draft202012Validator.check_schema(function["parameters"])
        except SchemaError:
            warnings.add("invalid_tool_schema")
    trajectory = {"schema": SCHEMA, "execution_status": state, "context": redact_payload(context)[0],
                  "messages": clean_messages, "tools": clean_tools}
    # Redaction can damage numeric JSON arguments. Such samples require review.
    for m in clean_messages:
        for c in m.get("tool_calls", []):
            try:
                json.loads(c["function"]["arguments"])
            except ValueError:
                warnings.add("invalid_tool_arguments")
    groups = [digest([key, context[key]]) for key in ("task_id", "session_id") if context.get(key)]
    if not groups:
        warnings.add("missing_task_or_session")
    return trajectory, sorted(warnings), groups


def training_row(trajectory, annotation):
    result = {"messages": copy.deepcopy(trajectory["messages"])}
    selected = annotation.get("trainable_assistant_indices")
    for i, message in enumerate(result["messages"]):
        if message["role"] == "assistant" and selected is not None:
            message["weight"] = int(i in selected)
    if trajectory["tools"]:
        result["tools"] = trajectory["tools"]
    return result


def assign_splits(samples, seed, validation_percent, test_percent):
    """Union task/session/exact-content links before splitting; retries cannot leak."""
    parents = {}
    def root(key):
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key
    for sample in samples:
        keys = ["content:" + sample["content_hash"], *sample["group_keys"]]
        for key in keys[1:]:
            a, b = root(keys[0]), root(key)
            parents[max(a, b)] = min(a, b)
    splits = {}
    for sample in samples:
        group = root("content:" + sample["content_hash"])
        bucket = int(digest([seed, group])[:12], 16) % 10000
        splits[sample["id"]] = "test" if bucket < test_percent * 100 else "validation" if bucket < (test_percent + validation_percent) * 100 else "train"
    return splits


def messages_from_events(events):
    """Conservative AG-UI reconstruction. Never invent user prompts or tool schemas."""
    messages, texts, calls, warnings = [], {}, {}, set()
    for event in events:
        p = event.redacted_payload_json
        if not isinstance(p, dict):
            continue
        kind = p.get("type", event.event_type)
        if kind == "MESSAGES_SNAPSHOT" and isinstance(p.get("messages"), list):
            messages, texts, calls = [], {}, {}
            for raw_message in p["messages"]:
                if not isinstance(raw_message, dict):
                    warnings.add("unmatched_message_delta")
                    continue
                message = {"role": raw_message.get("role"), "content": raw_message.get("content", "")}
                tool_calls = raw_message.get("tool_calls", raw_message.get("toolCalls", []))
                if tool_calls:
                    message["tool_calls"] = copy.deepcopy(tool_calls)
                    for call in tool_calls:
                        if isinstance(call, dict) and call.get("id"):
                            calls[call["id"]] = message["tool_calls"][tool_calls.index(call)]
                tool_id = raw_message.get("tool_call_id", raw_message.get("toolCallId"))
                if tool_id:
                    message["tool_call_id"] = tool_id
                messages.append(message)
                if raw_message.get("id"):
                    texts[raw_message["id"]] = message
        elif kind == "TEXT_MESSAGE_START":
            message = {"role": p.get("role", "assistant"), "content": ""}
            texts[p.get("messageId")] = message
            messages.append(message)
        elif kind == "TEXT_MESSAGE_CONTENT":
            if p.get("messageId") in texts:
                texts[p["messageId"]]["content"] += str(p.get("delta", ""))
            else:
                warnings.add("unmatched_message_delta")
        elif kind == "TOOL_CALL_START":
            call = {"id": p.get("toolCallId"), "type": "function", "function": {"name": p.get("toolCallName"), "arguments": ""}}
            calls[p.get("toolCallId")] = call
            parent = texts.get(p.get("parentMessageId"))
            if parent is None:
                parent = {"role": "assistant", "content": ""}
                messages.append(parent)
            parent.setdefault("tool_calls", []).append(call)
        elif kind == "TOOL_CALL_ARGS":
            if p.get("toolCallId") in calls:
                calls[p["toolCallId"]]["function"]["arguments"] += str(p.get("delta", ""))
            else:
                warnings.add("unmatched_tool_delta")
        elif kind == "TOOL_CALL_RESULT":
            content = p.get("content", "")
            messages.append({"role": "tool", "tool_call_id": p.get("toolCallId"), "content": content if isinstance(content, str) else encoded(content).decode()})
    return messages, sorted(warnings)
