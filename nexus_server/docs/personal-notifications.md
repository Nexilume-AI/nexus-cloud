# Work Inbox and desktop notifications

`Work Inbox` is a safe index of work, not a second workflow engine. The source
Run, Job, approval, alert, Runtime, Computer, or Mobile remains authoritative.
Reading, snoozing, archiving, opening, and receiving Push never approves,
answers, retries, or changes that source.

## Isolation and privacy

- `InboxItem` contains fixed product copy, a source pointer, safe resource label,
  Project, audience, permission requirement, state, priority, and a server-owned
  navigation key. It never stores prompts, errors, amounts, terminal output,
  credentials, device content, or source-provided URLs.
- Personal items are visible only to their user. Shared role items recheck the
  user's current permission, Project access, and requester identity on every
  list, read, open, and Push delivery. Requesters never see their own approval
  item, even when they otherwise hold the approval permission.
- API keys, service accounts, and anonymous callers cannot use personal Inbox or
  Web Push. The current Project selector does not hide other authorized Projects
  in the same Organization; Project remains an explicit filter.
- `InboxReceipt` holds read, snooze, and archive state per user. Read-only list and
  summary requests do not create receipts. Unresolved role work cannot be
  archived. Source recovery resolves the existing item in place.
- Completed and resolved items are retained for 90 days. Unresolved action and
  failure items do not expire because of age.

Legacy `/api/v1/notifications/*` endpoints remain available during rollout. New
clients use `/api/v1/inbox/*`; both surfaces continue to keep source business
state separate from receipt state.

## Web Push

The standard Windows launcher enables Web Push by default and creates a durable
local P-256 VAPID key under `.local/nexus-cloud/credentials/web-push`. The key is
reused across restarts and its private material is never printed. Set
`NEXUS_WEB_PUSH_ENABLED=0` before launch to opt out.

Production Compose remains explicitly disabled until the operator supplies the
following HTTPS/VAPID configuration. In every environment, each user must still
click `Enable desktop notifications` before the browser permission prompt is
shown. Subscription endpoints and browser keys are encrypted at rest and never
returned by the API.

```text
NEXUS_WEB_PUSH_ENABLED=1
NEXUS_VAPID_PUBLIC_KEY=...
NEXUS_VAPID_PRIVATE_KEY=...
NEXUS_VAPID_SUBJECT=mailto:ops@example.com
```

The root service worker `/nexus-service-worker.js` handles only Push, notification
clicks, and Inbox invalidation. It does not cache private API responses. Payloads
contain an opaque Inbox item ID, a relative `/inbox` URL, a deduplication tag, and
one of four fixed generic messages. They never contain a Project, resource,
amount, error, prompt, input, output, endpoint, or token.

Every delivery rechecks role permission immediately before sending. HTTP 404/410
disables an expired browser endpoint; other failures retry with bounded
exponential backoff. Quiet hours use the user's IANA timezone, including
cross-midnight and DST transitions. Logging out disables the current browser
subscription before clearing local authentication.

## Runtime and rollout

Production uses Celery Beat for the 60-second reconciler and 15-second delivery
dispatcher. The standard local Cloud startup script runs the equivalent
`run_work_inbox_worker` process automatically.

Deployment order:

1. Apply `notifications.0003_inbox_scale` (after `0002_work_inbox`).
2. Start API, Worker, and Beat (or the local Inbox worker).
3. Deploy the Web bundle and root service worker.
4. Run the reconciler. It imports the previous receipt state, recent personal
   work, and all unresolved role/operations work idempotently.
5. In production, configure VAPID and enable Web Push only after HTTPS and workers are healthy. The standard local launcher performs this configuration automatically.

Push configuration failure reports `Degraded` through Inbox preferences and
summary while Inbox itself remains fully usable. Existing Monitoring email
delivery is independent and is not replaced by Work Inbox.

## Large Inbox operation

- List and summary requests are read-only. Caller-specific receipt subqueries,
  SQL counts and source-state expressions replace historical-item loops. Role
  checks are shared by distinct permission/Project, not repeated per item.
- Pages contain 30 items and use signed keyset cursors bound to caller,
  Organization and filters. `include_counts=0` avoids recomputing the separate
  summary. `GET /api/v1/inbox/items/{id}/` resolves an authorized notification
  outside the current page without falling back to unrelated work.
- The Web keeps at most ten pages in memory, debounces search by 300 ms, and
  pauses/aborts SSE when hidden. The stream is an async iterator on ASGI: its
  first frame is immediate; heartbeats only read the revision, not the summary.
- Read-all processes at most 500 items per transaction. Follow its signed
  `next_cursor` until null. The snapshot excludes later arrivals and every batch
  rechecks permission. Old clients receive a bounded first batch and must follow
  the continuation to acknowledge more than 500 items.
- Reconciliation uses durable checkpoints with a five-minute overlap, bounded
  daily repair and a PostgreSQL session advisory lock. Per-source work is capped
  by `NEXUS_INBOX_RECONCILE_BATCH_SIZE` (default 100, maximum 500). Presence and
  active-item sweeps rotate rather than starving items beyond the first batch.
  Unchanged projections do not write `updated_at` or trigger false invalidation.
  Completed/resolved retention deletes at most 500 parent items per cycle;
  unresolved work remains available, as before.
- Push workers claim rows using `skip_locked`, a 60-second generation lease,
  and a bounded concurrency setting (`NEXUS_INBOX_PUSH_CONCURRENCY`, default 4,
  maximum 8). A batch claims only enough work to send immediately. Eight failed
  attempts exhaust a delivery. A new source event or explicitly re-enabling the
  device can requeue it. HTTP 404/410 still disables the expired device.
- Web Push is **at-least-once**, not exactly-once: a process can fail after the
  external provider accepts a message but before the database records success.
  Stable item tags replace duplicate desktop notices where the browser supports
  that behavior. Leases prevent concurrent claims and stale completion writes;
  they cannot make an external provider and PostgreSQL one atomic transaction.

Deploy the migration before restarting API and Inbox/Beat workers. Do not run
old workers alongside the new lease protocol. No Agent, Cloud enrollment or
Computer configuration change is required.
