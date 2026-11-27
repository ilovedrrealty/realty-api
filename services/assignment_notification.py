"""Queued future-assignment notices; no historical scans or automatic resends."""
import asyncio
import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException
from pydantic import EmailStr, TypeAdapter
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from config import settings
from models.assignment_notification import AssignmentEmailJob as Job, AssignmentEvent as Event, NOTICE_KINDS
from models.lead import Lead
from models.listing import Listing
from models.user import User
from utils.assignment_messages import (
    build_realtor_assigned_owner_notification, build_realtor_lead_assigned_notification,
    build_upgrade_approved_notification,
)

logger = logging.getLogger(__name__)
POLL_SECONDS, MAX_BATCH, LEASE_SECONDS = 15, 10, 120
PROVIDER_TIMEOUT = httpx.Timeout(connect=3, read=10, write=5, pool=1)
ABSOLUTE_PROVIDER_SECONDS = 15
MAX_RESPONSE_BYTES = 16 * 1024
PROVIDER_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")
EMAIL = TypeAdapter(EmailStr)


def utcnow():
    return datetime.now(timezone.utc)


def enabled():
    return bool(getattr(settings, "lead_delivery_enabled", False))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identifier(value, label="assignment"):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, "Invalid " + label + " identifier")


def prepare_request(db, *, actor_id, source_kind, source_id, agent_id, request_id=None):
    """Serialize identical commands before touching source state; never commit."""
    _limits(db)
    request_id = uuid4() if request_id is None else identifier(request_id, "request")
    source_id = identifier(source_id)
    agent_id = None if agent_id is None else identifier(agent_id, "agent")
    body_hash = digest({"source_kind": source_kind, "source_id": str(source_id),
                        "agent_id": str(agent_id) if agent_id else None})
    lock = int.from_bytes(hashlib.sha256((str(actor_id) + "/" + str(request_id)).encode()).digest()[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
    prior = db.query(Event).filter(Event.actor_id == actor_id, Event.request_id == request_id).first()
    if prior is not None and prior.request_sha256 != body_hash:
        raise HTTPException(409, "Assignment request was already used with different details")
    return prior, request_id, body_hash, source_id, agent_id


def lock_users(db, ids, *, read=False):
    """Stable UUID order; NO KEY UPDATE allows existing FK KEY SHARE checks."""
    ids = sorted({value for value in ids if value is not None}, key=str)
    if not ids:
        return {}
    query = db.query(User).filter(User.id.in_(ids)).order_by(User.id).populate_existing()
    query = query.with_for_update(read=True) if read else query.with_for_update(key_share=True)
    return {row.id: row for row in query.all()}


def eligible(user):
    return user is not None and user.role in ("admin", "realtor") and user.status == "active" and user.email_verified is True


def _email(value):
    try:
        return str(EMAIL.validate_python(value))
    except Exception:
        return None


def _notice(db, event, kind, recipient, render):
    """Optional preparation failure is a durable failed0 outcome, not a lost assignment."""
    now, payload, status, code = event.created_at, {}, "pending", None
    try:
        address = _email(recipient.email)
        if address is None:
            raise ValueError()
        subject, html = render()
        if (not isinstance(subject, str) or not 0 < len(subject) <= 500
                or not isinstance(html, str) or not 0 < len(html.encode()) <= 256 * 1024
                or not isinstance(settings.email_from, str) or not settings.email_from.strip()):
            raise ValueError()
        payload = {"recipient_email": address, "body": {
            "from": settings.email_from, "to": [address], "subject": subject, "html": html}}
    except Exception:
        status, code = "failed", "message_preparation_failed"
    row = Job(id=uuid4(), event_id=event.id, kind=kind, recipient_user_id=recipient.id,
              payload=payload, payload_sha256=digest(payload), status=status,
              attempt_count=0, outcome_uncertain=False, created_at=now, updated_at=now,
              error_code=code)
    db.add(row)
    return row


def record_event(db, *, actor_id, request_id, request_sha256, source_kind, source_id,
                 previous_agent_id, agent, lead=None, owner_upgraded=None):
    """Add event/notices to the caller's assignment transaction; never send/commit."""
    now = utcnow()
    scope = Event.lead_id == source_id if source_kind == "lead" else Event.user_id == source_id
    previous_cycle = db.scalar(select(func.max(Event.cycle)).where(scope)) or 0
    agent_id = agent.id if agent else None
    # The caller holds the source row. A->B->A creates a new cycle, whereas a
    # fresh explicit A->A action stays in A's current cycle and keeps its notice.
    cycle = 1 if previous_cycle == 0 else previous_cycle + int(previous_agent_id != agent_id)
    event = Event(id=uuid4(), source_kind=source_kind,
        lead_id=source_id if source_kind == "lead" else None,
        user_id=source_id if source_kind == "account" else None,
        actor_id=actor_id, request_id=request_id, request_sha256=request_sha256,
        previous_agent_id=previous_agent_id, agent_id=agent_id, cycle=cycle,
        owner_upgraded_id=owner_upgraded.id if owner_upgraded else None,
        action="assigned" if agent else "cleared", created_at=now)
    db.add(event)
    db.flush()
    if source_kind == "account":
        return event
    # No optional listing/render failure may prevent saving a valid assignment.
    def lead_message():
        info = None
        if lead.property_id:
            listing = db.get(Listing, lead.property_id)
            if listing is not None:
                info = {"id": str(listing.id), "title": listing.title}
        return build_realtor_lead_assigned_notification(agent.display_name or agent.email, lead, info,
                                                       getattr(agent, "calendly_url", None))
    _notice(db, event, "lead_assigned_agent", agent, lead_message)
    if owner_upgraded is not None:
        _notice(db, event, "owner_upgrade_customer", owner_upgraded,
                lambda: build_upgrade_approved_notification(owner_upgraded.display_name or owner_upgraded.email, "owner"))
        _notice(db, event, "owner_assigned_agent", agent,
                lambda: build_realtor_assigned_owner_notification(agent.display_name or agent.email,
                    owner_upgraded.display_name or owner_upgraded.email, owner_upgraded.email))
    return event


@dataclass(frozen=True)
class Claim:
    id: UUID
    token: UUID


@dataclass(frozen=True)
class Outcome:
    status: str
    code: str | None = None
    provider_id: str | None = None
    uncertain: bool = False


def _stopping(stop):
    return stop is not None and stop.is_set()


def _limits(db):
    # The inherited engine bounds connect/pool acquisition. These transaction
    # limits also bound SELECT/advisory/row-lock waits, including worker guards.
    db.execute(text("SET LOCAL statement_timeout='5s'"))
    db.execute(text("SET LOCAL lock_timeout='3s'"))
    db.execute(text("SET LOCAL idle_in_transaction_session_timeout='30s'"))


def expire_claims(db, now):
    _limits(db)
    ids = list(db.scalars(select(Job.id).where(Job.status == "processing", Job.lease_until < now)
        .order_by(Job.lease_until, Job.id).with_for_update(skip_locked=True).limit(MAX_BATCH)))
    changed = db.execute(update(Job).where(Job.id.in_(ids), Job.status == "processing").values(
        status="needs_review", outcome_uncertain=True, error_code="interrupted_outcome_unknown",
        lease_token=None, lease_until=None, updated_at=now)).rowcount
    db.commit()
    return changed


def claim_next(db, *, now=None, stop_event=None):
    if not enabled() or _stopping(stop_event):
        return None
    now = now or utcnow()
    _limits(db)
    row = db.execute(select(Job.id).where(Job.status == "pending", Job.attempt_count == 0)
        .order_by(Job.created_at, Job.id).with_for_update(skip_locked=True).limit(1)).scalar_one_or_none()
    if row is None or _stopping(stop_event):
        db.rollback()
        return None
    token = uuid4()
    count = db.execute(update(Job).where(Job.id == row, Job.status == "pending", Job.attempt_count == 0)
        .values(status="processing", attempt_count=1, attempted_at=now,
                lease_token=token, lease_until=now + timedelta(seconds=LEASE_SECONDS), updated_at=now)).rowcount
    if count != 1 or _stopping(stop_event):
        db.rollback()
        return None
    db.commit()
    return Claim(row, token)


def finish(db, claim, outcome, now=None):
    now = now or utcnow()
    _limits(db)
    count = db.execute(update(Job).where(Job.id == claim.id, Job.status == "processing",
        Job.lease_token == claim.token).values(status=outcome.status,
        accepted_at=now if outcome.status == "accepted" else None,
        provider_id=outcome.provider_id if outcome.status == "accepted" else None,
        outcome_uncertain=outcome.uncertain, error_code=outcome.code,
        lease_token=None, lease_until=None, updated_at=now)).rowcount
    db.commit()
    return count == 1


def release_unsent(db, claim):
    """Called only on a proven no-provider path before the one POST begins."""
    _limits(db)
    count = db.execute(update(Job).where(Job.id == claim.id, Job.status == "processing",
        Job.lease_token == claim.token).values(status="pending", attempt_count=0,
        attempted_at=None, lease_token=None, lease_until=None, updated_at=utcnow())).rowcount
    db.commit()
    return count == 1


def _destination(db, event, job, payload):
    """Source lock first, then ordered user locks, held through bounded handoff."""
    if job.kind == "owner_upgrade_customer":
        # This confirms the committed promotion, not the agent assignment.
        # An agent change/suspension must not drop a still-valid owner message.
        recipient = lock_users(db, (event.owner_upgraded_id,), read=True).get(event.owner_upgraded_id)
        if recipient is None or recipient.id != job.recipient_user_id or recipient.role != "owner":
            return "upgrade_destination_changed"
        address = _email(recipient.email)
        body = payload.get("body")
        if (address is None or address != payload.get("recipient_email") or not isinstance(body, dict)
                or body.get("to") != [address] or body.get("from") != settings.email_from):
            return "email_destination_changed"
        return None
    lead = db.query(Lead).filter(Lead.id == event.lead_id).populate_existing().with_for_update().first()
    if lead is None or lead.assigned_realtor_id != event.agent_id:
        return "assignment_superseded"
    latest_cycle = db.scalar(select(func.max(Event.cycle)).where(Event.lead_id == event.lead_id))
    if latest_cycle != event.cycle:
        return "assignment_cycle_superseded"
    users = lock_users(db, (event.agent_id, event.owner_upgraded_id), read=True)
    agent = users.get(event.agent_id)
    if not eligible(agent):
        return "agent_no_longer_eligible"
    recipient = agent
    if recipient.id != job.recipient_user_id:
        return "agent_destination_changed"
    if job.kind == "owner_assigned_agent" and event.owner_upgraded_id is None:
        return "invalid_notice_context"
    address = _email(recipient.email)
    body = payload.get("body")
    if (address is None or address != payload.get("recipient_email") or not isinstance(body, dict)
            or body.get("to") != [address] or body.get("from") != settings.email_from):
        return "email_destination_changed"
    return None


class _UnconfirmedResponse(Exception):
    pass


def provider_result(transport, event, job):
    if not settings.resend_api_key:
        return Outcome("failed", "email_provider_not_configured")
    async def exchange():
        # This is an absolute deadline, not an inactivity/read timeout. The
        # default async transport cooperates with cancellation and closes I/O.
        async with asyncio.timeout(ABSOLUTE_PROVIDER_SECONDS):
            async with httpx.AsyncClient(transport=transport, timeout=PROVIDER_TIMEOUT,
                                        follow_redirects=False, trust_env=False) as client:
                async with client.stream("POST", "https://api.resend.com/emails",
                    headers={"Authorization": "Bearer " + settings.resend_api_key,
                        "Content-Type": "application/json", "Accept-Encoding": "identity",
                        "Idempotency-Key": f"assignment-event/{event.id}/{job.kind}/v1"},
                    json=job.payload["body"]) as response:
                    # Reject compression before a decoder can expand it. Even a
                    # nonconforming provider cannot create an unbounded body.
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise _UnconfirmedResponse()
                    raw = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=4096):
                        if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise _UnconfirmedResponse()
                        raw.extend(chunk)
                    try:
                        data = json.loads(raw)
                    except (ValueError, TypeError, RecursionError):
                        data = None
                    return response.status_code, data
    try:
        status, data = asyncio.run(exchange())
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        return Outcome("failed", "connection_not_established")
    except (TimeoutError, _UnconfirmedResponse):
        return Outcome("needs_review", "response_unconfirmed", uncertain=True)
    except Exception:
        return Outcome("needs_review", "provider_outcome_unknown", uncertain=True)
    if 200 <= status < 300:
        value = data.get("id") if isinstance(data, dict) else None
        if isinstance(value, str) and PROVIDER_ID.fullmatch(value):
            return Outcome("accepted", provider_id=value)
        return Outcome("needs_review", "acceptance_unconfirmed", uncertain=True)
    if status in (400, 401, 403, 404, 422, 429):
        return Outcome("failed", "provider_rejected")
    return Outcome("needs_review", "provider_outcome_unknown", uncertain=True)


def process_claim(db, claim, *, transport=None, stop_event=None, now=None):
    now = now or utcnow()
    _limits(db)
    job = db.query(Job).filter(Job.id == claim.id).populate_existing().with_for_update().first()
    if job is None or job.status != "processing" or job.lease_token != claim.token:
        db.rollback()
        return False
    if _stopping(stop_event) or not enabled():
        return release_unsent(db, claim)
    lease = job.lease_until
    if lease.tzinfo is None:
        lease = lease.replace(tzinfo=timezone.utc)
    if lease <= now:
        return finish(db, claim, Outcome("needs_review", "lease_expired", uncertain=True), now)
    event = db.get(Event, job.event_id)
    if event is None or event.source_kind != "lead" or job.kind not in NOTICE_KINDS:
        return finish(db, claim, Outcome("skipped", "invalid_notice_context"), now)
    if digest(job.payload) != job.payload_sha256:
        return finish(db, claim, Outcome("failed", "payload_changed"), now)
    error = _destination(db, event, job, job.payload)
    if error is not None:
        return finish(db, claim, Outcome("skipped", error), now)
    if _stopping(stop_event) or not enabled():
        return release_unsent(db, claim)
    # Locks on lead/current users remain held until finish commits. Assignment
    # mutation uses the same source-first order, so it cannot race the handoff.
    outcome = provider_result(transport, event, job)
    return finish(db, claim, outcome)


def run_batch(session_factory, *, max_jobs=MAX_BATCH, transport=None, stop_event=None):
    if not enabled():
        return {"enabled": False, "processed": 0}
    if _stopping(stop_event):
        return {"enabled": True, "processed": 0, "stopped": True}
    processed = 0
    try:
        with session_factory() as db:
            expire_claims(db, utcnow())
        for _ in range(max(0, min(MAX_BATCH, int(max_jobs)))):
            if _stopping(stop_event):
                break
            with session_factory() as db:
                claim = claim_next(db, stop_event=stop_event)
                if claim is None:
                    break
                try:
                    processed += int(process_claim(db, claim, transport=transport, stop_event=stop_event))
                except Exception:
                    db.rollback()
                    processed += int(finish(db, claim, Outcome("needs_review", "worker_outcome_unknown", uncertain=True)))
        return {"enabled": True, "processed": processed}
    except Exception:
        logger.error("Assignment notice worker failed; inspect private metadata")
        return {"enabled": True, "processed": processed, "error_code": "worker_database_error"}


async def worker_loop(session_factory):
    """Cooperative bounded thread; never run DB/provider I/O on the event loop."""
    stop = threading.Event()
    active = None
    try:
        while True:
            await asyncio.sleep(POLL_SECONDS)
            if enabled():
                active = asyncio.create_task(asyncio.to_thread(run_batch, session_factory, stop_event=stop))
                await asyncio.shield(active)
                active = None
    finally:
        stop.set()
        if active is not None:
            while not active.done():
                try:
                    await asyncio.shield(active)
                except asyncio.CancelledError:
                    continue
            try:
                active.result()
            except Exception:
                logger.error("Assignment notice batch stopped with an internal failure")


def history(db, *, source_kind, source_id, limit, offset):
    _limits(db)
    target = Lead if source_kind == "lead" else User
    if db.scalar(select(target.id).where(target.id == source_id)) is None:
        raise HTTPException(404, "Assignment target not found")
    scope = Event.lead_id == source_id if source_kind == "lead" else Event.user_id == source_id
    total = db.scalar(select(func.count()).select_from(Event).where(scope))
    events = db.query(Event.id, Event.action, Event.created_at).filter(scope).order_by(
        Event.created_at.desc(), Event.id.desc()).offset(offset).limit(limit).all()
    ids = [row.id for row in events]
    notices = db.query(Job.event_id, Job.kind, Job.status, Job.attempt_count, Job.created_at,
        Job.attempted_at, Job.accepted_at, Job.outcome_uncertain).filter(Job.event_id.in_(ids)).all() if ids else []
    if source_kind == "account" and notices:
        raise ValueError("Invalid account assignment history")
    grouped = {value: [] for value in ids}
    for row in notices:
        grouped[row.event_id].append({"kind": row.kind, "status": row.status, "attempts": row.attempt_count,
            "created_at": row.created_at, "attempted_at": row.attempted_at,
            "accepted_at": row.accepted_at, "outcome_uncertain": row.outcome_uncertain})
    return {"total": total, "limit": limit, "offset": offset, "tracked": total > 0,
            "items": [{"id": row.id, "action": row.action, "created_at": row.created_at,
                       "notices": sorted(grouped[row.id], key=lambda item: NOTICE_KINDS.index(item["kind"]))}
                      for row in events]}
