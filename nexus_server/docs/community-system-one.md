# Community: rules and System One routing

The Community source includes the complete System One Provider adapter, explicit
model catalog, native API, Router configuration UI, Auto selector and Personal
database migrations. No hosted integration patch or private package is required.
This feature uses the single owner's existing Provider connections and scoped
Router credentials. It does not add Enterprise billing or Organization IAM.

## Install or upgrade

Follow the [Community host guide](../nexus_personal/HOST.md) for a new installation.
For an existing installation, back up its database and protected configuration,
install the updated Community runtime dependency lock, and rebuild/reinstall the
Community server wheel. With that installation's protected configuration loaded:

```sh
python -m nexus_personal.manage migrate --noinput
python -m nexus_personal.manage check
```

Build `nexus_web` with `npm ci` followed by `npm run build:community`. Deploy the
complete `dist/community` output, preserving its asset manifest, and restart
the installation's web, worker and beat processes. Do not use Enterprise or test
settings for a running Community server.

The migration graph adds `routers.0002_routercredentialvault`,
`routers.0003_router_system_one` and `gateway.0002_gatewaydecisionoperation`.
Existing specified-model routes retain their strategy; Auto is opt-in.

## Rules routing, without a Decision Model

1. Add ordinary inference Providers and create Sources/Model Pools.
2. Create and deploy Execution Routers for those Pools.
3. Add their explicit outputs to an Aggregation Router under distinct model names.
4. In **Configure**, choose **Auto · rules** and save. Deploy the Router if needed.
5. In **API & keys**, obtain its Router key. `/models` now includes `auto`.

Use the actual base URL and key from your installation:

```python
import os
from openai import OpenAI

client = OpenAI(base_url=os.environ["OPENAI_BASE_URL"],
                api_key=os.environ["OPENAI_API_KEY"])
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Explain this function briefly."}],
)
print(response.choices[0].message.content)
```

Rules prefer the lowest observed eligible Source latency for routine requests.
Complex-task keywords prefer the lower-numbered binding priority. Assign those
priorities deliberately; these heuristics do not measure model intelligence.
Calling an explicit exported model bypasses cross-model selection.

## Intelligent routing

1. Create a **Direct API** Provider with protocol **System One · native decisions**.
2. Enter its upstream base URL and key, then start the Provider and refresh models.
3. If a callable decision model is not advertised, choose **Add explicit model**
   and enter its exact upstream ID. It remains unverified until a real native call.
4. On the Aggregation Router, select **Auto · Decision Model**, choose that
   Provider and decision model, and save. The client still calls `model="auto"`.

The decision service receives bounded latest-user text and eligible candidate
metadata. Timeout, invalid output or an unavailable decision Provider falls back
to rules. One eligible model needs no decision call. A successful tool continuation
keeps its eligible model; a new user turn is evaluated again. Selection is scoped
to the Router configuration and caller credential, with an optional
`X-Nexus-Task-ID` to separate concurrent tasks with identical text.

Capacity, capabilities, current ownership, model restrictions and Source health
remain enforced. Exported keys follow their Router; restricted Tool Setup grants
must include both `auto` and each permitted underlying model. A revoked credential
cannot continue a cached task. See [Auto routing](aggregate-auto-routing.md) for
selection, streaming and retry details.

## Expose the native decision API

To let clients call the decision service itself, use its model as a Source, attach
its Pool to an Execution Router and deploy it. **API & keys → System One · native**
provides the `/api/v1/openai/v1/systemone` URL and native examples using the same
Router key. Configuring a Provider as an Auto decision service alone does not
publish that Provider's model as a callable output.

The native endpoint supports bounded `state` and `questions` JSON. It is not Chat
Completions and is not streaming. It preserves request IDs, records execution
receipts and audit metadata, and rejects repeat `Idempotency-Key` admission with
409 without another upstream call. Pre-admission permission rejection retains
its 403. Community does not perform wallet settlement or offer commercial key
budgets; upstream services can still charge for decisions and inference.

## Verification

Regression suites run from this repository's `nexus_server` directory, using
the isolated `nexus_personal.tests.settings` host. They cover real local HTTP
Provider discovery and dispatch, rules, native decisions, named-model bypass,
fallback, task continuity, scoped credentials, revocation and no-replay behavior:

```sh
python -m django test --settings=nexus_personal.tests.settings \
  nexus_personal.tests.test_aggregate_auto \
  nexus_personal.tests.test_explicit_models \
  tests.test_aggregate_auto tests.test_system_one tests.test_sse_protocol
```

The HTTP upstream in this suite is a deterministic fixture, not a paid external
System One service. Operators must supply valid upstream credentials and verify
their own model catalog and network connectivity.
