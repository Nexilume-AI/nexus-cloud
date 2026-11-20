# Decision routing: capacity-aware input and model objective

Available in Enterprise and Community distributions. Production rollout requires the matching backend and frontend release.

## Configuration

`decision_config.routing_preference` accepts `cost` (default) or `performance`.
The Router decision-service form exposes **Cost first** and **Performance first**.
These options select the final inference model; they do not shrink or expand the
decision prompt. The same capacity-aware input builder applies to both.

- Cost first asks for the lowest estimated cost among candidates able to complete
  the task. Candidate costs use configured Source input/output tariffs and the
  request output budget (or the gateway default). With multiple Sources, the
  upper estimate is used; unknown prices/media costs stay unknown. This is not a
  forecast of cache savings or a final bill. Equal/unknown prices use capability
  and speed as tie-breakers.
- Performance first prioritizes task success and the strongest relevant profile
  based on the user's existing model order. These profiles are preferences, not
  measured quality guarantees. It defaults to the strongest eligible relevant
  candidate for nontrivial reasoning, correctness verification, optimization,
  debugging, constrained planning, proofs and cross-module work. Uncertain task
  difficulty favors stronger capability. Merely being capable, cheaper or faster
  is insufficient reason to downgrade. Clearly mechanical, low-risk tasks with
  directly checkable output can still prefer speed; explicit task-specific
  capability evidence can also justify a different candidate. Smaller capability
  rank means stronger preference, independently of option presentation order.
  This is guidance to the Decision Model, not a hard override of its answer or
  an assertion of measured model quality.
- Both modes retain authorization and capability filtering, capacity fallback,
  task continuity, and direct calls to explicitly named models. Decision errors
  fall back once using the same objective; demanding tasks conservatively keep
  capability priority. Generic and Clef decision models receive the objective
  and pricing descriptions; the old model-specific Fast shortcut no longer
  bypasses the chosen objective.

## Long input

The old 4,000-character cutoff is removed. The latest user task is redacted for
known credential patterns and remains complete until the decision payload is
assembled with candidate descriptions and instructions. The main inference
payload is never edited by this path.

Capacity is the minimum known context/input bound from model metadata, including
existing Provider model overrides. If metadata is absent, the exact Alibaba
`decision-model-preview` on the documented Beijing/Singapore MaaS endpoint uses
its [vendor-documented 65,536-token capacity](https://www.alibabacloud.com/help/en/model-studio/decision-model-api.md)
(checked 2026-11-20). A matching model ID on a custom endpoint does not inherit
that capacity. Other unknown capacities use a conservative 4,096-token fallback;
operators can supply the actual capacity through the Provider model override.
At least 256 tokens or 12.5%
of capacity are reserved for output/template/tokenizer uncertainty. All JSON
fields count toward the final budget, using bundled o200k reference tokenization.
Because model capacity metadata does not guarantee the same tokenizer or hidden
choice template, admission uses a separate envelope of 1.5 times the reference
count plus 128 template tokens. Traces report both counts. A real dense-text
probe was undercounted by about 34% before this additional guard; ordinary prose
was nearly exact. This is safety headroom, not an exact tokenizer guarantee.
The existing native System One transport limit of 256 KiB also applies.
For the documented 64K model this gives a 57,344-token guarded input budget and
38,144 reference tokens before selection/serialization overhead. Information-rich
tasks can retain tens of thousands of tokens; short tasks and repeated boilerplate
are not padded merely to fill this budget.

When the full task fits, it is sent unchanged. Otherwise an extractive selector
scans the entire text, prioritizes explicit requirements, preserves beginning/end
segments and distributed middle excerpts, and retains their original order.
Repeated boilerplate is deprioritized. Final serialized JSON is rechecked against
both token and byte budgets; candidate/instruction overhead that cannot fit fails
before sending and invokes the normal routing fallback.

This is deterministic extractive selection, not a semantic LLM summary. It
reduces middle-of-document loss but cannot guarantee preserving every requirement
when the task itself exceeds capacity. Third-party tokenizers may differ; safety
headroom and upstream-error fallback remain necessary. Traces include capacity,
known/unknown status, original/final reference token counts, byte count, selected
segment counts, truncation state, routing objective and candidate cost estimates,
without storing the prompt in these diagnostics.

## Local verification (2026-11-20)

- Shared focused backend suites: 36 tests under Enterprise and the Community test host.
- Frontend: TypeScript and 22 targeted unit tests.
- Simulated native HTTP decision exchange verifies bounded wire payload,
  preservation of a middle constraint, and the selected objective.
- A 1,065,020-reference-token synthetic input was fitted to 4K/8K/32K contexts;
  actual reference counts were 1,516 / 2,920 / 11,335, all below the reserved
  budgets. Middle constraints and original inference input were preserved.
  Warm local preparation took 0.68–0.73 seconds (not model latency).
- Tests cover differentiated input/output prices, unknown/invalid prices,
  cheaper-but-slower candidates, performance fallback, configuration persistence,
  task pinning, authorization isolation, Unicode/JSON escaping, small/unknown
  capacity, metadata overhead, and the transport boundary.

The local fixtures do not establish live quality or billed-cost improvements.
Subsequent separately authorized temporary real-model acceptance is documented
in `technical-report/decision-routing-real-20261120/report.md` at the repository
root. Those tests preceded deployment. Actual upstream prices were unavailable;
the report distinguishes Token usage, configured zero tariffs, output-budget
truncation, and the smaller decision-only prompts from full inference inputs.

Local headless browser interaction also passed both objective selections and save payloads, with desktop/mobile overflow checks and a reviewed mobile screenshot.

Larger-context follow-up: scoped Alibaba documented-capacity fallback passed 27 focused tests under each host. Two real decision-only requests reported 46,998 and 47,041 input tokens, both accepted within the 57,344-token input budget (6.9/9.7 seconds). See `technical-report/decision-routing-real-20261120/longer-input.md`; no production deployment.

## Decision deadline

`timeout_ms` accepts 1,000–60,000 ms, default20,000. Existing saved8,000ms settings remain8seconds; this does not silently modify existing Router configuration. The form offers1/3/5/8/10/15/20/30/45/60seconds.

A monotonic elapsed deadline covers payload fitting, DNS, TCP/TLS, send, headers, body and parsing inside the decision invocation. Expiration closes the active socket and returns to the existing objective-aware fallback with no second decision POST. A resolver completing after expiration cannot dispatch. Native OS resolution may finish in a bounded daemon worker; at most32workers exist and saturation falls back rather than queuing indefinitely. An upstream request already sent may still complete or incur usage after the client disconnects.

## Stronger performance preference verification

The 2026-11-20 paired real decision-only test made20successfulrequests across5tasks, with original/reversed option presentation. On4nontrivialtasks the strongest configured candidate was selected5/8times before the stronger policy and8/8after. This validates preference alignment, not answer-quality superiority; sorting also escalated once, so over-selection remains possible. No model name is hardcoded.

## Production rollout — 2026-11-20

Backend and frontend deployed together, preserving existing capacity checks, cache statistics and stream recovery. Staged production-source tests:33passed; Community checkout:33backend and20frontend tests passed. Public Auto returned a complete correct response with one usage receipt; deployed performance policy selected the highest configured capability preference in two real decision probes. Entry and lazy Router assets were verified against release SHA256. Existing Router objectives, credentials, candidate order and saved8-second timeouts were not changed. New configurations default to20seconds; operators can choose1–60seconds.
