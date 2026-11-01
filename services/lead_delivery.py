"""Durable delivery for new lead events, with explicit manual-only retries.

No import, migration, GET or retry request sends anything. Only an explicitly
enabled worker calls providers. HTTP success is provider acceptance, NOT proof
that a notification reached the recipient's inbox. An uncertain provider outcome
is always held for review, even when the provider supports idempotency keys.
"""
import asyncio
import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from uuid import UUID, uuid4

import httpx
from sqlalchemy import and_, case, event, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from config import settings
from models.lead import Lead
from models.lead_delivery import LeadDeliveryJob, LeadDeliveryWorkerState
from models.listing import Listing
from models.site_settings import SiteSettings
from models.user import User
from utils import ghl
from utils.email import build_lead_notification, build_new_user_notification, parse_notification_recipients
from utils.customer_messages import customer_message
from utils.phone import normalize_phone

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 4
LEASE_SECONDS = 120
POLL_SECONDS = 15
MAX_BATCH = 10
PROVIDER_TIMEOUT = httpx.Timeout(connect=3, read=10, write=5, pool=1)
BACKOFF_SECONDS = (60, 300, 900, 3600)
CRM_STEPS = ("crm_contact", "crm_tags", "crm_note", "crm_task")
SAFE_REJECTIONS = {400, 401, 403, 404, 422}
PROVIDER_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")
SAFE_ERROR_CODE = re.compile(r"[a-z0-9_]{1,120}")
MANUAL_RETRY_PREFIX = "manual_retry_authorized:"
MANUAL_RETRY_PATTERN = r"^manual_retry_authorized:[a-z0-9_]{1,120}$"


@event.listens_for(Session, "after_flush")
def _remember_new_lead_events(db, _context):
    ids = {row.id for row in db.new if isinstance(row, Lead) and row.id is not None}
    if ids:
        db.info.setdefault("new_lead_delivery_ids", set()).update(ids)
    user_ids = {row.id for row in db.new if isinstance(row, User) and row.id is not None}
    if user_ids:
        db.info.setdefault("new_signup_delivery_ids", set()).update(user_ids)


@event.listens_for(Session, "after_transaction_end")
def _forget_committed_lead_events(db, transaction):
    if transaction.parent is None:
        db.info.pop("new_lead_delivery_ids", None)
        db.info.pop("new_signup_delivery_ids", None)


def utcnow():
    return datetime.now(timezone.utc)


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def enabled() -> bool:
    return bool(getattr(settings, "lead_delivery_enabled", False))


def signup_enabled() -> bool:
    """The existing worker AND the new customer-email feature must be enabled."""
    return enabled() and bool(getattr(settings, "customer_messages_enabled", False))


def _single_recipient(value):
    try:
        recipients = parse_notification_recipients(value or "")
        return recipients[0] if len(recipients) == 1 else None
    except Exception:
        return None


def payload_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def notification_recipients(db: Session) -> list[str]:
    row = db.query(SiteSettings).filter(SiteSettings.id == 1).first()
    value = (row.data or {}).get("notify_email") if row else None
    return parse_notification_recipients(value or settings.notify_email)


def property_snapshot(db: Session, lead: Lead) -> dict | None:
    if not lead.property_id:
        return None
    listing = db.get(Listing, lead.property_id)
    if listing is None:
        return None
    return {"id": str(listing.id), "title": listing.title,
            "price": float(listing.price) if listing.price else None,
            "location": listing.location, "bedrooms": listing.bedrooms,
            "bathrooms": float(listing.bathrooms) if listing.bathrooms else None,
            "listing_type": listing.type}


