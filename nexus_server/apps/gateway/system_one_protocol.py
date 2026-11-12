"""Bounded native JSON decisions. No chat conversion or caller-supplied endpoint."""
import json
import math
import re
from rest_framework.exceptions import ParseError, ValidationError
from rest_framework.parsers import BaseParser

MAX_BYTES = 65536


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("Non-finite JSON number")
    value = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    def walk(item, depth=0):
        if depth > 32:
            raise ValueError("JSON nesting exceeds 32 levels")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Non-finite JSON number")
        if isinstance(item, dict):
            for child in item.values():
                walk(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                walk(child, depth + 1)
    walk(value)
    return value


class NativeJSONParser(BaseParser):
    media_type = "application/json"

    def parse(self, stream, media_type=None, parser_context=None):
        encoded = stream.read(MAX_BYTES + 1)
        if len(encoded) > MAX_BYTES:
            raise ParseError("System One request exceeds 64 KiB.")
        try:
            return strict_json(encoded)
        except (ValueError, TypeError, RecursionError):
            raise ParseError("Invalid JSON: duplicate keys, non-finite numbers and excessive nesting are not accepted.") from None


def validate_request(data):
    if not isinstance(data, dict) or set(data) - {"model", "state", "questions", "router_id"}:
        raise ValidationError("Expected model, state and questions; router_id is optional. Streaming and image/extraction inputs are not supported.")
    model = data.get("model")
    if not isinstance(model, str) or not model.strip() or len(model) > 128 or any(ord(c) < 32 for c in model):
        raise ValidationError("A Router-exported model ID is required.")
    if not isinstance(data.get("state"), (str, dict, list)):
        raise ValidationError("state must be text, an object or an array.")
    questions = data.get("questions")
    if not isinstance(questions, dict) or not 1 <= len(questions) <= 32:
        raise ValidationError("questions must contain 1–32 named questions.")
    for name, question in questions.items():
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name) or not isinstance(question, dict):
            raise ValidationError("Invalid question ID or question object.")
        if set(question) - {"type", "instructions", "criteria"} or not isinstance(question.get("instructions"), (str, dict, list)):
            raise ValidationError("Each question needs a type and instructions, with optional criteria.")
        kind, criteria = question.get("type"), question.get("criteria")
        if kind == "choice":
            if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 255 or any(
                not key or len(key) > 128 or any(ord(c) < 32 for c in key) or not isinstance(value, (str, dict, list))
                for key, value in criteria.items()):
                raise ValidationError("Choice criteria must contain 2–255 named options with descriptions.")
        elif kind == "score":
            if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10 or any(not isinstance(v, (str, dict, list)) for v in criteria):
                raise ValidationError("Score criteria must contain 2–10 ordered descriptions.")
        elif kind == "noul":
            if criteria is not None and (not isinstance(criteria, dict) or set(criteria) - {"true", "false"}):
                raise ValidationError("Noul criteria may contain only true and false descriptions.")
        else:
            raise ValidationError("Supported question types: choice, score, noul.")
    if "router_id" in data:
        from rest_framework.serializers import UUIDField
        data = {**data, "router_id": str(UUIDField().run_validation(data["router_id"]))}
    return dict(data)


def number(value, lo, hi):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and lo <= value <= hi


def validate_response(raw, questions):
    from .system_one import choice_answer
    if not isinstance(raw, dict) or not isinstance(raw.get("model"), str) or not isinstance(raw.get("answers"), dict) or set(raw["answers"]) != set(questions):
        raise ValueError("Invalid response envelope")
    for name, question in questions.items():
        answer = raw["answers"][name]
        kind = question["type"]
        if not isinstance(answer, dict) or answer.get("type") != kind:
            raise ValueError("Invalid answer type")
        if kind == "choice":
            choice_answer(raw, question=name, choices=question["criteria"])
        elif kind == "score":
            if not number(answer.get("score"), 0, len(question["criteria"]) - 1):
                raise ValueError("Invalid score")
            probabilities = answer.get("probabilities")
            if probabilities is not None and (not isinstance(probabilities, dict) or any(
                k not in {str(i) for i in range(len(question["criteria"]))} or not number(v, 0, 1) for k, v in probabilities.items())):
                raise ValueError("Invalid probabilities")
        elif not number(answer.get("noul"), 0, 1):
            raise ValueError("Invalid yes/no probability")
        if "confidence" in answer and not number(answer["confidence"], 0, 1):
            raise ValueError("Invalid confidence")
    usage = raw.get("usage")
    if usage is not None and not isinstance(usage, dict):
        raise ValueError("Invalid usage")
    counts = []
    for key in ("input_tokens", "output_tokens"):
        value = (usage or {}).get(key)
        if value is not None and (type(value) is not int or not 0 <= value <= 2**31 - 1):
            raise ValueError("Invalid token usage")
        counts.append(value)
    if sum(v or 0 for v in counts) > 2**31 - 1:
        raise ValueError("Invalid total usage")
    return counts
