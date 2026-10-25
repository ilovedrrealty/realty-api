from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from typing import Optional, List, Literal
from datetime import datetime, timezone

from database import get_db
from models.lead import Lead
from models.user import User
from models.listing import Listing
from models.booking import Booking
from utils.auth import get_current_user, get_optional_user
from utils.permission import require_admin
from utils.agents import find_eligible_agent
from utils.limiter import limiter
from utils.email import send_upgrade_approved_email, send_realtor_assigned_owner_email, send_lead_notification, send_realtor_lead_assigned_email
from utils import ghl
from config import settings
from models.site_settings import SiteSettings
from utils.phone import normalize_phone
from schemas.lead_tracking import LeadTracking
from utils.lead_tracking import tracking_metadata, measurement_receipt, private_tracking_summary

router = APIRouter(tags=["leads"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class LeadCreate(BaseModel):
    type: Literal["property_inquiry", "buyer_interest", "seller_interest", "rental_interest", "booking"] = "property_inquiry"
    name: str = Field(min_length=1, max_length=200)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=50)
    message: Optional[str] = Field(default=None, max_length=2000)
    property_id: Optional[str] = None
    submission_id: Optional[UUID] = None
    tracking: Optional[LeadTracking] = None

    @field_validator("name", "email", mode="before")
    @classmethod
    def strip_contact_fields(cls, value, info):
        if isinstance(value, str):
            value = value.strip()
            if info.field_name == "email" and not value:
                return None
        return value

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value):
        return normalize_phone(value)

    @model_validator(mode="after")
    def require_contact(self):
        if self.type in {"buyer_interest", "seller_interest", "rental_interest"}:
            if not self.email and not self.phone:
                raise ValueError("Provide a valid phone number or email address")
        elif not self.email:
            raise ValueError("Email is required for property inquiries and bookings")
        return self


class LeadResponse(BaseModel):
    id: str
    type: str
    name: str
    email: Optional[str]
    phone: Optional[str]
    message: Optional[str]
    property_id: Optional[str]
    property_title: Optional[str]
    listing_realtor_id: Optional[str]
    listing_realtor_name: Optional[str]
    listing_owner_id: Optional[str]
    listing_owner_name: Optional[str]
    from_user_id: Optional[str]
    from_user_code: Optional[str]
    from_user_name: Optional[str]
    from_user_avatar_url: Optional[str]
    assigned_realtor_id: Optional[str]
    assigned_realtor_name: Optional[str]
    status: str
    created_at: datetime
    assigned_at: Optional[datetime]
    contacted_at: Optional[datetime]
    closed_at: Optional[datetime]
    ghl_contact_url: Optional[str]
    booking_status: Optional[str] = None
    tracking_summary: Optional[dict] = None

    class Config:
        from_attributes = True


class AssignBody(BaseModel):
    realtor_id: str


class StatusBody(BaseModel):
    status: str  # new | assigned | contacted | closed


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_realtor(user):
    if user.role not in ("realtor", "admin"):
        raise HTTPException(status_code=403, detail="Realtor access required")


def _apply_status_timestamp(lead: Lead, status: str) -> None:
    now = datetime.now(timezone.utc)
    if status == "assigned" and lead.assigned_at is None:
        lead.assigned_at = now
    elif status == "contacted" and lead.contacted_at is None:
        lead.contacted_at = now
    elif status == "closed" and lead.closed_at is None:
        lead.closed_at = now


