"""Authorized-candidate diagnostics. Never put prompt text or secrets in traces."""
from apps.gateway.capabilities import ModelOperationUnsupported, deployment_contract, request_operation
from apps.gateway.model_info import source_fields
from apps.gateway.request_tokens import RequestTokens, request_options


class AutoCandidatesUnavailable(ModelOperationUnsupported):
    def __init__(self, candidates):
        self.error_details = {'stage': 'auto_eligibility', 'excluded_candidates': candidates}
        summary = '; '.join(f"{c['model']}: " + ', '.join(r['code'] for r in c['reasons']) for c in candidates)
        super().__init__('No authorized Auto candidate supports this request capacity and capabilities. ' + summary)


def inspect_candidate(source, payload, catalog, *, tokens=None):
    fields = {k: v.get('value') for k, v in source_fields(source, catalog).items() if isinstance(v, dict)}
    options = request_options(payload)
    estimate = (tokens or RequestTokens(payload)).estimate(source)
    count, output = estimate['input_tokens'], options['output_tokens']
    reasons = []
    def reject(code, **detail):
        reasons.append({'code': code, **detail})
    contract = deployment_contract(source)
    operation = request_operation(payload)
    if operation not in contract['operations']:
        reject('operation_unsupported', requested=operation, supported=contract['operations'])
    media = estimate['unknown_input_costs']
    image = operation in ('images.edit', 'images.variation') or any(k in media for k in ('image_url', 'input_image'))
    if image and 'image' not in contract['input_modalities']:
        reject('image_input_unsupported')
    if 'input_audio' in media and 'audio' not in contract['input_modalities']:
        reject('audio_input_unsupported')
    if operation.startswith('images.') and 'image' not in contract['output_modalities']:
        reject('image_output_unsupported')
    for code, required, limit in (
        ('context_window_exceeded', count + output, fields.get('context_window')),
        ('max_input_tokens_exceeded', count, fields.get('max_input_tokens')),
        ('max_output_tokens_exceeded', output, fields.get('max_output_tokens')),
    ):
        if limit is not None and required > limit:
            reject(code, required_tokens=required, limit_tokens=limit)
    if options['tools'] and fields.get('supports_function_calling') is False:
        reject('tools_unsupported')
    effort, levels = options['reasoning_effort'], fields.get('reasoning_efforts')
    if effort and levels is not None and effort not in levels:
        reject('reasoning_effort_unsupported', requested=effort, supported=levels)
    return {'eligible': not reasons, 'reasons': reasons,
            'capacity': {**estimate, 'output_tokens': output,
                         'context_window': fields.get('context_window'),
                         'max_input_tokens': fields.get('max_input_tokens'),
                         'max_output_tokens': fields.get('max_output_tokens')}}
