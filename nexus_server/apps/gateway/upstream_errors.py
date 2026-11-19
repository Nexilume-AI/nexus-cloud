"""Bounded, allowlisted diagnostics. Never relay arbitrary upstream text."""
import re

MESSAGES = {
    'context_length_exceeded': 'Upstream rejected the context length.',
    'rate_limit_exceeded': 'Upstream rate limit was reached.',
    'insufficient_quota': 'Upstream quota is exhausted.',
    'timeout': 'Upstream timed out while processing the request.',
    'server_error': 'Upstream reported an internal error.',
    'invalid_request': 'Upstream rejected the request format or parameters.',
    'incompatible_decision_schema': 'Decision model requires a task-specific schema and cannot rank these routes.',
}


def diagnostic(raw, *, stage, status=None):
    error = raw.get('error', raw) if isinstance(raw, dict) else {}
    error = error if isinstance(error, dict) else {}
    code = str(error.get('code') or error.get('type') or '')
    message = str(error.get('message') or '')[:8192].lower()
    aliases = {'context_window_exceeded': 'context_length_exceeded', 'request_too_large': 'context_length_exceeded',
               'invalid_request_error': 'invalid_request', 'rate_limit_error': 'rate_limit_exceeded',
               'request_timeout': 'timeout', 'upstream_timeout': 'timeout', 'internal_server_error': 'server_error'}
    reason = aliases.get(code, code)
    if 'fixed mapping question' in message or 'task-specific checkpoint' in message:
        reason = 'incompatible_decision_schema'
    elif any(s in message for s in ['maximum context length', 'context length exceeded', 'context window exceeded', 'too many tokens']):
        reason = 'context_length_exceeded'
    elif 'timed out' in message or 'timeout' in message:
        reason = 'timeout'
    elif reason not in MESSAGES:
        reason = 'rate_limit_exceeded' if status == 429 else 'server_error' if status and status >= 500 else 'unknown'
    details = {'origin': 'upstream', 'stage': stage, 'reason': reason, 'retry_safe': False}
    if status is not None:
        details['upstream_status'] = status
    if code in MESSAGES or code in aliases or code in {'PROVIDER_STREAM_FRAME_TOO_LARGE', 'PROVIDER_RESPONSE_TOO_LARGE', 'PROVIDER_TIMEOUT'}:
        details['upstream_code'] = code
    if reason == 'context_length_exceeded':
        for field, pattern in [('context_limit_tokens', r'maximum context length is\s+(\d{1,10})'),
                               ('requested_tokens', r'(?:requested|resulted in)\s+(\d{1,10})\s+tokens')]:
            match = re.search(pattern, message)
            if match:
                details[field] = int(match.group(1))
    return MESSAGES.get(reason, 'Upstream reported a failure without a recognized reason.'), details
