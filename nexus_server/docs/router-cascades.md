# Cascaded aggregation Routers

Aggregation Routers can map a model name to an output of another deployed
Aggregation Router, or to the default output of an Execution Router. Execution
Routers retain their existing Model Pool configuration.

```text
company-api / coding
  -> engineering-api / fast
     -> fast-execution / model-name
        -> Model Pool -> Source
```

## Configure

1. Deploy the child Router and verify its model catalog.
2. Open the parent Router, choose **Add API model**, then select the child Router.
3. For an aggregation child, select **Child model**. Enter the public model alias.
4. Deploy the parent. Call its existing OpenAI-compatible endpoint with the alias.

An aggregation child can export multiple distinct model mappings to the same
parent. Execution children retain the single-default-output mapping rule. Changes
to or removal of a child alias invalidate that export; an old parent mapping does
not silently switch to another model. Restore the alias or update the mapping.

## Graph and authority

- Maximum depth is **four Router levels including the final Execution Router**.
- Edges stay within one Organization. Self references, cycles and excessive depth
  are rejected on insertion, re-enabling and deployment. Ancestors are included in
  depth validation. A tenant row lock serializes concurrent graph mutations.
- Runtime and capability traversal also enforce depth and cycle bounds, including
  configurations created outside the management API.
- The entry credential still limits entry Router, model aliases and operations.
  It cannot call a child Router directly just because a permitted parent maps it.
- Every traversed child must still be deployed and callable by the authenticated
  account. Restricted Router credentials use their validated owner; legacy API
  keys use their issuer. Revocation and disabled outputs are rechecked on every
  request, including tool continuations. Model Pool and Source authorization stays
  with the existing edition-specific Gateway.

## Exactly one Auto decision owner

Named mappings do not run the parent selector. For example, mapping `smart` to
the child's `auto` explicitly delegates selection to that child. Its rules or
Decision Model choose the final model; the parent only forwards the mapping.

When the caller requests the parent's own `auto`, only routes ending in explicit
model outputs are eligible. Any route that would trigger another Auto selector
(including an Execution Router's System One Auto output) is excluded. If no such
route exists, the call fails without starting an inference. Add explicit model
mappings, or call the named delegated alias instead.

Successful tool continuations retain the decision owner's eligible selection.
New user tasks are reassessed. Invalid/unavailable Decision Model responses use
the existing rule fallback. Trace `path` contains all Router hops; the selector's
trace includes `decision.owner_router_id`. Nested traces preserve leaf/source
fallback details.

## Accounting and protocol

Cascading is an internal resolution operation, **not an HTTP call to a child API**.
Admission, reservations, inference, streaming finalization and usage settlement
run once at the original Gateway entry. The entry Router owns the request log and
charge; intermediate Routers do not add another request charge. Existing Source
retry accounting and decision-overhead reporting are unchanged.

`/models`, `/model/info` and health catalogs follow the same bounded output graph.
Capacity aggregation remains conservative; capability metadata does not invent
limits for unknown Sources. The standard stream contract remains unchanged.

## Upgrade

Run normal Django migrations before starting the new version. Enterprise uses
`routers.0010_aggregation_exports`; Community uses
`routers.0004_aggregation_exports`. These create stable output records for existing
aggregation aliases; they do not change existing bindings or issue credentials.
Future edits synchronize exports transactionally. No new client configuration or
API key format is needed.

## Validation

`nexus_personal.tests.test_router_cascade` exercises real fixture HTTP inference,
stream completion, scoped credentials, native Decision Model selection, successful
continuation, invalid-choice fallback, metadata, graph limits and one receipt / one
attempt / one usage record. Hosted `tests.test_router_cascade_billing` additionally
checks the single wallet debit and Key usage accounting, descendant access denial
and cross-Organization rejection.