def enqueue_lead_delivery(db: Session, lead: Lead, property_info: dict | None = None,
                          *, include_crm: bool = True, recipients: list[str] | None = None,
                          now: datetime | None = None) -> list[LeadDeliveryJob]:
    """Add jobs to the caller's NEW lead transaction; never commits or scans leads.

    Call only after db.flush() supplies the new lead's ID and before its commit.
    A duplicate lead transaction and all its jobs roll back together. Existing
    submission retries return earlier in the route and never call this function.
    """
    now = now or utcnow()
    if lead.id is None:
        raise ValueError("Flush the new lead before enqueueing delivery")
    if lead.id not in db.info.get("new_lead_delivery_ids", set()):
        raise ValueError("Delivery can only be enqueued in the original new lead transaction")
    # The transaction marker is removed after commit/rollback. Historical leads
    # cannot be replayed merely by passing their ORM objects to this helper.
    rows = []

    def add(step, provider, payload, *, depends=None, status="pending", code=None):
        row = LeadDeliveryJob(id=uuid4(), lead_id=lead.id, step=step, provider=provider,
            depends_on=depends, payload=payload, payload_sha256=payload_digest(payload),
            status=status, attempt_count=0, safe_to_retry=False, outcome_uncertain=False,
            next_attempt_at=now, created_at=now, updated_at=now, error_code=code)
        db.add(row)
        rows.append(row)

    if include_crm:
        first, last = ghl._split_name(lead.name)
        contact = {"locationId": settings.ghl_location_id, "firstName": first,
                   "lastName": last, "source": "I Love DR Realty Website"}
        if lead.email:
            contact["email"] = lead.email
        try:
            phone = normalize_phone(lead.phone)
        except ValueError:
            phone = None
        if phone:
            contact["phone"] = phone
        context = {"location_id": settings.ghl_location_id, "version": ghl.GHL_API_VERSION}
        tags = list(ghl.LEAD_TYPE_TAGS.get(lead.type, ["website-lead"]))
        note_parts = [escape(lead.message)] if lead.message else []
        if property_info:
            note_parts.extend(ghl._build_property_lines(property_info, settings.landing_url))
        label = ghl.LEAD_TYPE_LABELS.get(lead.type, lead.type.replace("_", " ").title())
        task_lines = ["New lead from I Love DR Realty Website."]
        if lead.message:
            task_lines.append(f"<br><b>Message:</b> {escape(lead.message)}")
        if property_info:
            task_lines.extend(ghl._build_property_lines(property_info, settings.landing_url))
        # One stable reference helps a human reconcile an uncertain provider POST.
        task_lines.append(f"<br>Website inquiry reference: {lead.id}")
        skip = not settings.ghl_enabled
        status, code = ("skipped", "integration_disabled") if skip else ("pending", None)
        add("crm_contact", "ghl", {**context, "body": contact}, status=status, code=code)
        add("crm_tags", "ghl", {**context, "body": {"tags": tags}}, depends="crm_contact", status=status, code=code)
        add("crm_note", "ghl", {**context, "body": {"body": "<br>".join(note_parts)}}, depends="crm_contact",
            status="skipped" if not note_parts else status, code="no_note_content" if not note_parts else code)
        add("crm_task", "ghl", {**context, "body": {
            "title": f"Follow up: {escape(lead.name)} ({label})", "body": "<br>".join(task_lines),
            "dueDate": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), "completed": False,
        }}, depends="crm_contact", status=status, code=code)
    try:
        targets = notification_recipients(db) if recipients is None else parse_notification_recipients(",".join(recipients))
        if len(targets) > 10:
            raise ValueError("Notification recipient limit exceeded")
    except Exception:
        targets = []
        add("email_configuration", "resend", {}, status="failed", code="invalid_notification_configuration")
    if not targets and not any(row.step == "email_configuration" for row in rows):
        add("email_configuration", "resend", {}, status="failed", code="missing_notification_configuration")
    if targets:
        subject, html = build_lead_notification(lead, property_info)
        # Private recipient addresses are snapshotted but never exposed in status.
        for number, target in enumerate(targets, 1):
            add(f"email_{number}", "resend", {"body": {
                "from": settings.email_from, "to": [target], "subject": subject, "html": html,
            }})
    tracking = lead.tracking or {}
    recipient = _single_recipient(lead.email)
    if (signup_enabled() and recipient and tracking.get("contact_consent") is True
            and tracking.get("internal_test") is not True
            and lead.type in {"buyer_interest", "seller_interest", "rental_interest", "property_inquiry"}):
        # The stored snapshot supplied by the caller is not permission to link a
        # private/archived listing. Check the actual public record independently.
        public_id = db.query(Listing.id).filter(Listing.id == lead.property_id, Listing.status == "active").scalar() if lead.property_id else None
        message = customer_message(lead.type, lead.name, tracking.get("locale", "en"), public_listing_id=public_id)
        add("customer_email", "resend", {"event_type": lead.type,
            "public_listing_id": str(public_id) if public_id and lead.type == "property_inquiry" else None,
            "body": {"from": settings.email_from, "to": [recipient], "subject": message.subject,
                     "html": message.html, "text": message.text}})
    return rows


