"""Admin-only operational metadata; immutable private payloads are never exposed."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, load_only

from database import get_db
from models.lead import Lead
from models.lead_delivery import DELIVERY_STATUSES, LeadDeliveryJob
from config import settings
from services.lead_delivery import (MANUAL_RETRY_PREFIX, MAX_ATTEMPTS, SAFE_ERROR_CODE,
                                   delivery_error_code, enabled)
from utils.permission import require_admin
from utils.request_security import require_json_mutation

class PrivateDeliveryRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request: Request):
            try:
                require_json_mutation(request)
                response = await handler(request)
            except RequestValidationError:
                # Validation errors must not echo an untrusted path/query value.
                response = JSONResponse({"detail": "Invalid delivery request"}, status_code=422)
            except StarletteHTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            except Exception:
                # Neither database statement parameters nor provider/customer
                # payloads belong in an operational response or exception log.
                response = JSONResponse({"detail": "Delivery status is temporarily unavailable"}, status_code=503)
            _private(response)
            response.headers["Vary"] = "Cookie, Authorization"
            return response

        return private_handler


router = APIRouter(tags=["lead delivery"], route_class=PrivateDeliveryRoute)


class RetryDeliveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _metadata_query(db, *, include_signup=False):
    # Status views do not even fetch immutable customer-containing payloads.
    query = db.query(LeadDeliveryJob).options(load_only(
        LeadDeliveryJob.id, LeadDeliveryJob.lead_id, LeadDeliveryJob.user_id, LeadDeliveryJob.step,
        LeadDeliveryJob.provider, LeadDeliveryJob.status, LeadDeliveryJob.attempt_count,
        LeadDeliveryJob.safe_to_retry, LeadDeliveryJob.outcome_uncertain,
        LeadDeliveryJob.created_at, LeadDeliveryJob.first_attempt_at,
        LeadDeliveryJob.last_attempt_at, LeadDeliveryJob.next_attempt_at,
        LeadDeliveryJob.accepted_at, LeadDeliveryJob.error_code, LeadDeliveryJob.depends_on,
    ))
    return query if include_signup else query.filter(LeadDeliveryJob.lead_id.isnot(None))


def _private(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _can_retry(row, now):
    due = row.next_attempt_at
    if due is not None and due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return (row.status in ("failed", "retry_wait") and row.safe_to_retry
            and not row.outcome_uncertain and 0 < row.attempt_count < MAX_ATTEMPTS
            and due is not None and due <= now
            and isinstance(row.error_code, str) and SAFE_ERROR_CODE.fullmatch(row.error_code) is not None)


def _status(row, parents, now):
    parent_status = parents.get((row.lead_id, row.depends_on)) if row.depends_on else None
    blocked = row.depends_on if row.status in ("pending", "retry_wait") and row.depends_on and parent_status != "accepted" else None
    return {"id": str(row.id), "lead_id": str(row.lead_id), "step": row.step,
            "provider": row.provider, "status": row.status, "attempts": row.attempt_count,
            "max_attempts": MAX_ATTEMPTS, "created_at": row.created_at,
            "first_attempt_at": row.first_attempt_at, "last_attempt_at": row.last_attempt_at,
            "next_attempt_at": row.next_attempt_at, "accepted_at": row.accepted_at,
            "error_code": delivery_error_code(row.error_code), "blocked_by": blocked,
            "outcome_uncertain": row.outcome_uncertain, "can_retry": _can_retry(row, now)}


@router.get("/admin/leads/delivery-summary")
def delivery_summary(response: Response, user=Depends(require_admin), db: Session = Depends(get_db)):
    _private(response)
    # One statement gives all counters the same MVCC snapshot while workers and
    # new intakes are changing statuses. No customer/payload columns are read.
    snapshot = db.execute(text("""WITH counts AS (
        SELECT status, count(*) AS n FROM lead_delivery_jobs WHERE lead_id IS NOT NULL GROUP BY status
    ), steps AS (
        SELECT step, status, count(*) AS n FROM lead_delivery_jobs WHERE lead_id IS NOT NULL GROUP BY step, status
    ) SELECT clock_timestamp() AS generated_at,
        (SELECT coalesce(jsonb_object_agg(status,n),'{}'::jsonb) FROM counts) AS counts,
        (SELECT count(DISTINCT lead_id) FROM lead_delivery_jobs WHERE lead_id IS NOT NULL) AS tracked,
        (SELECT coalesce(jsonb_agg(jsonb_build_object('step',step,'status',status,'count',n) ORDER BY step,status),'[]'::jsonb) FROM steps) AS by_step,
        (SELECT to_jsonb(w)-'id' FROM lead_delivery_worker_state w WHERE id=1) AS worker
    """)).mappings().one()
    counts = {status: 0 for status in DELIVERY_STATUSES}
    counts.update(snapshot["counts"])
    return {"generated_at": snapshot["generated_at"], "enabled": enabled(), "total_jobs": sum(counts.values()),
            "tracked_leads": snapshot["tracked"], "status_counts": counts, "by_step": snapshot["by_step"],
            "worker": snapshot["worker"] or {"last_poll_at": None, "last_completed_at": None, "last_batch_count": 0, "error_code": None},
            "note": "Accepted means acknowledged by the provider, not confirmed recipient inbox delivery. Historical leads are not enqueued."}


@router.get("/admin/leads/{lead_id}/delivery")
def lead_delivery_detail(lead_id: UUID, response: Response, user=Depends(require_admin), db: Session = Depends(get_db)):
    _private(response)
    if db.scalar(select(Lead.id).where(Lead.id == lead_id)) is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    rows = _metadata_query(db).filter(LeadDeliveryJob.lead_id == lead_id).order_by(LeadDeliveryJob.created_at, LeadDeliveryJob.step).all()
    parents = {(row.lead_id, row.step): row.status for row in rows}
    now = datetime.now(timezone.utc)
    return {"lead_id": str(lead_id), "tracked": bool(rows), "enabled": enabled(),
            "jobs": [_status(row, parents, now) for row in rows],
            "note": None if rows else "No delivery record was created for this historical or legacy-delivered lead. It will not be replayed."}


@router.get("/admin/lead-deliveries")
def delivery_queue(response: Response, status: str | None = None, lead_id: UUID | None = None,
                   limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10000),
                   user=Depends(require_admin), db: Session = Depends(get_db)):
    _private(response)
    if status is not None and status not in DELIVERY_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid delivery status")
    query = _metadata_query(db)
    if status:
        query = query.filter(LeadDeliveryJob.status == status)
    if lead_id is not None:
        query = query.filter(LeadDeliveryJob.lead_id == lead_id)
    total = query.count()
    rows = query.order_by(LeadDeliveryJob.created_at.desc(), LeadDeliveryJob.id).offset(offset).limit(limit).all()
    parent_ids = {row.lead_id for row in rows if row.depends_on}
    parents = {(lead_id, step): state for lead_id, step, state in
        db.query(LeadDeliveryJob.lead_id, LeadDeliveryJob.step, LeadDeliveryJob.status)
          .filter(LeadDeliveryJob.lead_id.in_(parent_ids), LeadDeliveryJob.step == "crm_contact").all()} if parent_ids else {}
    return {"total": total, "limit": limit, "offset": offset,
            "items": [_status(row, parents, datetime.now(timezone.utc)) for row in rows]}


@router.post("/admin/lead-deliveries/{job_id}/retry")
def retry_delivery(job_id: UUID, request: RetryDeliveryRequest, response: Response,
                   user=Depends(require_admin), db: Session = Depends(get_db)):
    """Authorize one due, known-safe attempt; this HTTP request never sends."""
    _private(response)
    return _authorize_retry(db, job_id, lead_only=True)


def _authorize_retry(db, job_id, *, lead_only, email_only=False):
    scope = [LeadDeliveryJob.id == job_id]
    if lead_only:
        scope.append(LeadDeliveryJob.lead_id.isnot(None))
    if email_only:
        scope.append(LeadDeliveryJob.provider == "resend")
    if db.scalar(select(LeadDeliveryJob.id).where(*scope)) is None:
        raise HTTPException(status_code=404, detail="Delivery record not found")
    now = datetime.now(timezone.utc)
    changed = db.execute(update(LeadDeliveryJob).where(
        *scope, LeadDeliveryJob.status.in_(("failed", "retry_wait")),
        LeadDeliveryJob.safe_to_retry.is_(True), LeadDeliveryJob.outcome_uncertain.is_(False),
        LeadDeliveryJob.attempt_count > 0, LeadDeliveryJob.attempt_count < MAX_ATTEMPTS,
        LeadDeliveryJob.next_attempt_at <= now,
        LeadDeliveryJob.error_code.op("~")(r"^[a-z0-9_]{1,120}$"),
    ).values(status="pending", updated_at=now, safe_to_retry=True,
             error_code=MANUAL_RETRY_PREFIX + LeadDeliveryJob.error_code))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="This delivery is not eligible for one manual retry. Review its provider outcome, cooldown or attempt limit.")
    db.commit()
    return {"id": str(job_id), "status": "pending", "enabled": enabled(),
            "note": "One attempt authorized for the enabled worker; further failures require a new manual action. This action made no provider request."}


@router.get("/admin/communications")
def communications(response: Response, status: str | None = None, event_kind: str | None = None,
                   limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10000),
                   user=Depends(require_admin), db: Session = Depends(get_db)):
    """Email operational metadata only: no recipients, event-subject IDs or bodies."""
    _private(response)
    if status is not None and status not in DELIVERY_STATUSES:
        raise HTTPException(422, "Invalid communication status")
    if event_kind is not None and event_kind not in {"lead", "signup"}:
        raise HTTPException(422, "Invalid communication event")
    query = _metadata_query(db, include_signup=True).filter(LeadDeliveryJob.provider == "resend")
    if status:
        query = query.filter(LeadDeliveryJob.status == status)
    if event_kind == "signup":
        query = query.filter(LeadDeliveryJob.user_id.isnot(None))
    elif event_kind == "lead":
        query = query.filter(LeadDeliveryJob.lead_id.isnot(None))
    total = query.count()
    current_time = datetime.now(timezone.utc)
    rows = query.order_by(LeadDeliveryJob.created_at.desc(), LeadDeliveryJob.id).offset(offset).limit(limit).all()
    return {"total": total, "limit": limit, "offset": offset, "enabled": enabled(),
            "customer_messages_enabled": bool(settings.customer_messages_enabled),
            "items": [{"id": str(row.id), "event_kind": "signup" if row.user_id else "lead", "channel": "email",
                "step": row.step, "provider": row.provider, "status": row.status, "attempts": row.attempt_count,
                "max_attempts": MAX_ATTEMPTS, "created_at": row.created_at, "accepted_at": row.accepted_at,
                "first_attempt_at": row.first_attempt_at, "last_attempt_at": row.last_attempt_at,
                "next_attempt_at": row.next_attempt_at, "error_code": delivery_error_code(row.error_code),
                "outcome_uncertain": row.outcome_uncertain, "can_retry": _can_retry(row, current_time)} for row in rows],
            "note": "Provider acceptance is not inbox delivery. No historical accounts or inquiries are enqueued."}


@router.post("/admin/communications/{job_id}/retry")
def retry_communication(job_id: UUID, request: RetryDeliveryRequest, response: Response,
                        user=Depends(require_admin), db: Session = Depends(get_db)):
    _private(response)
    return _authorize_retry(db, job_id, lead_only=False, email_only=True)
