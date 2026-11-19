# Long-context routing and streaming

Decision models and inference models have separate input budgets. Nexus does not
send a million-token inference request verbatim to the decision model.

## Decision input

The routing harness builds a bounded task excerpt, retaining both its beginning
and latest instructions. It then fits the serialized decision request to the
configured decision model's `context_window` and `max_input_tokens` metadata.
The smallest known limit applies; unknown capacity uses a conservative 4,096
token reference budget. Space is reserved for the upstream template/schema.

Only the task excerpt is shortened. Candidate identifiers, choice definitions and
routing instructions remain intact. If those fixed fields cannot fit, Nexus
reports `DECISION_CONTEXT_TOO_SMALL` and uses the router's configured fallback.
The actual inference messages, tools and attachments are not truncated.

The audit trace includes `decision_input` (capacity, budget, reference token
estimate and truncation) and sanitized `decision_error` details. Estimates use
the bundled o200k reference tokenizer; the upstream model remains authoritative
when its tokenizer or template differs. Custom decision models still need to
implement the configured protocol and choice schema. Input fitting does not
convert an incompatible task-specific checkpoint into a general router.

## Streaming and disconnects

On ASGI, Chat and Responses streams send an SSE comment every ten seconds while
waiting, only between complete events. Comments carry no model output or usage.
The bounded stream bridge preserves backpressure and runs synchronous provider
reads outside the event loop. WSGI retains its synchronous streaming path.

Disconnects signal cancellation and close the upstream iterator when the blocking
read unwinds (bounded by the provider read timeout). They do not automatically
replay a request or mark the provider unhealthy. A `CLIENT_DISCONNECTED` receipt
records unknown upstream outcome when no final usage was observed. If final
usage and completion were already observed, normal settlement occurs exactly
once, including cache usage. Unknown usage is not fabricated as a successful
zero-cost completion.

Upstream HTTP and SSE failures expose a bounded diagnostic: origin, stage,
recognized reason, HTTP status and reported context/token limits when available.
Arbitrary upstream text, credentials and prompts are not copied into diagnostics.

Heartbeat comments prevent downstream idle expiry; they do not remove provider
read deadlines, model context limits or all possible network failures.

## Regression coverage

Tests cover heartbeat framing, cancellation, one-time audit/settlement, cached
token accounting, decision-only truncation, fixed-schema overflow and sanitized
upstream failures. Existing capacity fallback and cascade billing tests remain
part of the deployment gate. No database migration is required for this change.


## Production validation, 2026-11-19

The 65 production-stage regressions and 73 Community regressions passed.
Public HTTPS tests returned all three synthetic head/middle/tail markers:

| Request | Upstream input tokens | Elapsed seconds | Heartbeats |
| --- | ---: | ---: | ---: |
| Pro, approximately 1M | 999,624 | 120.412 | 6 |
| Pro, near context limit | 1,046,903 | 126.598 | 11 |
| Auto, Chat | 1,046,929 | 113.715 | 5 |
| Auto, Responses | 1,047,057 | 74.485 | 1 |
| GPT, near advertised input limit | 921,035 | 15.245 | 1 |

Auto traces recorded actual decision-model selection. A separate multibyte task
was fitted to 3,584 reference decision tokens (4,096 capacity fallback) while the
inference model received all 30,050 input tokens and followed the final instruction.
The test did not change the user's decision model or credentials.

The over-limit request was forwarded under the existing soft capacity fallback;
the upstream rejected 1,052,303 tokens against its 1,048,576 limit. GPT's million-
token Responses request still failed upstream without a recognized reason.
Those failures are preserved in the evidence rather than labeled successful.

An actual client disconnect produced exactly one `CLIENT_DISCONNECTED` receipt
with unknown upstream outcome. The earlier intended disconnect probe completed
too quickly and is explicitly excluded from disconnect validation. All fourteen
acceptance/cache requests have one audit receipt each.

Auto Responses reported 523,264 cached tokens; the repeated Chat cache probe
reported 28,416. Token counts conserved and matched receipts. A separate short
Responses cache probe failed upstream. Live tariffs were zero, so this is not a
measurement of monetary savings. These timings are observations, not benchmarks.

See [sanitized validation data](stream-recovery-validation-2026-11-19.json).