def enqueue_signup_delivery(db: Session, user: User, *, locale: str = "en", now: datetime | None = None):
    """Only the original new verified-user transaction can create signup jobs.

    No commit, account scan, backfill, provider request or fake lead is made.
    Existing logins/Google linking cannot supply the new-user transaction marker.
    """
    if not signup_enabled():
        return []
    if locale not in {"en", "es"}:
        raise ValueError("Unsupported signup locale")
    if user.id is None or user.id not in db.info.get("new_signup_delivery_ids", set()):
        raise ValueError("Signup delivery requires the original new user transaction")
    if user.email_verified is not True or user.status != "active":
        raise ValueError("Signup delivery requires a new active verified account")
    now = now or utcnow()
    rows = []
    recipient = _single_recipient(user.email)

    def add(step, body, *, status="pending", code=None):
        payload = {"signup_email": recipient, "body": body}
        row = LeadDeliveryJob(id=uuid4(), user_id=user.id, lead_id=None, step=step, provider="resend",
            payload=payload, payload_sha256=payload_digest(payload), status=status, error_code=code,
            attempt_count=0, safe_to_retry=False, outcome_uncertain=False,
            next_attempt_at=now, created_at=now, updated_at=now)
        db.add(row); rows.append(row)

    try:
        targets = notification_recipients(db)
        if len(targets) > 10:
            raise ValueError("Notification recipient limit exceeded")
    except Exception:
        targets = []
        add("email_configuration", {}, status="failed", code="invalid_notification_configuration")
    if not targets and not rows:
        add("email_configuration", {}, status="failed", code="missing_notification_configuration")
    if targets:
        subject, html = build_new_user_notification(user)
        for number, target in enumerate(targets, 1):
            add(f"email_{number}", {"from": settings.email_from, "to": [target], "subject": subject, "html": html})
    if recipient:
        message = customer_message("signup", user.display_name, locale)
        add("customer_email", {"from": settings.email_from, "to": [recipient], "subject": message.subject,
                               "html": message.html, "text": message.text})
    else:
        add("customer_email_configuration", {}, status="failed", code="invalid_customer_email")
    return rows


@dataclass(frozen=True)
class Claim:
    job_id: UUID
    token: UUID
    prior_status: str
    prior_attempt_count: int
    prior_first_attempt_at: datetime | None
    prior_last_attempt_at: datetime | None
    prior_safe_to_retry: bool
    prior_error_code: str | None


class _DispatchStopped(Exception):
    """Internal control flow: this claim has not contacted a provider."""


@dataclass(frozen=True)
class Outcome:
    status: str
    code: str | None = None
    provider_id: str | None = None
    safe_to_retry: bool = False
    uncertain: bool = False
    next_attempt_at: datetime | None = None


def expire_stale_claims(db: Session, now: datetime):
    # Never reclaim processing jobs: the prior process may have sent already.
    changed = db.execute(update(LeadDeliveryJob).where(
        LeadDeliveryJob.status == "processing", LeadDeliveryJob.lease_until < now
    ).values(status="needs_review", error_code="worker_interrupted_outcome_unknown",
             safe_to_retry=False, outcome_uncertain=True, lease_token=None,
             lease_until=None, updated_at=now)
             .returning(LeadDeliveryJob.lead_id, LeadDeliveryJob.provider)).all()
    for lead_id in {row.lead_id for row in changed if row.provider == "ghl" and row.lead_id is not None}:
        _project_crm_state(db, lead_id, now)
    db.commit()
    return len(changed)


