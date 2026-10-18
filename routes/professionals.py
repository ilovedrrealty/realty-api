"""Moderated public business profiles alongside existing authenticated roles."""
from copy import deepcopy
from datetime import datetime, timezone
import logging
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.formparsers import MultiPartException
from starlette.exceptions import HTTPException as StarletteHTTPException

from database import get_db
from models.professional_profile import ProfessionalProfile, ProfessionalProfileRevision
from models.user import User
from schemas.professionals import (ProfessionalDraft, SaveProfessionalBody, VersionBody, ReviewBody,
    OwnedProfessional, PublicProfessional, PublicProfessionalPage, AdminProfessionalDetail,
    AdminProfessionalPage, ReviewStatus, ListingStatus)
from utils.auth import get_current_user
from utils.permission import require_admin
from utils.professional_options import CATEGORIES, AREAS, LANGUAGES, CATEGORY_IDS, AREA_IDS, LANGUAGE_IDS, matching_areas
from utils.professional_photos import MAX_PHOTO_BYTES, prepare_photo, upload_professional_photo, validate_owned_photo
from utils.limiter import limiter

logger = logging.getLogger(__name__)
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
MULTIPART_LIMIT_DETAIL = "Professional upload exceeds the size limit"
PUBLIC_FIELDS = ("business_name", "contact_name", "category_ids", "service_area_ids", "descriptions", "languages",
                 "public_phone", "public_email", "public_whatsapp", "website_url", "photo_url")


class NoStoreRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                multipart = request.headers.get("content-type", "").startswith("multipart/")
                ceiling = MAX_PHOTO_BYTES + 65536 if multipart else 128 * 1024
                length = request.headers.get("content-length")
                if length and length.isdigit() and int(length) > ceiling:
                    raise HTTPException(413, "Request is too large")
                receive = request.receive
                consumed = 0

                async def bounded_receive():
                    nonlocal consumed
                    message = await receive()
                    consumed += len(message.get("body", b""))
                    if consumed > ceiling:
                        if multipart:
                            # Let Starlette close any multipart spool files before
                            # converting this specific parser error into HTTP413.
                            raise MultiPartException(MULTIPART_LIMIT_DETAIL)
                        raise HTTPException(413, "Request is too large")
                    return message

                request = Request(request.scope, receive=bounded_receive)
                # The bounded professional JSON contract has no streaming consumers.
                if not multipart and len(await request.body()) > ceiling:
                    raise HTTPException(413, "Request is too large")
                response = await original(request)
                response.headers.update(NO_STORE)
                return response
            except StarletteHTTPException as exc:
                status = 413 if exc.detail == MULTIPART_LIMIT_DETAIL else exc.status_code
                return JSONResponse(status_code=status, content={"detail": jsonable_encoder(exc.detail)}, headers={**(exc.headers or {}), **NO_STORE})
            except RequestValidationError as exc:
                return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())}, headers=NO_STORE)
            except Exception:
                logger.exception("Professional profile request failed")
                return JSONResponse(status_code=500, content={"detail": "Professional profiles are temporarily unavailable"}, headers=NO_STORE)
        return handler


router = APIRouter(tags=["professionals"], route_class=NoStoreRoute)


def _public(row):
    if not row.published_data or not row.published_at:
        return None
    return {"id": row.id, **{key: deepcopy(row.published_data[key]) for key in PUBLIC_FIELDS}, "approved_at": row.published_at}


def _owned(row):
    return {"id": row.id, "version": row.version, "review_status": row.review_status, "listing_status": row.listing_status,
            "draft": deepcopy(row.draft_data), "published_profile": _public(row), "review_note": row.review_note,
            "submitted_at": row.submitted_at, "reviewed_at": row.reviewed_at, "published_at": row.published_at,
            "created_at": row.created_at, "updated_at": row.updated_at}


def _owner(owner):
    return {"id": owner.id, "display_name": owner.display_name, "email": owner.email, "role": owner.role,
            "status": owner.status, "email_verified": owner.email_verified}


