"""Bound decision-only excerpts; never truncate the actual inference payload."""
import json
import re
from collections import deque
from urllib.parse import urlsplit
from apps.gateway.system_one_protocol import MAX_REQUEST_BYTES
from apps.gateway.request_tokens import encoding
from apps.gateway.provider_adapters import ProviderClientError


def input_limit(runtime, model):
    from apps.gateway.model_info import provider_fields
    from apps.gateway import models_dev
    offer = runtime.model_offers.filter(upstream_model_id=model).first()
    fields = provider_fields(offer, runtime, models_dev.snapshot()) if offer else {}
    bounds = [fields[k]['value'] for k in ('context_window', 'max_input_tokens')
              if fields.get(k, {}).get('value') is not None]
    return min(bounds) if bounds else documented_input_limit(runtime, model)


def documented_input_limit(runtime, model):
    """Narrow vendor fallback when API/model metadata does not expose capacity.

    Alibaba documents this exact model at 65,536 tokens (checked 2026-11-20):
    https://www.alibabacloud.com/help/en/model-studio/decision-model-api.md
    Do not infer capacity for a custom service that reuses the model ID.
    Provider model overrides/advertised bounds above remain authoritative.
    """
    if model != 'decision-model-preview' or getattr(runtime, 'runtime_type', '') != 'direct_api':
        return None
    endpoint = getattr(runtime, 'internal_api_url', '')
    if not isinstance(endpoint, str):
        return None
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname or ''
        if (parsed.scheme == 'https' and parsed.port in (None, 443)
                and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
                and parsed.path.rstrip('/') == '/compatible-mode/v1'
                and re.fullmatch(r'[a-z0-9-]+\.(?:ap-southeast-1|cn-beijing)\.maas\.aliyuncs\.com', host)):
            return 65536
    except ValueError:
        pass
    return None


# Extract verbatim requirements, never execute or paraphrase task instructions.
_REQUIREMENT = re.compile(
    r"\b(?:must|required?|shall|never|without|only|constraint|ensure|avoid|do not|"
    r"at least|at most|budget|deadline|acceptance|preserve)\b|"
    r"必须|不得|不能|不要|要求|约束|确保|保留|禁止|至少|最多|预算|验收|限制", re.I)
_MARKER = "\n[...omitted for routing...]\n"


def _pieces(text):
    # Sentence/line boundaries preserve local meaning. Split pathological long
    # lines as well; this is a selection unit, not a limit on retained content.
    result = []
    for match in re.finditer(r"[^\n。！？.!?]+[\n。！？.!?]*|[\n。！？.!?]+", text):
        line = match.group()
        result.extend(line[i:i+256] for i in range(0, len(line), 256))
    return result


def _spread(indices):
    # Breadth-first spatial coverage; no bias toward the front of the document.
    pending = deque([(0, len(indices))])
    while pending:
        start, end = pending.popleft()
        if start >= end:
            continue
        mid = (start + end) // 2
        yield indices[mid]
        pending.extend(((start, mid), (mid + 1, end)))