def _stopping(stop_event):
    return stop_event is not None and stop_event.is_set()


def manual_retry_prior_code(value):
    """Decode the single unconsumed Admin permit without losing its safe result.

    The marker is operational state, not proof against a privileged DB writer.
    Only the guarded Admin mutation writes it in application code. Processing
    consumes it under the same atomic claim token; an unsent claim restores it.
    """
    if not isinstance(value, str) or not value.startswith(MANUAL_RETRY_PREFIX):
        return None
    prior = value[len(MANUAL_RETRY_PREFIX):]
    return prior if SAFE_ERROR_CODE.fullmatch(prior) else None


def delivery_error_code(value):
    decoded = manual_retry_prior_code(value)
    return decoded or (value if isinstance(value, str) and SAFE_ERROR_CODE.fullmatch(value) else None)


def manual_retry_marker_sql():
    # Regex is bounded and identical to the Python decoder's accepted suffix.
    return LeadDeliveryJob.error_code.op("~")(MANUAL_RETRY_PATTERN)


def unexplained_pending_retry_exists(db: Session):
    """Bounded metadata-only activation check; never interpret legacy rows as permits."""
    return bool(db.scalar(select(exists().where(
        LeadDeliveryJob.status == "pending", LeadDeliveryJob.attempt_count > 0,
        or_(LeadDeliveryJob.safe_to_retry.is_(False),
            LeadDeliveryJob.outcome_uncertain.is_(True),
            LeadDeliveryJob.error_code.is_(None), ~manual_retry_marker_sql()),
    ))))


def claim_next_job(db: Session, now: datetime | None = None, *, stop_event=None) -> Claim | None:
    if not enabled() or _stopping(stop_event):
        return None
    now = now or utcnow()
    parent = aliased(LeadDeliveryJob)
    eligible = and_(LeadDeliveryJob.status == "pending",
                    LeadDeliveryJob.next_attempt_at <= now,
                    LeadDeliveryJob.outcome_uncertain.is_(False),
                    LeadDeliveryJob.attempt_count < MAX_ATTEMPTS,
                    or_(LeadDeliveryJob.attempt_count == 0,
                        and_(LeadDeliveryJob.safe_to_retry.is_(True), manual_retry_marker_sql())))
    same_event = or_(
        and_(LeadDeliveryJob.lead_id.isnot(None), parent.lead_id == LeadDeliveryJob.lead_id, parent.user_id.is_(None)),
        and_(LeadDeliveryJob.user_id.isnot(None), parent.user_id == LeadDeliveryJob.user_id, parent.lead_id.is_(None)))
    dependency_ready = or_(LeadDeliveryJob.depends_on.is_(None), exists(select(parent.id).where(
        same_event, parent.step == LeadDeliveryJob.depends_on,
        parent.status == "accepted")))
    previous = db.execute(select(LeadDeliveryJob.id, LeadDeliveryJob.status,
        LeadDeliveryJob.attempt_count, LeadDeliveryJob.first_attempt_at,
        LeadDeliveryJob.last_attempt_at, LeadDeliveryJob.safe_to_retry,
        LeadDeliveryJob.error_code).where(eligible, dependency_ready)
        .order_by(LeadDeliveryJob.next_attempt_at, LeadDeliveryJob.created_at, LeadDeliveryJob.id)
        .with_for_update(skip_locked=True).limit(1)).mappings().one_or_none()
    if previous is None or _stopping(stop_event):
        db.rollback()
        return None
    row_id = previous["id"]
    token = uuid4()
    changed = db.execute(update(LeadDeliveryJob).where(LeadDeliveryJob.id == row_id, eligible)
        .values(status="processing", lease_token=token, lease_until=now + timedelta(seconds=LEASE_SECONDS),
                attempt_count=LeadDeliveryJob.attempt_count + 1, last_attempt_at=now,
                first_attempt_at=case((LeadDeliveryJob.first_attempt_at.is_(None), now), else_=LeadDeliveryJob.first_attempt_at),
                safe_to_retry=False, updated_at=now))
    if changed.rowcount != 1 or _stopping(stop_event):
        db.rollback()
        return None
    db.commit()
    return Claim(row_id, token, previous["status"], previous["attempt_count"],
                 previous["first_attempt_at"], previous["last_attempt_at"],
                 previous["safe_to_retry"], previous["error_code"])


