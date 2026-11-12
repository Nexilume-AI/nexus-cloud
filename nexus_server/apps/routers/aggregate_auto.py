"""Cross-model selection. All candidates are resolved and authorized before ranking."""
import hashlib
import json
import re
import time
from django.core.cache import cache
from rest_framework import exceptions


def enabled(router):
    return router.router_type == 'aggregation' and router.strategy in ('task_type', 'system_one')


def task_text(payload):
    latest = next((m.get('content') for m in reversed(payload.get('messages', [])) if m.get('role') == 'user'), '')
    if isinstance(latest, list):
        latest = '\n'.join(p.get('text', '') for p in latest if isinstance(p, dict) and p.get('type') == 'text')
    return str(latest or '')


def continuity_key(request, router, payload):
    # Explicit task IDs or the latest user turn identify a tool chain, never an account-wide session.
    task = request.headers.get('X-Nexus-Task-ID', '')
    if len(task) > 128:
        raise exceptions.ValidationError('X-Nexus-Task-ID must be at most 128 characters.')
    key = getattr(request, 'api_key', None)
    personal_key = getattr(request, '_nexus_personal_router_credential_id', None)
    principal = ('key:' + str(key.pk) if key else 'personal-key:' + str(personal_key)
                 if personal_key else 'user:' + str(request.user.pk))
    binding = [str(router.tenant_id), str(router.project_id), str(router.pk), principal,
               str(router.updated_at), task, task_text(payload)]
    return 'nexus:auto-task:' + hashlib.sha256(json.dumps(binding).encode()).hexdigest()


def remember(deployment):
    state = getattr(deployment, '_nexus_auto_memory', None)
    if state:
        try:
            cache.set(state[0], state[1], timeout=1800)
        except Exception:
            pass  # Cache availability cannot turn a completed inference into failure.


def compatible(source, payload, catalog):
    from apps.gateway.model_info import source_fields
    from apps.gateway.capabilities import supports_request
    if not supports_request(source, payload):
        return False
    fields = {k: v.get('value') for k,v in source_fields(source, catalog).items() if isinstance(v,dict)}
    # Conservative UTF-8 byte bound; no image token estimate is claimed.
    size = len(json.dumps({'messages': payload.get('messages', []), 'tools': payload.get('tools', [])}, ensure_ascii=False).encode())
    output = payload.get('max_completion_tokens', payload.get('max_tokens', 512)) or 512
    if fields.get('context_window') is not None and size + output > fields['context_window']:
        return False
    if fields.get('max_input_tokens') is not None and size > fields['max_input_tokens']:
        return False
    if fields.get('max_output_tokens') is not None and output > fields['max_output_tokens']:
        return False
    if payload.get('tools') and fields.get('supports_function_calling') is False:
        return False
    effort = payload.get('reasoning_effort')
    levels = fields.get('reasoning_efforts')
    if effort and levels is not None and effort not in levels:
        return False
    return True


def rank(request, router, payload, rows):
    """Rows are (deployment, authorized binding, child trace); never add a route."""
    from apps.gateway import models_dev
    from . import decision_harness as harness
    preference_order = harness.aggregate_order(router, rows) if router.strategy == 'system_one' else []
    catalog = models_dev.snapshot()
    rows = [r for r in rows if compatible(r[0], payload, catalog)]
    if not rows:
        from apps.gateway.capabilities import ModelOperationUnsupported
        raise ModelOperationUnsupported('No authorized Auto candidate supports this request capacity and capabilities.')
    groups = {}
    for row in rows:
        groups.setdefault(row[1].exposed_model_name, []).append(row)
    complex_task = bool(re.search(r'prove|proof|architecture|race condition|deadlock|debug|optimi[sz]|证明|架构|死锁|调试|优化', task_text(payload), re.I))
    def score(name):
        candidates = groups[name]
        priority = preference_order.index(name) if preference_order else min(r[1].priority for r in candidates)
        latency = min(r[0].last_latency_ms or 10_000_000 for r in candidates)
        return (priority, latency, name) if complex_task else (latency, priority, name)
    names = sorted(groups, key=score)
    trace = {'algorithm': router.strategy, 'status': 'selected', 'reason': 'complex_task_priority' if complex_task else 'fastest_eligible', 'candidate_models': names}
    state_key = continuity_key(request, router, payload)
    continuation = any(m.get('role') == 'tool' for m in payload.get('messages', [])[-1:])
    try:
        previous = cache.get(state_key) if continuation else None
    except Exception:
        previous = None
    started = time.monotonic()
    if previous in groups:
        names.remove(previous); names.insert(0, previous)
        trace.update(reason='tool_continuation', status='retained')
    elif router.strategy == 'system_one' and len(names) > 1:
        from .decision_routing import resolve_provider
        from apps.gateway.system_one import decide
        from apps.gateway.provider_adapters import ProviderClientError
        from apps.common.crypto import decrypt_secret
        from cryptography.fernet import InvalidToken
        try:
            config = router.decision_config or {}
            account, runtime = resolve_provider(router=router, config=config)
            # Present fastest first, with stable IDs/ranks from the operator's
            # strength order. Do not renumber after filtering or truncate access.
            targets = {f'model_{i}': name for i, name in reversed(list(enumerate(preference_order))) if name in groups}
            choices = {route: harness.description(name=name, index=preference_order.index(name),
                count=len(preference_order), sources=[r[0] for r in groups[name]], catalog=catalog,
                payload=payload) for route, name in targets.items()}
            key = decrypt_secret(account.encrypted_key)
            if not key:
                raise ValueError('Missing decision credential')
            selected = decide(base_url=runtime.internal_api_url, key=key, model=config['model'],
                state=harness.state(payload, key=key), choices=choices, timeout=min(8, max(.5, config.get('timeout_ms',8000)/1000)),
                instructions=harness.INSTRUCTIONS)
            chosen = targets[selected]
            names.remove(chosen); names.insert(0, chosen)
            trace.update(reason='decision_model')
        except (ProviderClientError, exceptions.APIException, InvalidToken, ValueError, TypeError, KeyError):
            trace.update(status='fallback', reason='decision_unavailable_or_invalid')
    trace['latency_ms'] = round((time.monotonic()-started)*1000,2)
    trace['selected_model'] = names[0]
    ordered = [row for name in names for row in groups[name]]
    for deployment,binding,_ in ordered:
        deployment._nexus_auto_memory = (state_key, binding.exposed_model_name)
    return ordered, trace