def _build_response(lead: Lead, db: Session, include_ghl: bool = False, include_tracking: bool = False) -> LeadResponse:
    property_title = None
    listing_realtor_id = None
    listing_realtor_name = None
    listing_owner_id = None
    listing_owner_name = None
    if lead.property_id:
        listing = db.query(Listing).filter(Listing.id == lead.property_id).first()
        if listing:
            property_title = listing.title
            agent_id = listing.assigned_realtor_id or listing.submitted_by
            if agent_id:
                agent = db.query(User).filter(User.id == agent_id).first()
                if agent and agent.role in ("realtor", "admin"):
                    listing_realtor_id = str(agent.id)
                    listing_realtor_name = agent.display_name or agent.email
            if listing.owner_id:
                owner = db.query(User).filter(User.id == listing.owner_id).first()
                if owner and owner.role == "owner":
                    listing_owner_id = str(owner.id)
                    listing_owner_name = owner.display_name or owner.email

    assigned_realtor_name = None
    if lead.assigned_realtor_id:
        realtor = db.query(User).filter(User.id == lead.assigned_realtor_id).first()
        if realtor:
            assigned_realtor_name = realtor.display_name or realtor.email

    from_user_code = None
    from_user_name = None
    from_user_avatar_url = None
    if lead.from_user_id:
        from_user = db.query(User).filter(User.id == lead.from_user_id).first()
        if from_user:
            from_user_code = str(from_user.user_code) if from_user.user_code is not None else None
            from_user_name = from_user.display_name or from_user.email
            from_user_avatar_url = from_user.avatar_url

    booking_status = None
    if lead.type == "booking":
        booking = db.query(Booking).filter(Booking.lead_id == lead.id).first()
        if booking:
            booking_status = booking.status

    ghl_contact_url = None
    if include_ghl and lead.ghl_contact_id and settings.ghl_location_id:
        ghl_contact_url = (
            f"https://app.gohighlevel.com/v2/location/{settings.ghl_location_id}"
            f"/contacts/detail/{lead.ghl_contact_id}"
        )

    return LeadResponse(
        id=str(lead.id),
        type=lead.type,
        name=lead.name,
        email=lead.email,
        phone=lead.phone,
        message=lead.message,
        property_id=str(lead.property_id) if lead.property_id else None,
        property_title=property_title,
        listing_realtor_id=listing_realtor_id,
        listing_realtor_name=listing_realtor_name,
        listing_owner_id=listing_owner_id,
        listing_owner_name=listing_owner_name,
        from_user_id=str(lead.from_user_id) if lead.from_user_id else None,
        from_user_code=from_user_code,
        from_user_name=from_user_name,
        from_user_avatar_url=from_user_avatar_url,
        assigned_realtor_id=str(lead.assigned_realtor_id) if lead.assigned_realtor_id else None,
        assigned_realtor_name=assigned_realtor_name,
        status=lead.status,
        created_at=lead.created_at,
        assigned_at=lead.assigned_at,
        contacted_at=lead.contacted_at,
        closed_at=lead.closed_at,
        ghl_contact_url=ghl_contact_url,
        booking_status=booking_status,
        tracking_summary=private_tracking_summary(lead) if include_tracking else None,
    )


def _get_notify_email(db: Session) -> str:
    row = db.query(SiteSettings).filter(SiteSettings.id == 1).first()
    if row and row.data and row.data.get("notify_email"):
        return row.data["notify_email"]
    return settings.notify_email


# ── Public endpoints ──────────────────────────────────────────────────────────

def _receipt(lead: Lead) -> dict:
    return {"id": str(lead.id), "status": lead.status, "measurement": measurement_receipt(lead)}


def _existing_submission(body: LeadCreate, db: Session, user, legacy: bool = False):
    if not body.submission_id:
        return None
    lead = db.query(Lead).filter(Lead.submission_id == body.submission_id).first()
    if lead is None:
        return None
    # A UUID is an idempotency key, not permission to read someone else's lead.
    expected = (body.type, body.name, str(body.email) if body.email else None, body.phone, body.message,
                None if legacy else (str(body.property_id) if body.property_id else None),
                None if legacy or not user else str(user.id))
    actual = (lead.type, lead.name, lead.email, lead.phone, lead.message,
              str(lead.property_id) if lead.property_id else None,
              str(lead.from_user_id) if lead.from_user_id else None)
    submitted_tracking = body.tracking.model_dump(mode="json", exclude_none=True) if body.tracking else None
    saved_tracking = {k: v for k, v in (lead.tracking or {}).items() if k not in {"received_at", "version"}} or None
    if actual != expected or submitted_tracking != saved_tracking:
        raise HTTPException(status_code=409, detail="This submission identifier was already used. Start a new request.")
    return lead


def _persist_submission(body: LeadCreate, db: Session, user=None, legacy: bool = False):
    existing = _existing_submission(body, db, user, legacy)
    if existing:
        return existing, False
    lead = Lead(type=body.type, name=body.name, email=body.email, phone=body.phone, message=body.message,
                property_id=UUID(body.property_id) if body.property_id and not legacy else None,
                from_user_id=user.id if user and not legacy else None, status="new",
                submission_id=body.submission_id, tracking=tracking_metadata(body))
    db.add(lead)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Concurrent retry: the database's unique index decides the winner.
        existing = _existing_submission(body, db, user, legacy)
        if existing is None:
            raise
        return existing, False
    db.refresh(lead)
    return lead, True