def _admin_detail(row, db):
    history = db.query(ProfessionalProfileRevision).filter(ProfessionalProfileRevision.profile_id == row.id).order_by(ProfessionalProfileRevision.version.desc()).all()
    return {**_owned(row), "owner": _owner(db.get(User, row.user_id)), "history": [
        {"version": item.version, "action": item.action, "note": item.note, "actor_id": item.actor_id, "created_at": item.created_at} for item in history]}


def _check_version(row, expected):
    if row.version != expected:
        raise HTTPException(409, "This profile changed. Reload the latest version before saving.")


def _lock_own(db, user, expected):
    row = db.query(ProfessionalProfile).filter(ProfessionalProfile.user_id == user.id).with_for_update().first()
    if row is None:
        raise HTTPException(404, "Professional profile not found")
    _check_version(row, expected)
    return row


def _record(db, row, actor, action, note=None):
    row.version += 1
    row.updated_at = datetime.now(timezone.utc)
    db.add(row)
    db.flush()
    db.add(ProfessionalProfileRevision(profile_id=row.id, version=row.version, action=action, actor_id=actor.id,
        note=note or None, snapshot={"draft": deepcopy(row.draft_data), "published": deepcopy(row.published_data),
                                  "review_status": row.review_status, "listing_status": row.listing_status}))
    db.commit()
    db.refresh(row)


def _complete(draft):
    data = ProfessionalDraft.model_validate(draft)
    if not (data.business_name or data.contact_name) or not data.category_ids or not data.service_area_ids:
        raise HTTPException(422, "Add your public name, service category and service area before submitting")
    if not (data.descriptions.en or data.descriptions.es):
        raise HTTPException(422, "Add an English or Spanish description before submitting")
    if not data.public_contact_consent or not (data.public_phone or data.public_email or data.public_whatsapp):
        raise HTTPException(422, "Choose a public business contact and consent to publishing it")
    return data


def _visible(db):
    return db.query(ProfessionalProfile).join(User, User.id == ProfessionalProfile.user_id).filter(
        ProfessionalProfile.listing_status == "published", ProfessionalProfile.published_data.isnot(None),
        ProfessionalProfile.published_at.isnot(None), User.status == "active", User.email_verified.is_(True),
        ProfessionalProfile.published_data["public_contact_consent"].as_boolean().is_(True))


@router.get("/professionals/options")
def professional_options():
    return {"categories": CATEGORIES, "areas": AREAS, "languages": LANGUAGES}