def release_unsent_claim(db: Session, claim: Claim):
    """Restore only this token's proven-unsent claim, preserving its retry budget."""
    changed = db.execute(update(LeadDeliveryJob).where(
        LeadDeliveryJob.id == claim.job_id, LeadDeliveryJob.status == "processing",
        LeadDeliveryJob.lease_token == claim.token,
    ).values(status=claim.prior_status, attempt_count=claim.prior_attempt_count,
             first_attempt_at=claim.prior_first_attempt_at, last_attempt_at=claim.prior_last_attempt_at,
             safe_to_retry=claim.prior_safe_to_retry, lease_token=None, lease_until=None, updated_at=utcnow()))
    db.commit()
    return changed.rowcount == 1


def _hold_failure(job, now, code, *, uncertain=False, retry_after=0):
    """Record an outcome; elapsed time alone never schedules another request."""
    uncertain = uncertain or bool(job.outcome_uncertain)
    if uncertain:
        return Outcome("needs_review", code, uncertain=True)
    delay = max(BACKOFF_SECONDS[max(0, min(job.attempt_count - 1, len(BACKOFF_SECONDS)-1))], retry_after)
    next_manual_at = max(_aware(job.next_attempt_at), now + timedelta(seconds=delay))
    return Outcome("failed", code, safe_to_retry=True, next_attempt_at=next_manual_at)


def _rate_delay(response):
    value = response.headers.get("Retry-After")
    # Missing, date-form, negative, oversized and malformed values are held for
    # review, rather than silently shortening an unestablished provider delay.
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,6}", value.strip()):
        return None
    seconds = int(value.strip())
    return seconds if seconds <= 86400 else None


