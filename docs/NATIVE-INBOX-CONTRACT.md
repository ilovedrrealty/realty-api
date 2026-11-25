# Native inbox contract — approved implementation contract

Parent backend: `8957b18520d15dac503e0e4a5ce4b2455135d601`.
No provider is activated by this implementation. Root approved this policy and
cursor contract for implementation on 2026-11-25. Source still requires full
review and isolated acceptance before any freeze, push or migration.

## Policy

* Actors must be current active, verified registered users. There are no new
  accounts or role grants.
* Native conversations have two primary registered participants. Admins may
  review native messages through an audited review session and may participate
  as their own Admin identity in a currently writable conversation.
* A buyer can start a conversation only with their currently assigned eligible
  Realtor or an eligible Admin. Both directions enforce the rule. Eligibility
  means current active/verified `realtor` or `admin`; historical lead fallback is
  never a messaging permission.
* A conversation created for a buyer retains that buyer as its brokerage
  subject. The buyer retains their own history. A former assigned Realtor loses
  read and write access immediately on reassignment; a new Realtor does not
  automatically receive the old direct-message bodies. Admin oversight remains.
* Other registered members can start conversations with members who opt into
  member messaging. A members-visible social profile supplies the initial
  default; an explicit Inbox preference overrides it. Private profiles are not
  silently published. An existing conversation survives later discovery opt-out.
* Existing social blocks and new inbox blocks prevent new conversations and
  further entries in either direction. Blocking does not delete retained
  history. A blocked pair is read-only, including for Admin interventions.
* Recipient discovery returns only `id`, `user_code`, `display_name`, `role`, and
  a finite relationship label. It never returns email, phone, profile drafts,
  notes, or imported trade-contact data. Names never fall back to email.
* Base roles remain `buyer`, `owner`, `realtor`, `admin`. Approved professionals
  with base role buyer keep existing authorized service conversations; no
  implicit professional exception or role change is introduced in this draft.
* Legacy service negotiation and awarded-work transcripts are available through
  audited, read-only Admin review. Their original participant endpoints and
  project/custody/grant rights do not change. There is no owner/provider
  impersonation and no additional access for public or other registered users.
* Every thread/header and preferences response identifies policy
  `native-inbox-v1` and discloses Admin oversight. Legacy review responses clearly
  identify their original context and read-only status.
* New message kinds are `message`, `manual_whatsapp`, and `manual_call`.
  Manual entries identify the real recording actor and explicitly state they
  are manual records, never proof of sending, delivery, a completed call, or a
  reply from the counterparty. All entries are visible to authorized thread
  readers; this is not a hidden staff-notes feature.

## Common contracts

All endpoints require the existing website session and return `no-store`,
`no-cache`, `nosniff`, and `Vary: Cookie, Authorization`. JSON mutations use the
existing same-origin protection and rate limits. Errors are finite generic
messages and never echo private payloads.

All mutation bodies include `submission_id: UUID`. Each actor can use a
submission ID for exactly one action and normalized payload. A matching retry
returns the original receipt without applying the action again; conflicting
reuse returns 409. There are no automatic duplicate sends.

Every mutation returns the same minimal receipt:

```json
{"submission_id":"UUID","action":"entry_created","target_id":"UUID"}
```

Actions: `conversation_opened`, `entry_created`, `read_marked`,
`visibility_saved`, `block_saved`, `review_opened`. The target is respectively
the conversation, entry, conversation, actor, blocked member, or review audit.
`GET /messaging/commands/{submission_id}` resolves the actor's receipt after
current authorization is rechecked. Unknown/unavailable receipts return 404.
Clients reload the authoritative resource after a receipt; receipts do not
contain message text, recipient contacts, or stale settings values.

Member projection:

```ts
type Member = {
  id: string; user_code: number | null; display_name: string;
  role: 'buyer' | 'owner' | 'realtor' | 'admin';
}
```

Directory/list pagination is `page` 1–1000, fixed `page_size: 30`; responses
include `total`. **Message history has no page-number ceiling.** All history
endpoints accept an optional opaque `cursor` (maximum 512 characters), return
at most 30 newest-first entries and provide `cursor`/`next_cursor` plus `total`.
Clients keep a cursor stack for Previous and pass back the returned cursor
unchanged for Older. Native cursors bind the conversation and `before_sequence`;
legacy cursors bind the source, timestamp and message UUID. Cursor position is
never authorization. New arrivals do not shift an older-history boundary.
Native sequence is a nonnegative safe JSON integer stored as BIGINT; legacy
sequence is null. UI may reverse a loaded page for display. Dates are UTC ISO
timestamps. All input is bounded plain text, rendered as text.

## Routes

* `GET /messaging/policy`: `{policy_version, admin_oversight: true, channels}`.
  Channels are `in_app`, `manual_whatsapp`, `manual_call` true; `email`, `gmail`,
  `whatsapp_sync`, `sms`, `telephone_sync` false.
* `GET /messaging/preferences`: `{visibility: 'private'|'members', version,
  policy_version, admin_oversight: true}`. Version is 0 until explicitly saved.
* `PUT /messaging/preferences`: `{submission_id, expected_version, visibility}`.
* `GET /messaging/recipients?q=&page=1`: paginated `{items, total, page,
  page_size}`. Each item is Member plus `relationship`:
  `admin`, `assigned_agent`, `assigned_buyer`, or `member`.
* `GET /messaging/blocks?page=1`: paginated own inbox blocks as Member items.
* `PUT /messaging/blocks/{user_id}`: `{submission_id, blocked: boolean}`.
* `POST /messaging/conversations`: `{submission_id, recipient_id}`. Opens the
  existing direct pair or creates it once. No client-selected author or audience.
