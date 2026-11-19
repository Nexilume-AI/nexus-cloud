# Auto capacity and candidate diagnostics

Auto routes evaluate input **tokens**, reserved output tokens, and model
capabilities after resolving authorized candidates. They do not compare UTF-8
bytes with a token capacity. Named models continue to bypass cross-model Auto
selection; permissions and the upstream model's actual context limit remain in
force.

The estimator uses offline BPE vocabularies from
[OpenAI tiktoken](https://github.com/openai/tiktoken), shipped with their MIT
license and SHA256 checksums. It counts Chat messages, tool schemas and outputs,
or native Responses input items, instructions, tools and output format. Input is
tokenized once per encoding within a request, not cached across users. Long
strings are processed in bounded chunks. Install `requirements.txt` (or the
Community platform lock) before starting a new deployment.

Known tiktoken model mappings use the corresponding bundled `o200k_base` or
`cl100k_base`. Other models use `o200k_base` as a **reference estimate**, not an
assertion that DeepSeek, Kimi or another provider uses that tokenizer. Chat
templates, chunk boundaries, and upstream tokenization can differ. The estimate
is never used as actual billable usage. Provider-reported usage remains the
billing authority.

Image/audio/file bodies and opaque encrypted reasoning are not interpreted as
Base64 text tokens. `unknown_input_costs` reports those exclusions; a text-only
estimate does **not** certify that the entire multimodal request fits. Unknown
model capacities are not treated as zero. Explicitly unsupported modalities and
operations are still rejected. Separate HTTP/request-size policies are unchanged.

Each considered candidate has a `candidate_assessments` entry in the Auto routing
trace. Candidates that fit the estimate are preferred. If none fit, Auto can try
**one** otherwise-authorized and capable source whose only failed checks are
estimated input/context capacity. It picks the largest usable input budget:
the smaller of `context_window - output_tokens` and `max_input_tokens`, when
known. It leaves the entire request unchanged and lets the upstream validate it.
It does not fan out to other models or run another Decision Model request.

This fallback never relaxes permissions, capabilities, reasoning levels, an
explicit output limit, or an output budget exceeding the entire context window.
A previous tool-continuation pin cannot force a smaller source in this fallback.
The trace uses `reason: capacity_estimate_fallback` and records
`capacity_fallback.upstream_validation_required: true`. The selected assessment
retains its original `eligible: false` estimate and reasons, with
`admitted_by_capacity_fallback: true` to distinguish admission from estimated fit.
Upstream errors remain errors; fallback does not promise the request fits.

If no normally eligible or capacity-fallback candidate remains, the HTTP 400
error (or SSE error event after stream
headers) retains code `MODEL_OPERATION_UNSUPPORTED` and adds:

```json
{
  "stage": "auto_eligibility",
  "excluded_candidates": [{
    "model": "public-model-alias",
    "eligible": false,
    "reasons": [{
      "code": "max_output_tokens_exceeded",
      "required_tokens": 8192,
      "limit_tokens": 4096
    }],
    "capacity": {
      "input_tokens": 100000,
      "output_tokens": 8192,
      "tokenizer": "o200k_base",
      "tokenizer_match": "reference",
      "estimated": true,
      "unknown_input_costs": []
    }
  }]
}
```

This object appears in `error.details` for JSON/Chat SSE and `details` on a native
Responses SSE error. Cascades preserve it. Other reason codes include
`max_input_tokens_exceeded`, `max_output_tokens_exceeded`,
`reasoning_effort_unsupported`, `tools_unsupported`, `operation_unsupported`,
`image_input_unsupported`, `audio_input_unsupported`, and
`image_output_unsupported`. Numeric required/limit fields use tokens.

Diagnostics contain public model aliases, capability names and counts, never
message text, tool arguments, attachment URLs or credentials. Models eliminated
by credential authorization are not added back or exposed by this assessment.
An all-candidates rejection is logged with Router/request IDs before any Decision
Model call. A Decision Model failure still falls back to the eligible rule order.
