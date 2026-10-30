from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, field_validator
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, aliased
from typing import List, Optional
from uuid import UUID

from models.booking import Booking
from models.lead import Lead
from routes.bookings import _lead_to_booking_response
from schemas.booking import BookingResponse

from config import settings
from database import get_db, SessionLocal
from models.activity_log import ActivityLog
from models.site_settings import SiteSettings
from models.upgrade_request import UpgradeRequest
from models.listing import Listing
from models.listing_edit import ListingEdit
from models.listing_event import ListingEvent
from models.user import User
from models.deal_request import DealRequest
from models.bulk_import_job import BulkImportJob
from schemas.auth import CreateAdminUserBody
from schemas.upgrade_request import UpgradeRequestAdminResponse, AdminRejectBody as UpgradeRejectBody
from schemas.listing import (
    ListingResponse, AdminListingResponse, AdminListingPageResponse, AdminRejectBody as ListingRejectBody,
    AdminAssignListingBody, BulkImportJobResponse,
)
from schemas.deal_request import DealRequestResponse, DealRequestRejectBody
from schemas.listing_edit import ListingEditResponse, ListingEditRejectBody
from schemas.listing_event import ListingEventResponse
from utils.permission import require_admin
from utils.agents import eligible_agent_query, find_eligible_agent
from utils.security import hash_password
from utils.share_image import generate_share_image, maybe_regenerate_share_image
from utils.bulk_import import parse_csv, run_bulk_import
from utils.email import (
    parse_notification_recipients,
    send_listing_approved_email,
    send_listing_rejected_email,
    send_upgrade_approved_email,
    send_upgrade_rejected_email,
)


def _log(db: Session, event_type: str, description: str, actor_id=None):
    db.add(ActivityLog(event_type=event_type, description=description, actor_id=actor_id))


def _listing_event(
    db: Session,
    listing_id,
    event_type: str,
    actor_id=None,
    note: str = None,
    snapshot_before: dict = None,
    snapshot_after: dict = None,
):
    db.add(ListingEvent(
        listing_id=listing_id,
        event_type=event_type,
        actor_id=actor_id,
        note=note,
        snapshot_before=snapshot_before,
        snapshot_after=snapshot_after,
    ))

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Upgrade Requests ──────────────────────────────────────────────────────────

@router.get("/upgrade-requests", response_model=List[UpgradeRequestAdminResponse])
def list_upgrade_requests(status: Optional[str] = Query(None), user=Depends(require_admin), db: Session = Depends(get_db)):
    Reviewer = aliased(User)
    q = (
        db.query(UpgradeRequest, User, Reviewer)
        .join(User, User.id == UpgradeRequest.user_id)
        .outerjoin(Reviewer, Reviewer.id == UpgradeRequest.reviewed_by)
    )
    if status:
        q = q.filter(UpgradeRequest.status == status)
    rows = q.order_by(UpgradeRequest.created_at.desc()).all()
    return [
        UpgradeRequestAdminResponse(
            id=req.id,
            user_id=req.user_id,
            user_email=u.email,
            user_display_name=u.display_name,
            requested_role=req.requested_role,
            status=req.status,
            rejection_reason=req.rejection_reason,
            created_at=req.created_at,
            reviewed_by_name=reviewer.display_name or reviewer.email if reviewer else None,
            reviewed_at=req.reviewed_at,
            license_number=req.license_number,
            territory=req.territory,
            years_experience=req.years_experience,
            specialties=req.specialties,
            bio=req.bio,
        )
        for req, u, reviewer in rows
    ]


