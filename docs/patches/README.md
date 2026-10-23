# Hosted authorization Router visibility fix

## 2026-10-23: signed-in user's Router list

[Source and regression-test patch](2026-10-23-authorization-router-visibility.patch)

This patch targets the Organization-enabled Nexus Cloud host. Its MCP model
authorization page could show an empty Router list for an administrator who
could already use those Routers elsewhere in the application. The authorization
selector enumerated active memberships only, while the normal Organization
selector also supports administrator access without an explicit membership.

The fix uses the configured tenancy host's `visible_tenants(user=user)` service
and retains active Organization, deployed Router, live project, Router use
permission and output-model checks. The identity remains the user signed into
the browser. Opening an authorization link does not grant the publisher's
account access or issue a credential; explicit confirmation is still required.

The patch includes regression tests for administrators without memberships and
for visible Routers without use permission. The complete browser-account
authorization suite passed 13 tests against an isolated PostgreSQL database.
The deployed host also returned the expected Router choices after the fix.

### Distribution boundary

This repository's Personal distribution intentionally disables hosted Router
credential issuance in `nexus_server/apps/agents/credential_router.py`. It does
not ship the Organization host or its browser-account authorization test suite.
Consequently this patch is published as hosted integration source, **not applied
to the Personal runtime**. It does not enable Router delegation in Personal.
Personal deployments can continue to authorize caller-owned provider credentials.

For an Organization-enabled checkout containing the target module and test suite,
review the patch and apply from its repository root:

```sh
git apply --check /path/to/2026-10-23-authorization-router-visibility.patch
git apply /path/to/2026-10-23-authorization-router-visibility.patch
cd nexus_server
python manage.py test tests.test_agent_account_authorization
```

Use the host's configured test settings and an isolated PostgreSQL test database.
No schema migration or frontend change is required.
