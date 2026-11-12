# Native System One decision routing

System One is a decision service, not an OpenAI Chat model. This integration
uses native structured decisions. Cloud exposes `POST /api/v1/openai/v1/systemone`
through the existing Router gateway; no Chat conversion is involved.

Community includes this integration directly. Follow the
[Community setup and upgrade guide](community-system-one.md) for its UI, native
API and Auto routing. Enterprise-specific wallet and API key budget behavior
described below is not part of Community's single-owner accounting.

## Providers

Create a Direct API connection and choose **System One · native decisions**.
Enter the service base URL, API key and ownership. A pasted `/systemone` suffix
is removed before model discovery. Keys retain the existing encrypted storage.

Discovery reads `/models`, accepting native `models[].name` and MaaS
`data[].id` directories. Native advertised capabilities are respected. MaaS
directories often mix model types and omit capabilities: selecting this protocol
is an operator declaration, not proof every listed model supports decisions.
Choose the exact native decision model ID supported by your service.

If a callable model is missing from the directory, use **Provider → Models →
Add explicit model**. Enter its exact ID, for example `decision-model-preview`.
The model is marked **Explicit model**, survives directory refresh, and starts
with unverified/degraded health. Adding it performs no paid probe. Router's
decision model dropdown includes it without per-Router text overrides. Use
**Use model** to connect it as a native Source in a Pool and expose that Pool
through a Router. Disabled or deleted entries cannot be revived through Add.
Actual successful native inference updates its health; catalog reachability
alone does not claim this verification.

For older configurations, **Specify an
unlisted model ID** in the Router policy and enter its exact name. This records
`model_source=explicit`; the default `catalog` mode remains strict. Configuration
still requires Router management and Provider use permissions, and checks the
same Organization and Project. An existing disabled, deleted or unavailable
Offer cannot be overridden. This legacy Router-only option does not create an
Offer or successful health result and saving configuration does not make a billable probe.

## Native Cloud API

Use the existing Router Bearer key and the **Router-exported model ID**. Keys
bound to a Router with no explicit operation restriction follow its published
model contracts. A key with an explicit `allowed_operations` list must include
`systemone`. Unbound keys must explicitly allow it. Model, Router, Organization,
Project, current membership and resource restrictions remain in force.

```sh
curl https://cloud.nexilume.com/api/v1/openai/v1/systemone \
  -H "Authorization: Bearer $NEXUS_ROUTER_KEY" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: intentional-decision-001' \
  -d '{"model":"YOUR_ROUTER_MODEL","state":"The customer needs an invoice.","questions":{"team":{"type":"choice","instructions":"Choose the responsible team.","criteria":{"billing":"Invoices and payments","support":"Technical assistance"}}}}'
```

`router_id` is optional for a key bound to exactly one Router. Supported inputs
are JSON text/object/array state and 1–32 Choice, Score or Noul questions, with a
64 KiB request/response limit. Duplicate keys, nonfinite numbers, excessive JSON
depth, unknown transport fields, streaming, images and extraction are rejected.
The Source determines the real model, endpoint and encrypted upstream key.
The returned native `answers`, model and optional usage remain native JSON.

Only one upstream POST is dispatched, including on timeout or invalid output.
An optional `Idempotency-Key` is scoped to the tenant and calling key/user; repeat
admissions return 409 without another POST (after current permissions are
rechecked). It is a durable no-replay fence, not a response cache. No prompts,
answers or plaintext credentials are persisted by this endpoint. A response has
`Cache-Control: no-store` and a Nexus `X-Request-ID` for tracing.

Gateway logs, Router/Source attribution, API key request/token counters, Provider
capacity, wallet reservations, settlement and audit are shared with other gateway
operations. The audit operation is `gateway.systemone`. Key RPM/TPM/monthly-budget
admission includes existing Gateway activity and pending native reservations.
Token and cost counters accumulate **reported** tokens using configured Source
prices; System One credits are not converted into token prices. Missing token
usage stays missing/null in the native response and is marked unknown in the
trace, not presented as a measured zero. Request counts remain usable without
token usage. A timeout may still incur an upstream charge; it is not a refund.

Native calls use eligible native Sources directly and do not invoke a second
decision model to route a decision request.

In **Router → API & keys**, select **System One · native** to see its request URL,
eligible exported models, and native Python/cURL examples. Both protocols reuse
the same managed Router key. A Router exporting only native models selects this
guide automatically. A decision Provider configured solely as an intelligent
routing policy does not implicitly expose that model as a callable output; add
its model as a Source/Pool and connect it to the Router to publish a native model.

