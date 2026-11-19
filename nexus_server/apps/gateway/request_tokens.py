"""Request-local BPE estimates for routing, never for billing.

Vocabularies are bundled: a Gateway request must not fetch a tokenizer. Chat
templates and third-party tokenizers can differ; estimates say so explicitly.
Opaque media is excluded, not counted as Base64 text. Its cost remains unknown.
"""
import base64
import gzip
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from django.conf import settings


@lru_cache(maxsize=2)
def encoding(name):
    import tiktoken
    directory = Path(__file__).with_name('tokenizers')
    specification = json.loads((directory / (name + '.json')).read_text())
    raw = gzip.decompress((directory / (name + '.tiktoken.gz')).read_bytes())
    if hashlib.sha256(raw).hexdigest() != specification['sha256']:
        raise ValueError('Bundled tokenizer checksum mismatch')
    ranks = {base64.b64decode(token): int(rank) for token, rank in
             (line.split() for line in raw.splitlines() if line)}
    return tiktoken.Encoding(name=name, pat_str=specification['pat_str'],
                            mergeable_ranks=ranks, special_tokens=specification['special_tokens'])


def request_options(payload):
    native = payload.get('_nexus_responses')
    native = native if isinstance(native, dict) else {}
    output = payload.get('max_completion_tokens') or payload.get('max_tokens') or native.get('max_output_tokens')
    output = output or max(int(getattr(settings, 'NEXUS_GATEWAY_DEFAULT_MAX_OUTPUT_TOKENS', 512)), 1)
    return {'output_tokens': output, 'tools': native.get('tools', payload.get('tools', [])),
            'reasoning_effort': (native.get('reasoning') or {}).get('effort', payload.get('reasoning_effort'))}


class RequestTokens:
    """Tokenize once per encoding per request, without retaining prompts globally."""
    def __init__(self, payload):
        self.counts = {}
        self.unknown = set()
        native = payload.get('_nexus_responses')
        if isinstance(native, dict):
            content = {k: native[k] for k in ('instructions', 'input', 'tools', 'tool_choice', 'text') if k in native}
        else:
            content = {k: payload[k] for k in ('messages', 'tools', 'tool_choice', 'response_format', 'functions', 'function_call') if k in payload}
        self.text = json.dumps(self._text_only(content), ensure_ascii=False, separators=(',', ':'))

    def _text_only(self, value):
        if isinstance(value, list):
            return [self._text_only(item) for item in value]
        if not isinstance(value, dict):
            return value
        kind = value.get('type', '')
        if kind in ('image_url', 'input_image', 'input_audio', 'input_file', 'file', 'video', 'video_url') and any(
                k in value for k in ('image_url', 'file_id', 'file_data', 'file', 'input_audio', 'data', 'video_url', 'url')):
            self.unknown.add(kind)
            return {'type': kind}  # Do not tokenize data URLs, file IDs, or encoded media.
        result = {}
        for key, item in value.items():
            if key == 'encrypted_content' and kind == 'reasoning':
                self.unknown.add('opaque_content')
                continue
            result[key] = self._text_only(item)
        return result

    def estimate(self, source):
        import tiktoken
        model = getattr(source, 'upstream_model_id', '') or ''
        try:
            name = tiktoken.model.encoding_name_for_model(model)
        except KeyError:
            name = 'o200k_base'
            matched = False
        else:
            matched = name in ('o200k_base', 'cl100k_base')
        if name not in ('o200k_base', 'cl100k_base'):
            name = 'o200k_base'
        if name not in self.counts:
            # encode_ordinary treats special-token-looking user text as data.
            tokenizer = encoding(name)
            # Bound work for adversarial unbroken strings; chunk boundaries are
            # another reason this admission estimate is not billable usage.
            self.counts[name] = sum(len(tokenizer.encode_ordinary(self.text[start:start + 16384]))
                                    for start in range(0, len(self.text), 16384))
        return {'input_tokens': self.counts[name], 'tokenizer': name,
                'tokenizer_match': 'model_mapping' if matched else 'reference',
                'estimated': True, 'unknown_input_costs': sorted(self.unknown)}
