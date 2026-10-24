# Cloud email registration and password recovery

New email accounts use the verified email address as the username. Addresses are
normalized with Django username normalization, trimmed and lowercased, and limited
to 150 characters. Existing OAuth accounts are not renamed.

## Flow and APIs

All routes are Cloud extension routes under `/api/v1/`. Anonymous POST requests
require a valid CSRF cookie/header pair, including requests before login.

| Route | Request | Successful response |
| --- | --- | --- |
| `auth/email/register/` | `email` | Generic 202; sends proof only for an eligible address |
| `auth/email/verify/` | `token`, `password`, `password_confirm` | 201; creates account and workspace |
| `auth/email/password-reset/` | `email` | Generic 202; sends proof only for one matching active account |
| `auth/email/password-reset/confirm/` | `token`, `password`, `password_confirm` | 200; replaces password |

The first step creates no user and accepts no password. A verification email links
to `/login#email-action=signup&token=…` or `email-action=reset`. The browser removes
the fragment immediately, including when a second link is opened in the same tab.
The user sets the password after proving access to the mailbox, then signs in
using the existing login flow. Links expire in 30 minutes and are single use.

Only the token SHA-256 digest is stored. The raw token is not returned by the API
or placed in a URL query string. Confirmation responses use no-store/no-referrer.
Passwords require 8–128 characters, matching confirmation and Django validation.
Email admission uses a database transaction lock shared with OAuth registration.
Reset proofs are bound to the current password hash; resets retire old user JWTs,
DRF tokens and Django sessions. Service API credentials are not rotated.

## Activation

`NEXUS_EMAIL_SIGNUP_ENABLED=0` disables registration and password recovery. When
enabled, production additionally requires the SMTP backend and a configured sender.
The public bootstrap exposes `authentication.email.signup_enabled`,
`password_reset_enabled`, and `username_is_email`; the UI follows these capabilities.
The capability checks configuration, not final delivery or live relay availability.
Operators must verify actual delivery before enabling it and monitor the relay.

Default local-memory mail does not expose the feature in production. Test backends
require the explicit nonproduction `NEXUS_EMAIL_AUTH_ALLOW_TEST_BACKEND` override.
SMTP exceptions return a generic 503 and discard the undelivered proof.
Limits are 3 requests per mailbox/hour, 30 per client IP/hour, and 200 outgoing
requests/hour globally. Cache failures fail closed. Use a shared cache for multiple
workers and configure the trusted proxy chain so client IPs are accurate.

For the current Postfix/SMTP relay deployment, see [email_operations.md](email_operations.md).
Local SMTP acceptance does not prove relay delivery or inbox placement.

## Deployment and verification

Apply `accounts.0003_email_account_challenge`, deploy the backend and built frontend,
then restart the web service. The migration is additive; do not drop the table as
part of rollback after real users have begun registration.

Backend coverage: `tests.test_email_registration`, `tests.test_accounts`,
`tests.test_google_oauth`, and `tests.test_github_oauth` passed together (41 tests)
against isolated PostgreSQL with real migrations. Frontend build and
`node tooling/emailRegistrationSmoke.mjs` passed for registration, recovery,
fragment removal, password mismatch, Chinese localization and capability gating.
These tests do not replace delivery verification with the selected SMTP relay.