@router.post("/upgrade-requests/{req_id}/approve", status_code=204)
def approve_upgrade_request(req_id: UUID, user=Depends(require_admin), db: Session = Depends(get_db)):
    req = db.query(UpgradeRequest).filter(UpgradeRequest.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != "pending":
        raise HTTPException(status_code=409, detail="Request already reviewed")

    req.status = "approved"
    req.reviewed_by = user.id
    req.reviewed_at = datetime.now(timezone.utc)

    target_user = db.query(User).filter(User.id == req.user_id).first()
    if target_user:
        target_user.role = req.requested_role

    role_label = req.requested_role.capitalize()
    name = target_user.display_name or target_user.email if target_user else str(req.user_id)
    _log(db, "upgrade_approved", f"New {role_label} approved: {name}", actor_id=user.id)
    db.commit()
    if target_user:
        try:
            send_upgrade_approved_email(target_user.email, target_user.display_name or target_user.email, req.requested_role)
        except Exception:
            pass


@router.post("/upgrade-requests/{req_id}/reject", status_code=204)
def reject_upgrade_request(req_id: UUID, body: UpgradeRejectBody, user=Depends(require_admin), db: Session = Depends(get_db)):
    req = db.query(UpgradeRequest).filter(UpgradeRequest.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != "pending":
        raise HTTPException(status_code=409, detail="Request already reviewed")

    req.status = "rejected"
    req.reviewed_by = user.id
    req.reviewed_at = datetime.now(timezone.utc)
    req.rejection_reason = body.reason
    target_user = db.query(User).filter(User.id == req.user_id).first()
    name = target_user.display_name or target_user.email if target_user else str(req.user_id)
    _log(db, "upgrade_rejected", f"Upgrade request rejected: {name}", actor_id=user.id)
    db.commit()
    if target_user:
        try:
            send_upgrade_rejected_email(target_user.email, target_user.display_name or target_user.email, body.reason)
        except Exception:
            pass


# ── Listings ──────────────────────────────────────────────────────────────────

def _admin_listing_query(db: Session):
    Submitter = aliased(User)
    Reviewer  = aliased(User)
    AssignedRealtor = aliased(User)
    return (
        db.query(Listing, Submitter, Reviewer, AssignedRealtor)
        .join(Submitter, Submitter.id == Listing.submitted_by)
        .outerjoin(Reviewer, Reviewer.id == Listing.approved_by)
        .outerjoin(AssignedRealtor, AssignedRealtor.id == Listing.assigned_realtor_id)
    )


def _to_admin_listing_response(row) -> AdminListingResponse:
    listing, submitter, reviewer, assigned_realtor = row
    return AdminListingResponse(
        **{c.key: getattr(listing, c.key) for c in Listing.__table__.columns},
        submitted_by_name=submitter.display_name,
        submitted_by_email=submitter.email,
        reviewed_by_name=reviewer.display_name if reviewer else None,
        reviewed_by_email=reviewer.email if reviewer else None,
        reviewed_at=listing.approved_at,
        assigned_realtor_name=assigned_realtor.display_name if assigned_realtor else None,
        assigned_realtor_email=assigned_realtor.email if assigned_realtor else None,
    )


@router.get("/listings", response_model=AdminListingPageResponse)
def list_all_listings(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    status: Optional[str] = Query(None),
    exclude_status: Optional[str] = Query(None),
    co_listing_enabled: Optional[bool] = Query(None),
    is_deal: Optional[bool] = Query(None),
    q: Optional[str] = Query(None),
    user=Depends(require_admin), db: Session = Depends(get_db),
):
    query = _admin_listing_query(db)
    if status:
        query = query.filter(Listing.status == status)
    if exclude_status:
        query = query.filter(Listing.status != exclude_status)
    if co_listing_enabled is not None:
        query = query.filter(Listing.co_listing_enabled == co_listing_enabled)
    if is_deal is not None:
        query = query.filter(Listing.is_deal == is_deal)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Listing.title.ilike(like), Listing.location.ilike(like)))

    total = query.with_entities(func.count(Listing.id)).scalar() or 0
    rows = query.order_by(Listing.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return AdminListingPageResponse(
        items=[_to_admin_listing_response(row) for row in rows],
        total=total, page=page, page_size=page_size,
    )


@router.put("/listings/{listing_id}/assign", status_code=204)
def assign_listing_realtor(listing_id: UUID, body: AdminAssignListingBody, user=Depends(require_admin), db: Session = Depends(get_db)):
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if body.realtor_id:
        realtor = find_eligible_agent(db, body.realtor_id)
        if not realtor:
            raise HTTPException(status_code=404, detail="Active verified agent not found")
        listing.assigned_realtor_id = realtor.id
        _log(db, "listing_assigned", f"Listing assigned to {realtor.display_name or realtor.email}: {listing.title}", actor_id=user.id)
        _listing_event(db, listing.id, "realtor_assigned", actor_id=user.id, note=realtor.display_name or realtor.email)
    else:
        listing.assigned_realtor_id = None
        _log(db, "listing_unassigned", f"Listing unassigned: {listing.title}", actor_id=user.id)
        _listing_event(db, listing.id, "realtor_unassigned", actor_id=user.id)

    db.commit()


@router.post("/listings/bulk-import", response_model=BulkImportJobResponse, status_code=202)
async def bulk_import_listings(
    background_tasks: BackgroundTasks,
    csv_file: UploadFile = File(...),
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    raw = await csv_file.read()
    try:
        rows = parse_csv(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    job = BulkImportJob(status="pending", total_rows=len(rows), created_by=user.id)
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(run_bulk_import, job.id, rows, user.id, SessionLocal)
    return job


@router.get("/listings/bulk-import", response_model=List[BulkImportJobResponse])
def list_bulk_import_jobs(user=Depends(require_admin), db: Session = Depends(get_db)):
    return db.query(BulkImportJob).order_by(BulkImportJob.created_at.desc()).limit(20).all()


@router.get("/listings/bulk-import/{job_id}", response_model=BulkImportJobResponse)
def get_bulk_import_job(job_id: UUID, user=Depends(require_admin), db: Session = Depends(get_db)):
    job = db.query(BulkImportJob).filter(BulkImportJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")
    return job


@router.get("/listings/{listing_id}", response_model=AdminListingResponse)
def get_admin_listing(listing_id: UUID, user=Depends(require_admin), db: Session = Depends(get_db)):
    row = _admin_listing_query(db).filter(Listing.id == listing_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Listing not found")
    return _to_admin_listing_response(row)


@router.post("/listings/{listing_id}/approve", status_code=204)
def approve_listing(listing_id: UUID, user=Depends(require_admin), db: Session = Depends(get_db)):
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if listing.status != "pending_approval":
        raise HTTPException(status_code=409, detail="Listing not in pending state")

    listing.status = "active"
    listing.approved_by = user.id
    listing.approved_at = datetime.now(timezone.utc)
    _log(db, "listing_approved", f"Listing approved: {listing.title}", actor_id=user.id)
    _listing_event(db, listing.id, "approved", actor_id=user.id)
    db.commit()
    submitter = db.query(User).filter(User.id == listing.submitted_by).first()
    if submitter:
        try:
            listing_url = f"{settings.landing_url}/property?id={listing.id}"
            send_listing_approved_email(submitter.email, submitter.display_name or submitter.email, listing.title, listing_url)
        except Exception:
            pass


@router.post("/listings/{listing_id}/archive", status_code=204)
def archive_listing(listing_id: UUID, user=Depends(require_admin), db: Session = Depends(get_db)):
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    listing.status = "archived"
    listing.approved_by = user.id
    listing.approved_at = datetime.now(timezone.utc)
    _log(db, "listing_archived", f"Listing archived: {listing.title}", actor_id=user.id)
    _listing_event(db, listing.id, "archived", actor_id=user.id)
    db.commit()


@router.post("/listings/{listing_id}/reject", status_code=204)
def reject_listing(listing_id: UUID, body: ListingRejectBody, user=Depends(require_admin), db: Session = Depends(get_db)):
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    listing.status = "rejected"
    listing.rejection_reason = body.reason
    listing.approved_by = user.id
    listing.approved_at = datetime.now(timezone.utc)
    _log(db, "listing_rejected", f"Listing rejected: {listing.title}", actor_id=user.id)
    _listing_event(db, listing.id, "rejected", actor_id=user.id, note=body.reason)
    db.commit()
    submitter = db.query(User).filter(User.id == listing.submitted_by).first()
    if submitter:
        try:
            send_listing_rejected_email(submitter.email, submitter.display_name or submitter.email, listing.title, body.reason)
        except Exception:
            pass


@router.post("/listings/{listing_id}/regenerate-share-image", status_code=204)
def regenerate_share_image(listing_id: UUID, user=Depends(require_admin), db: Session = Depends(get_db)):
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    listing.share_image_url = generate_share_image(listing)
    db.commit()


class BulkRegenerateResponse(BaseModel):
    processed: int
    succeeded: int
    failed_ids: List[str]


@router.post("/listings/regenerate-share-images", response_model=BulkRegenerateResponse)
def bulk_regenerate_share_images(
    only_missing: bool = Query(True, description="Skip listings that already have a share_image_url"),
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(Listing).filter(Listing.status != "archived")
    if only_missing:
        q = q.filter(Listing.share_image_url.is_(None))
    listings = q.all()

    failed_ids: List[str] = []
    for listing in listings:
        try:
            listing.share_image_url = generate_share_image(listing)
        except Exception:
            failed_ids.append(str(listing.id))
    db.commit()

    return BulkRegenerateResponse(
        processed=len(listings),
        succeeded=len(listings) - len(failed_ids),
        failed_ids=failed_ids,
    )


# ── Listing Edits ─────────────────────────────────────────────────────────────

EDIT_FIELDS = [
    "title", "description", "description_es", "type", "transaction", "price", "location",
    "bedrooms", "bathrooms", "area_sqft", "lot_size_sqft", "roi",
    "seller_financing", "hoa", "hoa_fee", "tax_exempt", "gated_community",
    "construction_status", "year_built", "features", "maps_url",
    "latitude", "longitude", "tag", "images",
    "tags", "video_links", "tour_3d_url", "utilities", "included_utilities",
    "association_fee", "deposit_policy",
    "co_listing_enabled", "co_listing_brokerage", "co_listing_agent_name",
    "co_listing_agent_contact", "co_listing_agent_email",
    "co_listing_brokerage_email", "co_listing_brokerage_phone", "co_listing_commission_split",
    "co_listing_notes", "co_listing_status",
]


def _listing_snapshot(listing: Listing) -> dict:
    snap = {}
    for field in EDIT_FIELDS:
        val = getattr(listing, field, None)
        if isinstance(val, Decimal):
            val = float(val)
        snap[field] = val
    return snap


@router.get("/listing-edits", response_model=List[ListingEditResponse])
def list_listing_edits(user=Depends(require_admin), db: Session = Depends(get_db)):
    Submitter = aliased(User)
    rows = (
        db.query(ListingEdit, Listing, Submitter)
        .join(Listing, Listing.id == ListingEdit.listing_id)
        .join(Submitter, Submitter.id == ListingEdit.submitted_by)
        .filter(ListingEdit.status == "pending")
        .order_by(ListingEdit.submitted_at.asc())
        .all()
    )
    return [
        ListingEditResponse(
            id=edit.id,
            listing_id=edit.listing_id,
            listing_title=listing.title,
            listing_location=listing.location,
            listing_thumbnail=(listing.images or [None])[0],
            submitted_by_name=submitter.display_name,
            submitted_by_email=submitter.email,
            submitted_at=edit.submitted_at,
            status=edit.status,
            current_data=_listing_snapshot(listing),
            proposed_data=edit.proposed_data,
            rejection_reason=edit.rejection_reason,
        )
        for edit, listing, submitter in rows
    ]


@router.post("/listing-edits/{edit_id}/approve", status_code=204)
def approve_listing_edit(edit_id: UUID, user=Depends(require_admin), db: Session = Depends(get_db)):
    edit = db.query(ListingEdit).filter(ListingEdit.id == edit_id).first()
    if not edit:
        raise HTTPException(status_code=404, detail="Edit not found")
    if edit.status != "pending":
        raise HTTPException(status_code=409, detail="Edit already reviewed")

    listing = db.query(Listing).filter(Listing.id == edit.listing_id).with_for_update().first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    proposed = dict(edit.proposed_data or {})
    before = _listing_snapshot(listing)
    from utils.location_quality import stage_location_change
    stage_location_change(db, listing, proposed, user.id)
    _NOT_NULL_BOOLEANS = {"seller_financing", "hoa", "tax_exempt", "gated_community", "co_listing_enabled"}
    for field in EDIT_FIELDS:
        if field in proposed:
            val = proposed[field]
            if val is None and field in _NOT_NULL_BOOLEANS:
                continue
            setattr(listing, field, val)

    listing.updated_at = datetime.now(timezone.utc)
    maybe_regenerate_share_image(listing, proposed.keys())
    edit.status = "approved"
    edit.reviewed_by = user.id
    edit.reviewed_at = datetime.now(timezone.utc)
    _log(db, "edit_approved", f"Listing edit approved: {listing.title}", actor_id=user.id)
    _listing_event(db, listing.id, "edit_approved", actor_id=user.id, snapshot_before=before, snapshot_after=proposed)
    db.commit()


@router.post("/listing-edits/{edit_id}/reject", status_code=204)
def reject_listing_edit(edit_id: UUID, body: ListingEditRejectBody, user=Depends(require_admin), db: Session = Depends(get_db)):
    edit = db.query(ListingEdit).filter(ListingEdit.id == edit_id).first()
    if not edit:
        raise HTTPException(status_code=404, detail="Edit not found")
    if edit.status != "pending":
        raise HTTPException(status_code=409, detail="Edit already reviewed")

    listing = db.query(Listing).filter(Listing.id == edit.listing_id).first()
    edit.status = "rejected"
    edit.reviewed_by = user.id
    edit.reviewed_at = datetime.now(timezone.utc)
    edit.rejection_reason = body.reason
    _log(db, "edit_rejected", f"Listing edit rejected", actor_id=user.id)
    _listing_event(
        db, edit.listing_id, "edit_rejected", actor_id=user.id,
        note=body.reason, snapshot_after=edit.proposed_data,
    )
    db.commit()


# ── Listing History ───────────────────────────────────────────────────────────

@router.get("/listings/{listing_id}/history", response_model=List[ListingEventResponse])
def get_listing_history(listing_id: UUID, user=Depends(require_admin), db: Session = Depends(get_db)):
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    Actor = aliased(User)
    rows = (
        db.query(ListingEvent, Actor)
        .outerjoin(Actor, Actor.id == ListingEvent.actor_id)
        .filter(ListingEvent.listing_id == listing_id)
        .order_by(ListingEvent.created_at.desc())
        .all()
    )
    return [
        ListingEventResponse(
            id=ev.id,
            listing_id=ev.listing_id,
            event_type=ev.event_type,
            actor_name=actor.display_name if actor else None,
            actor_email=actor.email if actor else None,
            note=ev.note,
            snapshot_before=ev.snapshot_before,
            snapshot_after=ev.snapshot_after,
            created_at=ev.created_at,
        )
        for ev, actor in rows
    ]


# ── Users ─────────────────────────────────────────────────────────────────────

_VALID_ROLES    = {'buyer', 'owner', 'realtor', 'admin'}
_VALID_STATUSES = {'active', 'suspended'}


class AgentAssignmentChoice(BaseModel):
    id: UUID
    display_name: str
    email: str
    role: str


@router.get("/agents", response_model=List[AgentAssignmentChoice])
def list_assignment_agents(
    limit: int = Query(500, ge=1, le=500),
    q: Optional[str] = Query(None, max_length=80),
    user=Depends(require_admin), db: Session = Depends(get_db),
):
    query = eligible_agent_query(db).with_entities(User.id, User.display_name, User.email, User.role)
    if q:
        # Literal substring matching, not wildcard expansion.
        query = query.filter(or_(User.display_name.icontains(q, autoescape=True), User.email.icontains(q, autoescape=True)))
    rows = query.order_by(func.lower(User.display_name), User.id).limit(limit).all()
    return [{"id": row.id, "display_name": row.display_name, "email": row.email, "role": row.role} for row in rows]


@router.get("/users")
def list_users(role: Optional[str] = Query(None), status: Optional[str] = Query(None), user=Depends(require_admin), db: Session = Depends(get_db)):
    if role and role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail="Invalid role")
    if status and status not in _VALID_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid status")
    Realtor = aliased(User)
    q = db.query(User, Realtor).outerjoin(Realtor, Realtor.id == User.assigned_realtor_id)
    if role:
        q = q.filter(User.role == role)
    if status:
        q = q.filter(User.status == status)
    rows = q.order_by(User.created_at.desc()).all()
    return [
        {
            "id": str(u.id), "user_code": u.user_code,
            "email": u.email, "role": u.role, "status": u.status,
            "display_name": u.display_name, "phone": u.phone,
            "created_at": u.created_at, "avatar_url": u.avatar_url,
            "assigned_realtor_id": str(u.assigned_realtor_id) if u.assigned_realtor_id else None,
            "assigned_realtor_name": (r.display_name or r.email) if r else None,
        }
        for u, r in rows
    ]


_CREATEABLE_ROLES = {'buyer', 'owner', 'realtor'}

@router.post("/users", status_code=201)
def create_user(body: CreateAdminUserBody, admin=Depends(require_admin), db: Session = Depends(get_db)):
    if body.role not in _CREATEABLE_ROLES:
        raise HTTPException(status_code=422, detail="Invalid role")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    new_user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        role=body.role,
        status="active",
        email_verified=True,
    )
    db.add(new_user)
    _log(db, "user_created", f"New user created: {body.display_name or body.email}", actor_id=admin.id)
    db.commit()
    db.refresh(new_user)
    return {
        "id": str(new_user.id), "user_code": new_user.user_code,
        "email": new_user.email, "role": new_user.role, "status": new_user.status,
        "display_name": new_user.display_name, "phone": new_user.phone,
        "created_at": new_user.created_at, "avatar_url": None,
    }


@router.put("/users/{user_id}/suspend", status_code=204)
def suspend_user(user_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if str(target.id) == str(user.id):
        raise HTTPException(status_code=400, detail="You cannot suspend your own account")
    target.status = "suspended"
    db.commit()


@router.put("/users/{user_id}/unsuspend", status_code=204)
def unsuspend_user(user_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    target.status = "active"
    db.commit()


_CHANGEABLE_ROLES = {"buyer", "owner", "realtor"}

class ChangeRoleBody(BaseModel):
    role: str

class AssignRealtorBody(BaseModel):
    realtor_id: Optional[str] = None

@router.put("/users/{user_id}/assign-realtor", status_code=204)
def assign_realtor_to_owner(user_id: str, body: AssignRealtorBody, admin=Depends(require_admin), db: Session = Depends(get_db)):
    target = db.query(User).filter(User.id == UUID(user_id)).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.role not in ("owner", "admin"):
        raise HTTPException(status_code=400, detail="Can only assign an agent to an owner or admin account")
    if body.realtor_id:
        realtor = find_eligible_agent(db, body.realtor_id)
        if not realtor:
            raise HTTPException(status_code=404, detail="Active verified agent not found")
        target.assigned_realtor_id = realtor.id
        _log(db, "realtor_assigned", f"Realtor {realtor.display_name or realtor.email} assigned to owner {target.display_name or target.email}", actor_id=admin.id)
    else:
        target.assigned_realtor_id = None
        _log(db, "realtor_unassigned", f"Realtor unassigned from owner {target.display_name or target.email}", actor_id=admin.id)
    db.commit()


@router.put("/users/{user_id}/role", status_code=204)
def change_user_role(user_id: str, body: ChangeRoleBody, admin=Depends(require_admin), db: Session = Depends(get_db)):
    if body.role not in _CHANGEABLE_ROLES:
        raise HTTPException(status_code=422, detail="Role must be buyer, owner, or realtor")
    target = db.query(User).filter(User.id == UUID(user_id)).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if str(target.id) == str(admin.id):
        raise HTTPException(status_code=400, detail="You cannot change your own role")
    if target.role == "admin":
        raise HTTPException(status_code=403, detail="Cannot change the role of an admin account")
    old_role = target.role
    target.role = body.role
    _log(db, "role_changed", f"User role changed: {target.email} {old_role} -> {body.role}", actor_id=admin.id)
    db.commit()


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get("/stats")
def get_admin_stats(user=Depends(require_admin), db: Session = Depends(get_db)):
    active_listings   = db.query(func.count(Listing.id)).filter(Listing.status == "active").scalar() or 0
    pending_listings  = db.query(func.count(Listing.id)).filter(Listing.status == "pending_approval").scalar() or 0
    archived_listings = db.query(func.count(Listing.id)).filter(Listing.status == "archived").scalar() or 0
    rejected_listings = db.query(func.count(Listing.id)).filter(Listing.status == "rejected").scalar() or 0
    total_users       = db.query(func.count(User.id)).scalar() or 0
    return {
        "active_listings": active_listings,
        "pending_listings": pending_listings,
        "archived_listings": archived_listings,
        "rejected_listings": rejected_listings,
        "total_users": total_users,
    }


# ── Activity Log ──────────────────────────────────────────────────────────────

# ── Deal Requests ─────────────────────────────────────────────────────────────

@router.get("/deal-requests", response_model=List[DealRequestResponse])
def list_deal_requests(status: Optional[str] = Query(None), user=Depends(require_admin), db: Session = Depends(get_db)):
    Requester = aliased(User)
    Reviewer  = aliased(User)
    q = (
        db.query(DealRequest, Listing, Requester, Reviewer)
        .join(Listing,   Listing.id   == DealRequest.listing_id)
        .join(Requester, Requester.id == DealRequest.requested_by)
        .outerjoin(Reviewer, Reviewer.id == DealRequest.reviewed_by)
    )
    if status:
        q = q.filter(DealRequest.status == status)
    rows = q.order_by(DealRequest.created_at.desc()).all()
    return [
        DealRequestResponse(
            id=req.id,
            listing_id=req.listing_id,
            listing_title=listing.title,
            listing_location=listing.location,
            listing_thumbnail=listing.images[0] if listing.images else None,
            requested_by_name=requester.display_name,
            requested_by_email=requester.email,
            discount_value=float(req.discount_value),
            discount_type=req.discount_type,
            message=req.message,
            status=req.status,
            rejection_reason=req.rejection_reason,
            reviewed_by_name=reviewer.display_name or reviewer.email if reviewer else None,
            reviewed_at=req.reviewed_at,
            created_at=req.created_at,
        )
        for req, listing, requester, reviewer in rows
    ]


@router.post("/deal-requests/{req_id}/approve", status_code=204)
def approve_deal_request(req_id: UUID, user=Depends(require_admin), db: Session = Depends(get_db)):
    req = db.query(DealRequest).filter(DealRequest.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Deal request not found")
    if req.status != "pending":
        raise HTTPException(status_code=409, detail="Request already reviewed")

    listing = db.query(Listing).filter(Listing.id == req.listing_id).first()
    if not listing or listing.status != "active":
        raise HTTPException(status_code=400, detail="Listing is no longer active")

    listing.is_deal = True
    listing.deal_discount_value = req.discount_value
    listing.deal_discount_type = req.discount_type

    req.status = "approved"
    req.reviewed_by = user.id
    req.reviewed_at = datetime.now(timezone.utc)

    discount_label = f"−{req.discount_value}%" if req.discount_type == "pct" else f"−${req.discount_value:,.0f}"
    _log(db, "deal_approved", f"Deal of the Week set: {listing.title} ({discount_label})", actor_id=user.id)
    db.commit()


@router.post("/deal-requests/{req_id}/reject", status_code=204)
def reject_deal_request(req_id: UUID, body: DealRequestRejectBody, user=Depends(require_admin), db: Session = Depends(get_db)):
    req = db.query(DealRequest).filter(DealRequest.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Deal request not found")
    if req.status != "pending":
        raise HTTPException(status_code=409, detail="Request already reviewed")

    req.status = "rejected"
    req.rejection_reason = body.reason
    req.reviewed_by = user.id
    req.reviewed_at = datetime.now(timezone.utc)

    listing = db.query(Listing).filter(Listing.id == req.listing_id).first()
    _log(db, "deal_rejected", f"Deal request rejected: {listing.title if listing else req.listing_id}", actor_id=user.id)
    db.commit()


@router.post("/listings/{listing_id}/clear-deal", status_code=204)
def clear_listing_deal(listing_id: UUID, user=Depends(require_admin), db: Session = Depends(get_db)):
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    listing.is_deal = False
    listing.deal_discount_value = None
    listing.deal_discount_type = 'pct'
    _log(db, "deal_cleared", f"Deal of the Week cleared: {listing.title}", actor_id=user.id)
    db.commit()


class SetDealBody(BaseModel):
    discount_value: Optional[float] = None
    discount_type: str = 'pct'


@router.post("/listings/{listing_id}/set-deal", status_code=204)
def set_listing_deal(listing_id: UUID, body: SetDealBody, user=Depends(require_admin), db: Session = Depends(get_db)):
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if listing.status != "active":
        raise HTTPException(status_code=400, detail="Listing must be active to set as Deal of the Week")
    if listing.is_deal:
        raise HTTPException(status_code=409, detail="Listing is already a Deal of the Week")
    listing.is_deal = True
    listing.deal_discount_value = body.discount_value
    listing.deal_discount_type = body.discount_type or 'pct'
    if body.discount_value:
        discount_label = f"−{body.discount_value}%" if body.discount_type == "pct" else f"−${body.discount_value:,.0f}"
    else:
        discount_label = "no discount"
    _log(db, "deal_approved", f"Deal of the Week set: {listing.title} ({discount_label})", actor_id=user.id)
    db.commit()


@router.get("/settings")
def get_settings(user=Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(SiteSettings).filter(SiteSettings.id == 1).first()
    data = row.data if row else {}
    return {"notify_email": data.get("notify_email", ""), "updated_at": row.updated_at if row else None}


class PlatformSettingsBody(BaseModel):
    notify_email: Optional[str] = None

    @field_validator("notify_email")
    @classmethod
    def validate_notify_email(cls, value):
        if value is None:
            return value
        return ", ".join(parse_notification_recipients(value))


@router.put("/settings", status_code=204)
def update_settings(body: PlatformSettingsBody, user=Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(SiteSettings).filter(SiteSettings.id == 1).first()
    if row is None:
        row = SiteSettings(id=1, data={})
        db.add(row)
    new_data = dict(row.data or {})
    if body.notify_email is not None:
        new_data["notify_email"] = body.notify_email.strip()
    row.data = new_data
    db.commit()


@router.get("/activity-log")
def get_activity_log(limit: int = Query(20, le=50), user=Depends(require_admin), db: Session = Depends(get_db)):
    entries = (
        db.query(ActivityLog)
        .order_by(ActivityLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(e.id),
            "event_type": e.event_type,
            "description": e.description,
            "created_at": e.created_at,
        }
        for e in entries
    ]


@router.get("/bookings", response_model=List[BookingResponse])
def get_all_bookings(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin-only: all bookings across all listings, newest request first.

    Includes guest requests that only exist as a booking Lead (a logged-out visitor
    who didn't pay through PayPal has no Booking row), so the totals match what
    owners see in their own Guest Bookings queue.
    """
    Owner = aliased(User)
    rows = (
        db.query(Booking, Listing, User, Owner)
        .join(Listing, Listing.id == Booking.listing_id)
        .outerjoin(User, User.id == Booking.buyer_id)
        .outerjoin(Owner, Owner.id == Listing.owner_id)
        .order_by(Booking.created_at.desc())
        .all()
    )
    results = [
        BookingResponse(
            id=str(b.id),
            listing_id=str(b.listing_id),
            listing_title=listing.title,
            listing_location=listing.location,
            listing_images=listing.images or [],
            check_in=str(b.check_in),
            check_out=str(b.check_out),
            guests=b.guests,
            total_price=float(b.total_price) if b.total_price else None,
            notes=b.notes,
            status=b.status,
            created_at=b.created_at,
            guest_name=guest.display_name if guest else b.guest_name,
            guest_email=guest.email if guest else b.guest_email,
            guest_phone=guest.phone if guest else None,
            ghl_contact_url=None,
            payment_status=b.payment_status,
            payout_status=b.payout_status,
            booked_price_per_day=float(b.booked_price_per_day) if b.booked_price_per_day else None,
            platform_fee=float(b.platform_fee) if b.platform_fee else None,
            payout_amount=float(b.payout_amount) if b.payout_amount else None,
            owner_name=owner.display_name if owner else None,
        )
        for b, listing, guest, owner in rows
    ]

    # Lead-only guest requests. Same rules as the owner endpoint: skip leads from logged-in
    # users (they have a Booking row) and leads already backed by a Booking row via lead_id.
    linked_lead_ids = db.query(Booking.lead_id).filter(Booking.lead_id.isnot(None))
    lead_rows = (
        db.query(Lead, Listing, Owner)
        .join(Listing, Listing.id == Lead.property_id)
        .outerjoin(Owner, Owner.id == Listing.owner_id)
        .filter(
            Lead.type == "booking",
            Lead.from_user_id.is_(None),
            ~Lead.id.in_(linked_lead_ids),
        )
        .all()
    )
    for lead, listing, owner in lead_rows:
        resp = _lead_to_booking_response(lead, listing)
        if resp:
            resp.owner_name = owner.display_name if owner else None
            results.append(resp)

    results.sort(key=lambda r: r.created_at, reverse=True)
    return results

