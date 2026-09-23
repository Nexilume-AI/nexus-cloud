# Runtime recovery source synchronization

Reviewed on 2026-09-23 against Community base `de373ad`.

The shared Cloud trust, Provider and Gateway changes come from source revision
`ac180e7`; the recoverable Browser error mapping is an additional reviewed
shared-code delta. Only the selected shared runtime files, their tests and
Provider UI changes were synchronized. No mixed repository history, enterprise
implementation, credentials, research data or generated build output is copied.

The TLS regression cases use the Community owner fixture. Source eligibility
uses a real Community owner, persisted Provider/Source and loopback HTTP fixture,
not the enterprise access backend. Browser error mapping uses a bounded unit
fixture; it is not presented as a live browser acceptance test.

## Verification

Run from `nexus_server`:

```sh
python -m django test \
  nexus_personal.tests.test_cloud_trust_rotation \
  nexus_personal.tests.test_provider_connection_http \
  nexus_personal.tests.test_error_http \
  nexus_personal.tests.test_provider_idle_recovery \
  nexus_personal.tests.test_browser_error_mapping \
  tests.test_hosted_cloud_trust tests.test_provider_doh_recovery \
  tests.test_gateway_agent_protocol \
  --settings=nexus_personal.tests.runtime_settings --noinput
```

Result: 66 passed. This test configuration uses its own temporary SQLite
database, not a running installation's database.

Run from `nexus_web`:

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm run typecheck
npx vitest run src/pages/ProvidersPage.test.ts src/localization/hardcoded.test.ts
npm run build:community
```

Result: 4 unit tests passed, type checking passed, production build passed.
The production build still reports a large-chunk size advisory.

Gitleaks 8.30.1 scanned the independent source tree with redacted output. Its
only finding is the existing fake CanonicalModel key in
`tests/test_source_pool_targeting_regression.py:41`; no new secret suppression
rule was added.

## Operation

Restart the Community Server and runtime workers using the installation's
normal launcher. Existing deployments without a stored Cloud trust fingerprint
receive one reconciliation replacement. Subsequent passes do not repeatedly
replace a healthy generation. A failed historical Run is retained for audit;
the user decides whether to submit another turn.

No database migration, SDK release, OpenWrt firmware update, public repository
visibility change or package-registry publication is part of this update.
