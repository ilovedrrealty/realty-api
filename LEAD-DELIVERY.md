# New-lead delivery operations

This outbox applies to leads created after the feature is enabled. It does not
scan, backfill or replay historical leads. The migration creates two empty,
private RLS-enabled tables; it sends nothing.

## Cutover and startup

Add `lead_delivery_enabled: bool = False` to `Settings` and document
`LEAD_DELIVERY_ENABLED=false` in `.env.example`. Import `LeadDeliveryJob` and
`LeadDeliveryWorkerState` in `models/__init__.py`. Include
`routes.lead_delivery.router` in the app. Start/cancel-and-await
`services.lead_delivery.worker_loop(SessionLocal)` in the application lifespan.
The loop sleeps 15 seconds before polling. A paused worker performs no DB or
provider operations. `run_batch` is callable for isolated tests and caps a batch
at ten jobs. Lifespan cancellation sets a cooperative stop signal, stops further
claims/dispatch, and drains the shielded active batch before returning. An
already-started provider request records its outcome; a proven-unsent claim is
restored with its original retry budget and timestamps. Provider HTTP timeout
phases are explicit (connect 3s, read 10s, write 5s, pool 1s). Hard process loss
still follows the interrupted-lease review path rather than replaying work.

`LEAD_DELIVERY_ENABLED=false` preserves the existing immediate lead-alert path.
`true` atomically stores the newly accepted lead and immutable notification jobs,
then leaves provider dispatch to the worker. The final production release must
enable and verify the worker; it must not end with new alerts silently paused.

The first enabled poll writes a private worker heartbeat even when no jobs exist.
Verify `enabled=true`, fresh `worker.last_poll_at` and
`worker.last_completed_at` through the Admin summary without creating a lead or
sending a test notification. Migration/source/backup review precedes activation.

## What is recorded

Each event has independent CRM contact, tag, note and follow-up-task records, plus
one email record per unique configured notification recipient. The note is
skipped if it has no content. CRM follow-ups wait for a confirmed contact ID;
notification emails do not wait for CRM success. Existing dual email routing is
retained. Recipient addresses and full request bodies are private snapshots and
are never returned by the operational status API.

Intake idempotency uses the existing `submission_id` unique key. Repeated requests
with that key produce one lead and one set of jobs. Requests without an intake
key remain separate events. The queue also enforces one job per lead/step. A
transaction marker prevents the enqueue helper from accepting an already
committed historical lead.

Booking-created lead alerts use the same transaction/cutover. Guest booking
confirmation, owner booking notifications, booking rules, payment authorization,
capture and payout logic remain separate and unchanged. Legacy `/leads/capture`
keeps its existing email-only provider routing.
For an actual Booking row, its `lead_id` link is assigned after flush and before
the same commit as the Lead and notification jobs. There is no second intake
commit for linkage. Database write failures roll back into a controlled 503
without logging or returning private outbox statement parameters.

## Status and retries

- `pending`: stored and waiting for a worker/dependency.
- `processing`: exclusively claimed with a token and a 120-second lease.
- `retry_wait`: known-safe retry scheduled with a bounded delay.
- `accepted`: provider acknowledged the request. For email this is **not** proof
  of recipient inbox delivery; no bounce/delivery webhook is assumed.
- `failed`: a known rejection or configuration problem requiring attention.
- `needs_review`: an operation may have succeeded externally; no blind replay.
- `skipped`: integration disabled at intake, or no note content.

Claims use row locks, SKIP LOCKED and a conditional token update. Interrupted
processing records become `needs_review`, rather than being automatically
reclaimed. CRM compatibility fields are derived from durable step outcomes and
serialized per lead so concurrent completions do not overwrite each other.

Each operation has at most four dispatch attempts. Connection failures before
request establishment and explicit rate-limit rejections can be delayed/retried.
Uncertain CRM POSTs, invalid contact acknowledgements and unclassified responses
require review. A confirmed duplicate contact ID is reused, and tags are added
separately rather than replacing existing tags.

Resend uses a stable event/step idempotency key with the exact stored body.
Uncertain email attempts are retried only inside a conservative 23-hour window
from their first attempt; Resend documents 24-hour key retention. Expired-window
or conflicting-payload outcomes require review. Changing a configured recipient
or CRM destination blocks queued delivery for review rather than transferring
private information to a different destination.

Admin retry only requeues a known-rejected, non-uncertain failed step below the
attempt limit. It does not call a provider, bypass a rate-limit delay, reset an
idempotency key, retry an accepted job or replay a whole contact workflow.
Review-needed outcomes require provider inspection; this release does not invent
delivery evidence or offer an unsafe automatic resend.

## Private Admin API

- `GET /admin/leads/delivery-summary`
- `GET /admin/leads/{lead_id}/delivery`
- `GET /admin/lead-deliveries?status=...&lead_id=...&limit=25&offset=0`
- `POST /admin/lead-deliveries/{job_id}/retry`

Retry requires an explicit empty JSON object (`{}`); missing, extra-field and
simple form/text bodies are rejected before any requeue. The authenticated
dashboard sends JSON through its existing Axios client. The shared mutation
guard requires an explicit JSON media type and validates complete origins;
cookie-authenticated mutations require a trusted Origin. Bearer clients remain
supported, while any supplied hostile/null Origin is rejected.

All endpoints require the existing Admin role and return `no-store`, including
authentication failures, validation failures and controlled error responses.
Validation responses do not echo submitted path or query values. The summary
uses one read-only statement for counts, step buckets and worker timestamps.
List/detail
queries do not fetch private payload columns. Public receipts are unchanged.
Error records contain controlled stage/status codes, not provider response
bodies, contact details or authorization headers. Missing email configuration
does not enter the legacy DEV email-printing path.

The dashboard `LeadDelivery` view consumes only this metadata. It has English
and Spanish labels, worker freshness/configuration indicators, bounded status
and inquiry filters, a per-inquiry step view, and an explicit confirmation before
queuing a server-approved safe retry. Reads and page refreshes never dispatch a
provider request. A failed requeue response prompts a status refresh rather than
an automatic POST retry. The frontend distinguishes provider acknowledgement
from recipient inbox delivery and explains why uncertain outcomes need review.

## Verification

`tests/test_lead_delivery.py` uses a random schema in explicitly loopback-only
Postgres and `httpx.MockTransport`. Tests cover atomic storage/rollback,
concurrent intake/claims/workers, interrupted/ambiguous operations, retry windows
and limits, destination/payload changes, Admin privacy, migration idempotency/RLS,
booking cutover/linkage, shutdown drain, unsent-budget restoration and historical
non-replay. No real provider is used by tests.

Official provider reference:
https://resend.com/docs/dashboard/emails/idempotency-keys

The existing HighLevel API version and contact-create/duplicate-ID behavior are
retained. Its contact upsert documentation also warns that a tags field replaces
current tags; this implementation therefore uses the separate add-tags endpoint:
https://marketplace.gohighlevel.com/docs/ghl/contacts/upsert-contact