def _provider_result(job, client: httpx.Client, now: datetime, contact_id: str | None, *, stop_event=None) -> Outcome:
    if job.outcome_uncertain:
        return Outcome("needs_review", "prior_provider_outcome_unknown", uncertain=True)
    body = job.payload.get("body")
    if not isinstance(body, dict):
        return Outcome("needs_review", "invalid_delivery_payload")
    if job.provider == "resend":
        if not settings.resend_api_key:
            return _hold_failure(job, now, "email_provider_not_configured")
        url = "https://api.resend.com/emails"
        event_key = f"website-signup/{job.user_id}" if job.user_id is not None else f"website-lead/{job.lead_id}"
        headers = {"Authorization": f"Bearer {settings.resend_api_key}", "Content-Type": "application/json",
                   "Idempotency-Key": f"{event_key}/{job.step}/v1"}
    else:
        if not settings.ghl_enabled or not settings.ghl_api_key or not settings.ghl_location_id:
            return _hold_failure(job, now, "crm_provider_not_configured")
        if job.payload.get("location_id") != settings.ghl_location_id:
            return Outcome("needs_review", "crm_destination_configuration_changed")
        path = {"crm_contact": "/contacts/", "crm_tags": "/contacts/{id}/tags",
                "crm_note": "/contacts/{id}/notes", "crm_task": "/contacts/{id}/tasks"}.get(job.step)
        if not path:
            return Outcome("needs_review", "invalid_delivery_step")
        if job.step != "crm_contact" and not ghl._contact_id(contact_id):
            return Outcome("needs_review", "crm_contact_not_confirmed")
        url = ghl.GHL_API_BASE + path.replace("{id}", contact_id or "")
        headers = {"Authorization": f"Bearer {settings.ghl_api_key}",
                   "Version": job.payload.get("version", ghl.GHL_API_VERSION), "Content-Type": "application/json"}
    if _stopping(stop_event):
        raise _DispatchStopped()
    try:
        response = client.post(url, headers=headers, json=body, timeout=PROVIDER_TIMEOUT)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        return _hold_failure(job, now, f"{job.provider}_connection_not_established")
    except httpx.RequestError:
        return _hold_failure(job, now, f"{job.provider}_outcome_unknown", uncertain=True)
    except Exception:
        return Outcome("needs_review", "provider_call_outcome_unknown", uncertain=True)

    data = None
    try:
        data = response.json()
    except (ValueError, TypeError):
        pass
    if job.step == "crm_contact" and response.status_code == 400 and isinstance(data, dict):
        meta = data.get("meta")
        found = ghl._contact_id(meta.get("contactId") if isinstance(meta, dict) else None)
        if found:
            return Outcome("accepted", provider_id=found)
    if response.is_success:
        if job.step == "crm_contact":
            contact = data.get("contact") if isinstance(data, dict) else None
            found = ghl._contact_id(contact.get("id") if isinstance(contact, dict) else None)
            return Outcome("accepted", provider_id=found) if found else Outcome("needs_review", "crm_contact_invalid_success_response", uncertain=True)
        if job.provider == "resend":
            found = data.get("id") if isinstance(data, dict) else None
            if not isinstance(found, str) or not PROVIDER_ID.fullmatch(found):
                return Outcome("needs_review", "email_acceptance_unknown", uncertain=True)
            return Outcome("accepted", provider_id=found)
        # A successful provider response acknowledges the write, not email inbox delivery.
        found = data.get("id") if isinstance(data, dict) else None
        return Outcome("accepted", provider_id=found if isinstance(found, str) and PROVIDER_ID.fullmatch(found) else None)
    code = f"{job.provider}_http_{response.status_code}"
    if response.status_code == 429:
        delay = _rate_delay(response)
        if delay is None:
            return Outcome("needs_review", f"{job.provider}_retry_after_unverified")
        return _hold_failure(job, now, code, retry_after=delay)
    if job.provider == "resend" and response.status_code == 409:
        name = data.get("name") if isinstance(data, dict) else None
        if name == "concurrent_idempotent_requests":
            return _hold_failure(job, now, "email_idempotent_request_in_progress", uncertain=True)
        return Outcome("needs_review", "email_idempotency_conflict", uncertain=True)
    if response.status_code in SAFE_REJECTIONS:
        return _hold_failure(job, now, code)
    if job.provider == "resend" and response.status_code >= 500:
        return _hold_failure(job, now, code, uncertain=True)
    # A server failure/timeout or unclassified response may follow a committed POST.
    return Outcome("needs_review", code, uncertain=True)


def _project_crm_state(db: Session, lead_id, now):
    # Serialize the compatibility projection when several independent CRM steps
    # finish together, so the last writer sees the earlier committed outcomes.
    lead = db.query(Lead).filter(Lead.id == lead_id).with_for_update().first()
    if lead is None:
        return
    rows = db.query(LeadDeliveryJob).filter(LeadDeliveryJob.lead_id == lead_id,
                                         LeadDeliveryJob.provider == "ghl").all()
    if not rows:
        return
    contact = next((row for row in rows if row.step == "crm_contact"), None)
    if contact and contact.status == "accepted" and ghl._contact_id(contact.provider_id):
        lead.ghl_contact_id = contact.provider_id
    failures = [f"{row.step}:{delivery_error_code(row.error_code)}" for row in rows if row.status in ("failed", "needs_review", "retry_wait") and row.error_code]
    complete = contact and contact.status == "accepted" and all(row.status in ("accepted", "skipped") for row in rows)
    lead.ghl_synced_at = now if complete else None
    lead.ghl_sync_error = "; ".join(failures)[:2000] or None


def finish_claim(db: Session, claim: Claim, outcome: Outcome, now: datetime):
    values = dict(status=outcome.status, error_code=outcome.code, safe_to_retry=outcome.safe_to_retry,
                  outcome_uncertain=outcome.uncertain, lease_token=None, lease_until=None, updated_at=now)
    if outcome.next_attempt_at:
        values["next_attempt_at"] = outcome.next_attempt_at
    if outcome.status == "accepted":
        values.update(accepted_at=now, provider_id=outcome.provider_id)
    changed = db.execute(update(LeadDeliveryJob).where(LeadDeliveryJob.id == claim.job_id,
        LeadDeliveryJob.status == "processing", LeadDeliveryJob.lease_token == claim.token).values(**values))
    if changed.rowcount == 1:
        lead_id, provider = db.execute(select(LeadDeliveryJob.lead_id, LeadDeliveryJob.provider)
            .where(LeadDeliveryJob.id == claim.job_id)).one()
        if provider == "ghl" and lead_id is not None:
            _project_crm_state(db, lead_id, now)
    db.commit()
    return changed.rowcount == 1