@router.post("/leads", status_code=201)
@limiter.limit("3/minute")
def create_lead(
    request: Request,
    response: Response,
    body: LeadCreate,
    db: Session = Depends(get_db),
    user=Depends(get_optional_user),
):
    response.headers["Cache-Control"] = "no-store"
    lead, created = _persist_submission(body, db, user)
    if not created:
        return _receipt(lead)

    property_info = None
    if lead.property_id:
        listing = db.query(Listing).filter(Listing.id == lead.property_id).first()
        if listing:
            property_info = {
                "id": str(listing.id),
                "title": listing.title,
                "price": float(listing.price) if listing.price else None,
                "location": listing.location,
                "bedrooms": listing.bedrooms,
                "bathrooms": float(listing.bathrooms) if listing.bathrooms else None,
                "listing_type": listing.type,
            }
    ghl.create_contact(lead, property_info, db)
    try:
        send_lead_notification(lead, property_info, notify_email=_get_notify_email(db))
    except Exception:
        pass

    return _receipt(lead)


# Legacy capture endpoint — keeps existing callers working
@router.post("/leads/capture", status_code=201)
@limiter.limit("5/minute")
def capture_lead(
    request: Request,
    response: Response,
    body: LeadCreate,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    lead, created = _persist_submission(body, db, legacy=True)
    if not created:
        return _receipt(lead)
    try:
        send_lead_notification(lead, notify_email=_get_notify_email(db))
    except Exception:
        pass
    return _receipt(lead)


# ── Buyer ─────────────────────────────────────────────────────────────────────

@router.get("/leads/mine", response_model=List[LeadResponse])
def get_my_leads(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    leads = (
        db.query(Lead)
        .filter(Lead.from_user_id == user.id)
        .order_by(Lead.created_at.desc())
        .all()
    )
    return [_build_response(l, db) for l in leads]


# ── Realtor ───────────────────────────────────────────────────────────────────

@router.get("/realtor/leads", response_model=List[LeadResponse])
def get_realtor_leads(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_realtor(user)
    leads = (
        db.query(Lead)
        .filter(Lead.assigned_realtor_id == user.id)
        .order_by(Lead.created_at.desc())
        .all()
    )
    return [_build_response(l, db, include_ghl=True) for l in leads]


@router.put("/realtor/leads/{lead_id}/status", status_code=204)
def realtor_update_lead_status(
    lead_id: str,
    body: StatusBody,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_realtor(user)
    allowed = {"schedule", "contacted", "closed"}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail="Realtors can only set status to schedule, contacted, or closed")
    lead = db.query(Lead).filter(Lead.id == UUID(lead_id)).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if str(lead.assigned_realtor_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Not assigned to you")
    lead.status = body.status
    _apply_status_timestamp(lead, body.status)
    db.commit()

    if lead.ghl_contact_id:
        ghl.update_contact_status(lead.ghl_contact_id, body.status)


# ── Admin ─────────────────────────────────────────────────────────────────────

@router.get("/admin/leads", response_model=List[LeadResponse])
def admin_get_leads(
    response: Response,
    type: Optional[str] = None,
    status: Optional[str] = None,
    assigned_realtor_id: Optional[str] = None,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    q = db.query(Lead)
    if type:
        q = q.filter(Lead.type == type)
    if status:
        q = q.filter(Lead.status == status)
    if assigned_realtor_id:
        q = q.filter(Lead.assigned_realtor_id == UUID(assigned_realtor_id))
    leads = q.order_by(Lead.created_at.desc()).all()
    return [_build_response(l, db, include_ghl=True, include_tracking=True) for l in leads]


@router.get("/admin/leads/tracking-summary")
def admin_lead_tracking_summary(response: Response, user=Depends(require_admin), db: Session = Depends(get_db)):
    # Aggregate only. No names, contacts, free text or raw advertising IDs.
    response.headers["Cache-Control"] = "no-store"
    from datetime import timedelta
    since = datetime.now(timezone.utc) - timedelta(days=30)
    rows = db.query(Lead.type, Lead.tracking, Lead.ghl_contact_id, Lead.ghl_sync_error).filter(Lead.created_at >= since).all()
    result = {"window_days": 30, "total": len(rows), "tracked_submissions": 0, "internal_tests": 0,
              "measurement_granted": 0, "with_ad_click_id": 0, "crm_linked": 0, "crm_errors": 0, "by_intent": {}}
    for row in rows:
        tracking = row.tracking or {}
        result["tracked_submissions"] += bool(tracking)
        result["internal_tests"] += tracking.get("internal_test") is True
        if tracking.get("internal_test") is not True:
            intent = tracking.get("intent")
            intent_key = intent if intent in {"buy", "sell", "rent", "general"} else "legacy_" + row.type
            result["by_intent"][intent_key] = result["by_intent"].get(intent_key, 0) + 1
            result["measurement_granted"] += tracking.get("measurement_consent") == "granted"
            result["with_ad_click_id"] += any((tracking.get("attribution") or {}).get(k) for k in ("gclid", "gbraid", "wbraid"))
        result["crm_linked"] += bool(row.ghl_contact_id)
        result["crm_errors"] += bool(row.ghl_sync_error)
    result["note"] = "Stored inquiry coverage, not Google Ads conversion totals. Legacy records have unknown measurement/test status."
    return result


@router.put("/admin/leads/{lead_id}/assign", status_code=204)
def admin_assign_lead(
    lead_id: str,
    body: AssignBody,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    lead = db.query(Lead).filter(Lead.id == UUID(lead_id)).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    realtor = find_eligible_agent(db, body.realtor_id)
    if not realtor:
        raise HTTPException(status_code=404, detail="Active verified agent not found")
    lead.assigned_realtor_id = realtor.id
    lead.status = "assigned"
    _apply_status_timestamp(lead, "assigned")
    # Upgrade the submitting user to owner when their seller_interest lead is assigned
    owner_upgraded = None
    if lead.type == "seller_interest" and lead.from_user_id:
        owner = db.query(User).filter(User.id == lead.from_user_id).first()
        if owner and owner.role == "buyer":
            owner.role = "owner"
            owner_upgraded = owner
    # Mirror realtor assignment onto the submitting user so their "My Agent" card reflects it
    if lead.from_user_id:
        submitter = db.query(User).filter(User.id == lead.from_user_id).first()
        if submitter and submitter.role in ("owner", "buyer", "admin"):
            submitter.assigned_realtor_id = realtor.id
    db.commit()

    if lead.ghl_contact_id:
        ghl.update_contact_status(lead.ghl_contact_id, "assigned")

    # Notify assigned realtor
    try:
        notify_property_info = None
        if lead.property_id:
            notify_listing = db.query(Listing).filter(Listing.id == lead.property_id).first()
            if notify_listing:
                notify_property_info = {"id": str(notify_listing.id), "title": notify_listing.title}
        send_realtor_lead_assigned_email(
            realtor.email,
            realtor.display_name or realtor.email,
            lead,
            notify_property_info,
            calendly_url=getattr(realtor, 'calendly_url', None),
        )
    except Exception:
        pass

    # Notify both parties after commit
    if owner_upgraded is not None:
        try:
            send_upgrade_approved_email(
                owner_upgraded.email,
                owner_upgraded.display_name or owner_upgraded.email,
                "owner",
            )
        except Exception:
            pass
        try:
            send_realtor_assigned_owner_email(
                realtor.email,
                realtor.display_name or realtor.email,
                owner_upgraded.display_name or owner_upgraded.email,
                owner_upgraded.email,
            )
        except Exception:
            pass


@router.put("/admin/leads/{lead_id}/status", status_code=204)
def admin_update_lead_status(
    lead_id: str,
    body: StatusBody,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    valid = {"new", "assigned", "contacted", "closed"}
    if body.status not in valid:
        raise HTTPException(status_code=400, detail=f"status must be one of {valid}")
    lead = db.query(Lead).filter(Lead.id == UUID(lead_id)).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.status = body.status
    _apply_status_timestamp(lead, body.status)
    db.commit()

    if lead.ghl_contact_id:
        ghl.update_contact_status(lead.ghl_contact_id, body.status)
