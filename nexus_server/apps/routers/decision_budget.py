"""Bound decision-only excerpts; never truncate the actual inference payload."""
import json
from apps.gateway.request_tokens import encoding
from apps.gateway.provider_adapters import ProviderClientError


def input_limit(runtime, model):
    from apps.gateway.model_info import provider_fields
    from apps.gateway import models_dev
    offer = runtime.model_offers.filter(upstream_model_id=model).first()
    fields = provider_fields(offer, runtime, models_dev.snapshot()) if offer else {}
    bounds = [fields[k]['value'] for k in ('context_window', 'max_input_tokens')
              if fields.get(k, {}).get('value') is not None]
    return min(bounds) if bounds else None


def fit(payload, limit=None):
    tokenizer = encoding('o200k_base')
    capacity = limit if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0 else 4096
    # Third-party tokenizers/templates differ; reserve space for their schema.
    budget = max(1, capacity - max(256, capacity // 8))
    result = {**payload, 'state': dict(payload['state'])}
    original = str(result['state'].get('task', ''))

    def serialize():
        raw = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
        if len(raw) > 65536:
            return raw, budget + 1
        text = raw.decode()
        return raw, sum(len(tokenizer.encode_ordinary(text[i:i+4096])) for i in range(0, len(text), 4096))

    raw, tokens = serialize()
    if tokens > budget or len(raw) > 65536:
        marker = '\n[...omitted for routing...]\n'
        lo, hi, best = 0, len(original), None
        while lo <= hi:
            keep = (lo + hi) // 2
            head = keep // 3
            result['state']['task'] = (original[:head] + marker + original[-(keep-head):]) if keep else ''
            candidate, count = serialize()
            if count <= budget and len(candidate) <= 65536:
                best = (candidate, count, keep); lo = keep + 1
            else:
                hi = keep - 1
        if best is None or best[2] == 0 and original:
            raise ProviderClientError('Decision model capacity cannot fit candidates, instructions and task.',
                error_code='DECISION_CONTEXT_TOO_SMALL', safe_to_retry=False, affects_health=False,
                error_details={'origin': 'nexus', 'stage': 'decision_input', 'context_tokens': capacity,
                               'input_budget_tokens': budget, 'retry_safe': False})
        raw, tokens, _ = best
    return raw, {'context_tokens': capacity, 'capacity_known': limit is not None,
                 'input_budget_tokens': budget, 'estimated_input_tokens': tokens,
                 'tokenizer': 'o200k_base_reference', 'task_truncated': json.loads(raw)['state'].get('task') != original}
