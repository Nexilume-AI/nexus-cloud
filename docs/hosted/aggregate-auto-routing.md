# Aggregate Router automatic model selection

An Aggregate Router can expose `auto` alongside its existing model mappings.
Select **Auto · rules** (`task_type`) or **Auto · Decision Model** (`system_one`)
in Configure. Specified models bypass cross-model selection. Disable Auto using
Specified models only. An existing real mapping named `auto` must be renamed first.

Rules prefer the lowest observed source latency for ordinary requests. Difficult
debugging, architecture, proof and optimization requests prefer lower-numbered
binding priorities. Set stronger models to lower priorities; ties use latency.
Unknown latency ranks last. These are transparent heuristics, not a guarantee of
optimal quality or end-to-end latency. Observed source latency includes upstream work.

Decision Model uses an explicitly configured same-Organization System One Provider,
its model ID and a bounded timeout. It receives only the latest user text (up to
4,000 characters), eligible model descriptions and latency/priority metadata, never
tool results, images, the full conversation or caller credentials. Routing failures
fall back to the rules ordering. Nested Auto outputs are excluded, preventing
recursive decisions. A single eligible model needs no Decision Model call.

## Calling

Use the existing Router credential and OpenAI-compatible chat or Responses endpoint:

```json
{"model":"auto","messages":[{"role":"user","content":"Explain this function briefly."}]}
```

If the key covers multiple Routers, include `router_id`. Named models retain their
existing mapping. Follow-Router credentials discover `auto` automatically. Explicit
model policies must allow both `auto` and the underlying candidate model names;
`auto` alone does not grant access to every model. Existing operation, Router,
Project, funding, source health and Provider restrictions still apply.

Candidates are filtered for operation/modality and known context/output/effort
limits. Text size uses a conservative UTF-8 byte estimate; unknown metadata remains
unknown, and upstream validation remains authoritative. `/model/info?model=auto`
reports the conservative intersection/minimum over backing models, never their
maximum. A successful model does not make other models healthy.

## Continuity and failures

After a successful response the selected model is cached for 30 minutes, scoped
to Organization, Project, Router/config version, caller Key/user and latest user
turn. Clients may additionally send `X-Nexus-Task-ID` (at most 128 characters) to
separate concurrent tasks with identical text. A tool-result continuation retains
the selection only while that model is still authorized and eligible. A new user
turn re-evaluates. Cache failure degrades to fresh selection; it does not fail a
completed inference. No raw prompt is stored in the cache key.

Existing safe retry/stream-commit rules govern execution fallback: no replay after
streamed output. Only completed successful execution updates continuity. Routing
traces record algorithm, selected model, candidates, fallback reason and decision
latency alongside existing request usage/audit. System One's internal decision
overhead may incur upstream charges; no token usage is fabricated when unavailable.