def _email_destination_error(db, job):
    recipients = (job.payload.get("body") or {}).get("to")
    if not isinstance(recipients, list) or len(recipients) != 1 or not isinstance(recipients[0], str):
        return "email_destination_configuration_changed"
    target = recipients[0]
    if job.user_id is not None:
        if not signup_enabled():
            return "customer_messages_disabled"
        user = db.get(User, job.user_id)
        if (user is None or user.status != "active" or user.email_verified is not True or _single_recipient(user.email) is None
                or _single_recipient(user.email) != job.payload.get("signup_email")):
            return "signup_destination_changed"
        if job.step == "customer_email":
            return None if target == _single_recipient(user.email) else "signup_destination_changed"
    elif job.step == "customer_email":
        if not signup_enabled():
            return "customer_messages_disabled"
        lead = db.get(Lead, job.lead_id)
        tracking = (lead.tracking or {}) if lead else {}
        if (lead is None or target != _single_recipient(lead.email)
                or tracking.get("contact_consent") is not True or tracking.get("internal_test") is True
                or lead.type != job.payload.get("event_type")
                or lead.type not in {"buyer_interest", "seller_interest", "rental_interest", "property_inquiry"}):
            return "customer_destination_changed"
        listing_id = job.payload.get("public_listing_id")
        if listing_id and db.query(Listing.id).filter(Listing.id == listing_id, Listing.status == "active").first() is None:
            return "customer_listing_context_changed"
        return None
    try:
        current = {address.casefold() for address in notification_recipients(db)}
        return None if target.casefold() in current else "email_destination_configuration_changed"
    except Exception:
        return "email_destination_configuration_changed"


def process_claimed_job(db: Session, claim: Claim, *, client: httpx.Client | None = None,
                        now: datetime | None = None, stop_event=None) -> bool:
    if not enabled() or _stopping(stop_event):
        release_unsent_claim(db, claim)
        return False
    now = now or utcnow()
    db.expire_all()
    job = db.get(LeadDeliveryJob, claim.job_id)
    if job is None or job.status != "processing" or job.lease_token != claim.token:
        return False
    if _aware(job.lease_until) <= now:
        return finish_claim(db, claim, Outcome("needs_review", "worker_lease_expired", uncertain=True), now)
    if job.outcome_uncertain:
        return finish_claim(db, claim, Outcome("needs_review", "prior_provider_outcome_unknown", uncertain=True), now)
    first_dispatch = (claim.prior_attempt_count == 0 and job.attempt_count == 1
                      and job.safe_to_retry is False)
    manual_dispatch = (claim.prior_attempt_count > 0 and claim.prior_safe_to_retry
                       and manual_retry_prior_code(claim.prior_error_code) is not None
                       and job.error_code == claim.prior_error_code
                       and job.safe_to_retry is False
                       and job.attempt_count == claim.prior_attempt_count + 1)
    if claim.prior_status != "pending" or not (first_dispatch or manual_dispatch):
        return finish_claim(db, claim, Outcome("needs_review", "manual_retry_authorization_missing"), now)
    if payload_digest(job.payload) != job.payload_sha256:
        return finish_claim(db, claim, Outcome("needs_review", "delivery_payload_changed"), now)
    if (job.lead_id is None) == (job.user_id is None) or (job.user_id is not None and job.provider != "resend"):
        return finish_claim(db, claim, Outcome("needs_review", "invalid_delivery_event"), now)
    if job.provider == "resend":
        error = _email_destination_error(db, job)
        if error:
            return finish_claim(db, claim, Outcome("needs_review", error), now)
    contact_id = None
    if job.depends_on:
        parent = db.query(LeadDeliveryJob).filter(LeadDeliveryJob.lead_id == job.lead_id,
            LeadDeliveryJob.user_id == job.user_id, LeadDeliveryJob.step == job.depends_on,
            LeadDeliveryJob.status == "accepted").first()
        contact_id = parent.provider_id if parent else None
    try:
        if client is None:
            with httpx.Client(timeout=PROVIDER_TIMEOUT, follow_redirects=False) as managed:
                outcome = _provider_result(job, managed, now, contact_id, stop_event=stop_event)
        else:
            outcome = _provider_result(job, client, now, contact_id, stop_event=stop_event)
    except _DispatchStopped:
        release_unsent_claim(db, claim)
        return False
    return finish_claim(db, claim, outcome, now)


