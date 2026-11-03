# Full chat endpoint health check fix

Sources configured with a full URL such as `/v1/chat/completions` previously
queried `/v1/chat/completions/models` during catalog health checks. A 404 caused
an unnecessary chat probe and marked otherwise working Sources as degraded.

Catalog checks now use the sibling `/v1/models` path. Base URLs, explicit catalog
URLs and trailing slashes remain supported. Authentication and outbound address
validation are unchanged; authentication failures do not trigger model probes.

Regression tests exercise a real local HTTP fixture and verify that successful
catalog checks never invoke inference. Hosted acceptance recovered six MiniMax
Sources, including another background-worker health-check round. A public Router
returned the requested short text over both JSON and SSE, including `[DONE]`.
This validates connectivity and protocol completion, not general model latency.