@router.get("/professionals", response_model=PublicProfessionalPage)
def directory(category: Optional[list[str]] = Query(None), area: Optional[list[str]] = Query(None),
              language: Optional[str] = Query(None), q: Optional[str] = Query(None, max_length=100),
              limit: int = Query(24, ge=1, le=50), offset: int = Query(0, ge=0, le=10000), db: Session = Depends(get_db)):
    if len(category or []) > 8 or len(area or []) > 20 or set(category or []) - CATEGORY_IDS or set(area or []) - AREA_IDS or (language and language not in LANGUAGE_IDS):
        raise HTTPException(422, "Unknown or excessive professional filters")
    query = _visible(db)
    if category:
        query = query.filter(or_(*(ProfessionalProfile.published_data["category_ids"].contains([value]) for value in set(category))))
    if area:
        coverage = set().union(*(matching_areas(value) for value in area))
        query = query.filter(or_(*(ProfessionalProfile.published_data["service_area_ids"].contains([value]) for value in coverage)))
    if language:
        query = query.filter(ProfessionalProfile.published_data["languages"].contains([language]))
    if q and q.strip():
        query = query.filter(or_(*(ProfessionalProfile.published_data[key].astext.icontains(q.strip(), autoescape=True) for key in ("business_name", "contact_name")),
                                ProfessionalProfile.published_data["descriptions"]["en"].astext.icontains(q.strip(), autoescape=True),
                                ProfessionalProfile.published_data["descriptions"]["es"].astext.icontains(q.strip(), autoescape=True)))
    total = query.count()
    rows = query.order_by(func.lower(ProfessionalProfile.published_data["business_name"].astext), ProfessionalProfile.id).offset(offset).limit(limit).all()
    return {"items": [_public(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/professionals/{profile_id}", response_model=PublicProfessional)
def public_profile(profile_id: UUID, db: Session = Depends(get_db)):
    row = _visible(db).filter(ProfessionalProfile.id == profile_id).first()
    if row is None:
        raise HTTPException(404, "Professional profile not found")
    return _public(row)


@router.get("/professional-profile", response_model=OwnedProfessional | None)
def own_profile(user=Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(ProfessionalProfile).filter(ProfessionalProfile.user_id == user.id).first()
    return _owned(row) if row else None


@router.put("/professional-profile", response_model=OwnedProfessional)
@limiter.limit("20/minute")
def save_profile(request: Request, body: SaveProfessionalBody, user=Depends(get_current_user), db: Session = Depends(get_db)):
    validate_owned_photo(body.draft.photo_url, user.id)
    row = db.query(ProfessionalProfile).filter(ProfessionalProfile.user_id == user.id).with_for_update().first()
    if row is None:
        if body.expected_version != 0:
            raise HTTPException(409, "Reload before creating this profile")
        row = ProfessionalProfile(id=uuid4(), user_id=user.id, version=0, review_status="draft", listing_status="hidden")
    else:
        _check_version(row, body.expected_version)
    row.draft_data = body.draft.model_dump(mode="json")
    row.review_status = "draft"
    row.submitted_at = None
    row.review_note = None
    if not body.draft.public_contact_consent and row.listing_status != "suspended":
        row.listing_status = "hidden"
    try:
        _record(db, row, user, "save")
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This profile was created or changed. Reload before saving.")
    return _owned(row)


@router.post("/professional-profile/submit", response_model=OwnedProfessional)
def submit_profile(body: VersionBody, user=Depends(get_current_user), db: Session = Depends(get_db)):
    row = _lock_own(db, user, body.expected_version)
    if row.listing_status == "suspended":
        raise HTTPException(403, "An admin must lift this profile's suspension before you submit")
    _complete(row.draft_data)
    validate_owned_photo(row.draft_data.get("photo_url", ""), user.id)
    row.review_status = "pending"
    row.submitted_at = datetime.now(timezone.utc)
    row.review_note = None
    _record(db, row, user, "submit")
    return _owned(row)


@router.post("/professional-profile/withdraw", response_model=OwnedProfessional)
def withdraw_profile(body: VersionBody, user=Depends(get_current_user), db: Session = Depends(get_db)):
    row = _lock_own(db, user, body.expected_version)
    if row.listing_status != "suspended":
        row.listing_status = "hidden"
    row.review_status = "draft"
    row.submitted_at = None
    _record(db, row, user, "withdraw")
    return _owned(row)


@router.post("/professional-profile/photo")
@limiter.limit("10/minute")
async def upload_photo(request: Request, file: UploadFile = File(...), user=Depends(get_current_user)):
    data = prepare_photo(await file.read(MAX_PHOTO_BYTES + 1))
    try:
        from starlette.concurrency import run_in_threadpool
        url = await run_in_threadpool(upload_professional_photo, data, str(user.id))
    except Exception:
        logger.exception("Professional photo upload failed")
        raise HTTPException(503, "Photo upload is temporarily unavailable")
    validate_owned_photo(url, user.id)
    return {"photo_url": url}


@router.get("/admin/professionals", response_model=AdminProfessionalPage)
def admin_profiles(review_status: Optional[ReviewStatus] = Query(None), listing_status: Optional[ListingStatus] = Query(None),
                   q: Optional[str] = Query(None, max_length=100), limit: int = Query(30, ge=1, le=50), offset: int = Query(0, ge=0, le=10000),
                   admin=Depends(require_admin), db: Session = Depends(get_db)):
    query = db.query(ProfessionalProfile, User).join(User, User.id == ProfessionalProfile.user_id)
    if review_status:
        query = query.filter(ProfessionalProfile.review_status == review_status)
    if listing_status:
        query = query.filter(ProfessionalProfile.listing_status == listing_status)
    if q and q.strip():
        query = query.filter(or_(ProfessionalProfile.draft_data["business_name"].astext.icontains(q.strip(), autoescape=True),
                                ProfessionalProfile.draft_data["contact_name"].astext.icontains(q.strip(), autoescape=True),
                                User.email.icontains(q.strip(), autoescape=True)))
    total = query.count()
    rows = query.order_by(ProfessionalProfile.updated_at.desc(), ProfessionalProfile.id).offset(offset).limit(limit).all()
    return {"items": [{"id": row.id, "version": row.version, "review_status": row.review_status, "listing_status": row.listing_status,
                       **{key: row.draft_data[key] for key in ("business_name", "contact_name", "category_ids", "service_area_ids")},
                       "owner": _owner(owner), "submitted_at": row.submitted_at, "reviewed_at": row.reviewed_at,
                       "published_at": row.published_at, "updated_at": row.updated_at} for row, owner in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/admin/professionals/{profile_id}", response_model=AdminProfessionalDetail)
def admin_profile(profile_id: UUID, admin=Depends(require_admin), db: Session = Depends(get_db)):
    row = db.get(ProfessionalProfile, profile_id)
    if row is None:
        raise HTTPException(404, "Professional profile not found")
    return _admin_detail(row, db)


def _moderate(profile_id, body, action, admin, db):
    row = db.query(ProfessionalProfile).filter(ProfessionalProfile.id == profile_id).with_for_update().first()
    if row is None:
        raise HTTPException(404, "Professional profile not found")
    _check_version(row, body.expected_version)
    note = body.note.strip()
    if action in ("reject", "suspend") and not note:
        raise HTTPException(422, "Add a private review note")
    if action in ("approve", "reject") and row.review_status != "pending":
        raise HTTPException(409, "Only a pending submission can be reviewed")
    if action == "approve":
        data = _complete(row.draft_data)
        owner = db.get(User, row.user_id)
        if owner.status != "active" or not owner.email_verified:
            raise HTTPException(409, "Owner must be active and verified before publication")
        validate_owned_photo(data.photo_url, owner.id)
        row.published_data = deepcopy(data.model_dump(mode="json"))
        row.published_at = datetime.now(timezone.utc)
        row.review_status = "approved"
        if row.listing_status != "suspended":
            row.listing_status = "published"
    elif action == "reject":
        row.review_status = "rejected"
    elif action == "suspend":
        row.listing_status = "suspended"
    elif action == "reinstate":
        if row.listing_status != "suspended":
            raise HTTPException(409, "This profile is not suspended")
        row.listing_status = "hidden"
        row.review_status = "draft"
        row.submitted_at = None
    row.review_note = note or None
    row.reviewed_by = admin.id
    row.reviewed_at = datetime.now(timezone.utc)
    _record(db, row, admin, action, note)
    return _admin_detail(row, db)


@router.post("/admin/professionals/{profile_id}/approve", response_model=AdminProfessionalDetail)
def approve(profile_id: UUID, body: ReviewBody, admin=Depends(require_admin), db: Session = Depends(get_db)):
    return _moderate(profile_id, body, "approve", admin, db)


@router.post("/admin/professionals/{profile_id}/reject", response_model=AdminProfessionalDetail)
def reject(profile_id: UUID, body: ReviewBody, admin=Depends(require_admin), db: Session = Depends(get_db)):
    return _moderate(profile_id, body, "reject", admin, db)


@router.post("/admin/professionals/{profile_id}/suspend", response_model=AdminProfessionalDetail)
def suspend(profile_id: UUID, body: ReviewBody, admin=Depends(require_admin), db: Session = Depends(get_db)):
    return _moderate(profile_id, body, "suspend", admin, db)


@router.post("/admin/professionals/{profile_id}/reinstate", response_model=AdminProfessionalDetail)
def reinstate(profile_id: UUID, body: ReviewBody, admin=Depends(require_admin), db: Session = Depends(get_db)):
    return _moderate(profile_id, body, "reinstate", admin, db)