* `GET /messaging/conversations?view=mine&page=1`: paginated summaries below.
  `view=oversight` is Admin-only and never includes unaudited message previews.
* `GET /messaging/unread`: `{conversations: integer, entries: integer}` for the
  actor's currently authorized primary-participant conversations only.
* `GET /messaging/conversations/{id}`: a fresh summary; nonparticipant Admins
  receive metadata with `read_only_reason: 'review_required'`.
* `GET /messaging/conversations/{id}/entries?cursor=`: ThreadPage for a currently
  authorized primary participant. Nonparticipant Admins use the review endpoint.
* `POST /messaging/conversations/{id}/entries`: EntryWrite below. A
  nonparticipant Admin supplies their unexpired `review_id` for this native
  conversation. Legacy review sessions cannot be used to send.
* `PUT /messaging/conversations/{id}/read`: `{submission_id,
  last_read_sequence}`. Cursor advances monotonically and cannot exceed the
  conversation sequence. It does not mark another participant's messages read.
* `GET /messaging/admin/legacy?page=1`: Admin-only paginated metadata for awarded
  and negotiation histories; source references, count and last timestamp only.
  Response is `{items: [{source: Source, total_messages: number,
  last_entry_at: string}], total, page, page_size: 30, policy_version,
  admin_oversight: true}`. No body preview, property title or contact field.
* `POST /messaging/admin/reviews`: `{submission_id, source, reason}` where
  reason is `oversight` or `support`. Returns a `review_opened` receipt whose
  target is the new audited review ID. The review expires after ten minutes.
* `GET /messaging/admin/reviews/{review_id}/entries?cursor=`: ThreadPage for that
  same current Admin and source while the review is valid. Native review may be
  writable under current pair rules; both legacy kinds are always read-only.
  A newly opened review of the same source can resume an older cursor after an
  earlier review expires; review expiry never truncates retained history.

```ts
type Source = {
  kind: 'native' | 'service_award' | 'service_negotiation';
  id: string; recipient_id: string | null;
}
type Summary = {
  id: string; participants: Member[]; brokerage_subject_id: string | null;
  created_at: string; last_entry_at: string | null; last_sequence: number;
  last_entry_preview: string | null; unread_count: number; is_observer: boolean;
  can_send: boolean; read_only_reason: string | null;
  policy_version: 'native-inbox-v1'; admin_oversight: true;
}
type EntryWrite = {
  submission_id: string;
  kind: 'message' | 'manual_whatsapp' | 'manual_call'; body: string;
  occurred_at?: string | null;
  reported_direction?: 'incoming' | 'outgoing' | null;
  call_outcome?: 'connected' | 'voicemail' | 'missed' | 'no_answer' | 'declined' | 'unspecified' | null;
  review_id?: string | null;
}
type Entry = {
  id: string; sequence: number | null; kind: EntryWrite['kind']; body: string;
  author: Member; context_role: 'owner' | 'provider' | null;
  created_at: string; occurred_at: string | null;
  reported_direction: 'incoming' | 'outgoing' | null;
  call_outcome: EntryWrite['call_outcome'];
  delivery_state: 'stored_in_app' | 'manual_record' | 'legacy_recorded';
}
type ThreadPage = {
  source: Source; conversation: Summary | null; items: Entry[];
  total: number; cursor: string | null; next_cursor: string | null; page_size: 30;
  read_only: boolean; read_only_reason: string | null;
  policy_version: 'native-inbox-v1'; admin_oversight: true;
  review_id: string | null; review_expires_at: string | null;
}
```

For `message`, manual fields must be absent/null. For either manual kind,
`occurred_at` and `reported_direction` are required. `call_outcome` is required
only for `manual_call`. Body is 1–2,000 Unicode code points after trimming;
ordinary internal newlines, carriage returns and tabs are allowed. NUL and other
unsafe C0/C1 control characters are rejected. There is no HTML, file upload, provider send,
or external channel credential in this version.

Read-only reasons are finite: `blocked`, `assignment_changed`,
`participant_unavailable`, `buyer_relationship_required`, `review_required`,
`legacy_read_only`. Unauthorized resources return 404 rather than disclosing
private metadata; malformed input is 422, conflicting commands/versions 409,
expired reviews 410, rate limits 429, and unavailable storage 503.

The new history routes use keysets even for legacy Admin review. Original
participant service/negotiation endpoints and their authorization remain
backward compatible. Existing EN/ES service and negotiation privacy copy must
also disclose audited Admin oversight when this policy is published; a notice
only on the new Inbox is insufficient.

## Data and concurrency boundaries

Proposed new private tables: `inbox_preferences`, `inbox_blocks`,
`inbox_conversations`, `inbox_entries`, `inbox_read_states`, `inbox_commands`,
`inbox_audit`. No existing table is rewritten by inbox actions. The seven tables
enable RLS with no client policies and revoke public/anon/authenticated access;
the established private backend database connection remains the only path.

All current actor/pair roles, active/verified state and assignments are fetched
fresh under a globally ordered User lock set; native conversation locks follow
User locks. Preference/block mutations use the same ordering. Message sequence,
entry, command and audit are committed together. Read cursor mutation is an
explicit idempotent command, not an effect of GET. Admin review opening is an
explicit audited command; its subsequent GETs do not mutate legacy history.

Hosted acceptance must preserve the complete legacy 99-table catalog/data/
sequences, including 82 protected owner contacts and 46 private professional
drafts. Current production contact/source counts reported by root are
5,633/5,640 after the cooperative import; no production data is used or changed
by local preparation. No local database, heavy build, push, migration or provider
call is authorized by this contract.