def fit(payload, limit=None):
    tokenizer = encoding('o200k_base')
    known = type(limit) is int and limit > 0
    capacity = limit if known else 4096
    # Reserve output plus tokenizer/template uncertainty. This is a reference
    # estimate, not a claim to know every third-party tokenizer.
    reserve = max(256, capacity // 8)
    budget = max(1, capacity - reserve)
    # Capacity metadata does not identify the upstream tokenizer or its hidden
    # choice template. Real high-entropy requests used about 34% more tokens
    # upstream than o200k_base. Keep a separate conservative envelope rather
    # than presenting reference counts as exact provider counts.
    template_margin = 128

    def guarded(count):
        return (count * 3 + 1) // 2 + template_margin

    reference_budget = max(0, (budget - template_margin) * 2 // 3)
    result = {**payload, 'state': dict(payload['state'])}
    original = str(result['state'].get('task', ''))

    def serialize(task):
        result['state']['task'] = task
        raw = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
        text = raw.decode()
        count = sum(len(tokenizer.encode_ordinary(text[i:i+4096])) for i in range(0, len(text), 4096))
        return raw, count

    def fits(raw, count):
        return guarded(count) <= budget and len(raw) <= MAX_REQUEST_BYTES

    def too_small():
        raise ProviderClientError('Decision model capacity cannot fit candidates, instructions and task.',
            error_code='DECISION_CONTEXT_TOO_SMALL', safe_to_retry=False, affects_health=False,
            error_details={'origin': 'nexus', 'stage': 'decision_input', 'context_tokens': capacity,
                           'input_budget_tokens': budget, 'retry_safe': False})

    raw, original_tokens = serialize(original)
    tokens = original_tokens
    selected_count = total_count = None
    if not fits(raw, tokens):
        empty, overhead = serialize('')
        if not fits(empty, overhead):
            too_small()
        pieces = _pieces(original)
        total_count = len(pieces)
        cached_costs = {}
        costs = []
        for piece in pieces:
            if piece not in cached_costs:
                cached_costs[piece] = len(tokenizer.encode_ordinary(json.dumps(piece, ensure_ascii=False))) + 12
            costs.append(cached_costs[piece])
        scores = [len(_REQUIREMENT.findall(piece)) for piece in pieces]
        # De-duplicate repeated boilerplate but always retain boundary candidates.
        seen, unique = set(), []
        for i, piece in enumerate(pieces):
            if piece not in seen:
                unique.append(i); seen.add(piece)
        salient = []
        for score in sorted(set(scores) - {0}, reverse=True):
            salient.extend(_spread([i for i in unique if scores[i] == score]))
        coverage = list(_spread(unique))

        def excerpt(allowance):
            selected, used = set(), 0
            def take(indices, ceiling):
                nonlocal used
                for i in indices:
                    if i not in selected and used + costs[i] <= ceiling:
                        selected.add(i); used += costs[i]
            if pieces:
                take([0, len(pieces)-1], allowance // 2)
            take(salient, allowance * 3 // 4)
            take(coverage, allowance)
            # Fill remaining room with adjacent context, without changing order.
            take(range(len(pieces)), allowance)
            out, previous = [], -1
            for i in sorted(selected):
                if i != previous + 1:
                    out.append(_MARKER)
                out.append(pieces[i]); previous = i
            if previous != len(pieces)-1:
                out.append(_MARKER)
            return ''.join(out), len(selected)

        # Final serialized JSON (including every candidate/instruction) is the
        # admission check. Reduce on either the token or transport-byte bound.
        allowance = reference_budget - overhead
        for _ in range(32):
            task, selected_count = excerpt(allowance)
            raw, tokens = serialize(task)
            if selected_count and fits(raw, tokens):
                break
            if not selected_count:
                too_small()
            factor = min(budget / max(guarded(tokens), 1), MAX_REQUEST_BYTES / max(len(raw), 1), .95)
            allowance = int(allowance * factor * .95)
        else:
            too_small()
    return raw, {'context_tokens': capacity, 'capacity_known': known,
                 'reserved_tokens': reserve, 'input_budget_tokens': budget,
                 'estimated_input_tokens': tokens, 'original_estimated_input_tokens': original_tokens,
                 'guarded_input_tokens': guarded(tokens),
                 'reference_input_budget_tokens': reference_budget,
                 'tokenizer_safety_factor': 1.5, 'template_margin_tokens': template_margin,
                 'transport_limit_bytes': MAX_REQUEST_BYTES, 'input_bytes': len(raw),
                 'tokenizer': 'o200k_base_reference',
                 'task_truncated': result['state']['task'] != original,
                 'selection': 'requirements_and_distributed_excerpts' if result['state']['task'] != original else 'full',
                 'selected_segments': selected_count, 'total_segments': total_count}