def _heartbeat(db: Session, now, *, completed=False, count=0, error=None):
    values = {"last_poll_at": now, "last_batch_count": count, "error_code": error}
    if completed:
        values["last_completed_at"] = now
    statement = insert(LeadDeliveryWorkerState).values(id=1, **values)
    db.execute(statement.on_conflict_do_update(index_elements=[LeadDeliveryWorkerState.id], set_=values))
    db.commit()


def run_batch(session_factory, *, max_jobs=MAX_BATCH, client: httpx.Client | None = None, stop_event=None):
    """Poll at most ten due NEW-event jobs. Paused means zero DB/provider work."""
    if not enabled():
        return {"enabled": False, "processed": 0}
    if _stopping(stop_event):
        return {"enabled": True, "processed": 0, "stopped": True}
    max_jobs = max(0, min(MAX_BATCH, int(max_jobs)))
    processed = 0
    try:
        with session_factory() as db:
            if unexplained_pending_retry_exists(db):
                _heartbeat(db, utcnow(), completed=True, error="pending_retry_authorization_unknown")
                return {"enabled": True, "processed": 0, "error_code": "pending_retry_authorization_unknown"}
            _heartbeat(db, utcnow())
            expire_stale_claims(db, utcnow())
        for _ in range(max_jobs):
            if _stopping(stop_event):
                break
            claim = None
            with session_factory() as db:
                claim = claim_next_job(db, stop_event=stop_event)
                if claim is None:
                    break
                try:
                    completed = process_claimed_job(db, claim, client=client, stop_event=stop_event)
                except Exception:
                    db.rollback()
                    # A crash after a provider call must not make the job eligible again.
                    completed = finish_claim(db, claim, Outcome("needs_review", "worker_step_outcome_unknown", uncertain=True), utcnow())
                processed += int(completed)
        with session_factory() as db:
            _heartbeat(db, utcnow(), completed=True, count=processed)
        return {"enabled": True, "processed": processed}
    except Exception:
        # No exception repr/SQL parameters: those may contain private payloads.
        logger.error("Lead delivery worker failed; inspect private status records")
        try:
            with session_factory() as db:
                _heartbeat(db, utcnow(), completed=True, count=processed, error="worker_database_error")
        except Exception:
            pass
        return {"enabled": True, "processed": processed, "error_code": "worker_database_error"}


async def worker_loop(session_factory):
    """Cancel cooperatively and drain the one active batch before lifespan exits."""
    stop_event = threading.Event()
    active_batch = None
    try:
        while True:
            await asyncio.sleep(POLL_SECONDS)
            if enabled():
                active_batch = asyncio.create_task(asyncio.to_thread(run_batch, session_factory, stop_event=stop_event))
                # Cancelling this coroutine must not cancel the task that tracks
                # the thread. The thread cooperates through stop_event instead.
                await asyncio.shield(active_batch)
                active_batch = None
    finally:
        stop_event.set()
        if active_batch is not None:
            # Complete an in-flight bounded provider request and its durable
            # outcome, then return. Repeated cancellation cannot abandon it.
            while not active_batch.done():
                try:
                    await asyncio.shield(active_batch)
                except asyncio.CancelledError:
                    continue
            try:
                active_batch.result()
            except Exception:
                logger.error("Lead delivery batch stopped with an internal failure")