For example, the supplied MaaS service accepted `decision-model-preview` via
native `/systemone` even though its `/models` directory did not include that ID.
This was verified using fixed test text, not customer data. Do not assume the
same model name or availability on other services.

Offers declare `systemone` and JSON output, never chat/streaming. Catalog refresh
and health do not issue billable inference. A reachable (possibly public) catalog
does **not** verify the API key or inference permission. An actual decision may
fail authentication even when the catalog is healthy. Never publish these
Offers as an ordinary Chat Source merely to bypass a contract mismatch.

## Execution Router

Add the ordinary inference Pools, select **System One · intelligent routing**,
then choose the decision Provider and model. Save and deploy as usual. A stable
default `nexus-auto-<router UUID>` model is exported. Calling that model lets the
decision choose among eligible Pools. Existing explicit Pool model names remain
pinned and do not invoke the decision service. Auto can also be aggregated through
the existing default-output mapping. Disabling the policy disables Auto.

Configuration contains only `provider_connection_id`, `model`, `model_source`, and a bounded
`timeout_ms` (500–8000). No URL or plaintext key is accepted in Router config.
Configuration requires Router management and Provider use permission. Dispatch
rechecks Organization, Provider/Runtime/Offer status and Project scope. The model
cannot add a target, authorize a resource, or bypass modality/health filters.

Only the latest user text (up to 4,000 characters) and bounded Pool descriptions
are sent; system prompts, files, images and tool bodies are not included. Do not
put secrets in user text or Pool names: arbitrary secrets cannot be detected
reliably. Enabling this policy explicitly enables that upstream data transfer.

With fewer than two or more than 32 eligible Pools, selection uses Pool priority
without a decision request. Otherwise exactly one non-streaming request is sent
with a new operation ID. Unknown output IDs, malformed probabilities, HTTP
errors, timeout and disabled Providers fall back to existing Pool priority. The
request is never automatically retried and no inference call is replayed.
Timeout can still incur an upstream decision charge; fallback is not a refund.
Trace records `router.decision` status, reason and latency without request text,
secrets or raw upstream errors. Decision charges belong to the configured
Provider account and are not included in Nexus token-cost accounting.

## Desktop

See `nexus_desktop/docs/auto-routing.md`. Choose native System One for
`state + questions → answers`. An explicitly named OpenAI JSON decision option
remains available for conventional classifiers; the two protocols are not
interchangeable. Neither changes local Ask/Auto or tool authorization.

## Release

Apply Router migration `0009_router_system_one` for Enterprise, or
`0003_router_system_one` for the personal host, before restarting Server/Web.
Native API also requires Gateway migration `0005_gatewaydecisionoperation`
(`0002_gatewaydecisionoperation` in the personal host).
No migration is applied to an existing Cloud by the test commands below.

```powershell
cd nexus_server
python manage.py test tests.test_system_one nexus_personal.tests.test_system_one_routing --settings=nexus_personal.tests.settings --noinput
cd ../nexus_desktop
node --test test/system-one.test.cjs test/decision-routing.test.cjs
```

Protocol reference: https://docs.system-one.dev/en/docs/api

### Opt-in live acceptance

`tools/system_one_acceptance.py` reads its key only from stdin; pass the explicit
`--base-url` and `--model`. `--catalog-only` performs no decision and reports
whether the exact ID is discoverable. `--pool-criteria` sends one small, fixed
native Choice request with Cloud Router-shaped options. No retry is automatic.
`nexus_desktop/test/system-one-live-acceptance.cjs` uses the actual Desktop
decision router and checks that concurrent reads of one turn make only one POST;
it never dispatches the selected inference model or operates a device.

For the full isolated HTTP chain, set non-secret
`NEXUS_SYSTEM_ONE_TEST_BASE_URL` and `NEXUS_SYSTEM_ONE_TEST_MODEL`, then supply
the key on stdin to this explicit test command:

```powershell
python manage.py test tools.system_one_cloud_acceptance --settings=nexus_personal.tests.settings --noinput
```

This creates and destroys only an in-memory test database. Provider directory
reads and one native decision use the configured real service; the final Chat
Source is a fixed loopback fixture. The test fails if the decision fell back,
so a successful Chat reply cannot hide a failed native integration. Each live
decision can incur the configured upstream account's charge.
