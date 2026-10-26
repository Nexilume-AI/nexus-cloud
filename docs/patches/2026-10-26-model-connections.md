# Hosted model connections and batch Agent permissions

This release publishes the complete hosted implementation as a reviewable source
patch, including models, migration, management UI, streaming proxy, integration,
tests and SDK documentation. Source commit: `f542f8fc903b12463c1fb979a95834239e926b79`; base: `5cc9bcdd`.

The patch targets the Organization-hosted source composition used by
cloud.nexilume.com. The Personal distribution in this repository deliberately
does not expose Organization identity, Router delegation or credential-vault
models. Do not apply this patch directly to the Personal tree or replace its
authorization adapters with Organization code. The hosted API and SDK contract
are documented in [Model connections](../model-connections.md).

This includes existing interactive-view schema prerequisites (0070–0072) and the
new additive 0073 migration. No current grants are silently upgraded. Original
upstream keys stay Cloud-side for reusable connections, with per-Agent scoped
runtime tokens, atomic request counters and expiry/revocation checks.

Validation: 80 isolated PostgreSQL regression tests, migration reverse/forward
round trip, browser response-envelope/CSRF state test, TypeScript/Vite production
build, and public authenticated CRUD/batch/reuse/quota/revocation acceptance.
Public acceptance used a disposable account and synthetic provider credential;
no paid model generation or performance claim is made.
