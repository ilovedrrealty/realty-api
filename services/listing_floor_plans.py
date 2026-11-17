"""Version the exact plan bytes without exposing draft or superseded files."""

from datetime import datetime, timezone
import hashlib
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import undefer

from models.listing import Listing
from models.listing_floor_plan import ListingFloorPlan as Plan


def _unavailable():
    return HTTPException(404, "Floor plan unavailable.")


def _changed():
    return HTTPException(409, "The floor plan changed. Refresh before continuing.")


def _listing(db, listing_id, *, lock=False):
    query = db.query(Listing.id, Listing.status).filter(Listing.id == listing_id)
    listing = (query.with_for_update() if lock else query).one_or_none()
    if listing is None:
        raise _unavailable()
    return listing


def _asset(row):
    return dict(id=row.id, content_type=row.content_type, byte_count=row.byte_count,
                sha256=row.sha256, created_at=row.created_at) if row is not None else None


def _slots(db, listing_id):
    return {row.stage: row for row in db.query(Plan).filter(Plan.listing_id == listing_id).populate_existing().all()}


def _state(slots):
    return {stage: _asset(slots.get(stage)) for stage in ("draft", "published")}


def admin_read(db, listing_id):
    _listing(db, listing_id)
    return _state(_slots(db, listing_id))


def stage(db, listing_id, actor_id, replaces, payload, content):
    _listing(db, listing_id, lock=True)
    slots = _slots(db, listing_id)
    prior = slots.get("draft")
    if prior and prior.sha256 == content.sha256 and prior.content_type == content.content_type:
        return _state(slots)
    if replaces != (prior.id if prior else None):
        raise _changed()
    if prior:
        db.delete(prior)
        db.flush()
    plan = Plan(id=uuid4(), listing_id=listing_id, stage="draft", content_type=content.content_type,
                byte_count=content.byte_count, sha256=content.sha256, payload=payload,
                created_at=datetime.now(timezone.utc), staged_by_id=actor_id)
    db.add(plan)
    slots["draft"] = plan
    result = _state(slots)
    db.commit()
    return result


def publish(db, listing_id, actor_id, body):
    listing = _listing(db, listing_id, lock=True)
    if listing.status != "active":
        raise HTTPException(409, "Publish the listing before publishing its floor plan.")
    slots = _slots(db, listing_id)
    prior = slots.get("published")
    if prior and prior.id == body.draft_id:
        return _state(slots)
    draft = slots.get("draft")
    if draft is None or draft.id != body.draft_id or body.expected_published_id != (prior.id if prior else None):
        raise _changed()
    if prior:
        db.delete(prior)
        db.flush()
    draft.stage = "published"
    draft.approved_at = datetime.now(timezone.utc)
    draft.approved_by_id = actor_id
    result = _state({"published": draft})
    db.commit()
    return result


def withdraw(db, listing_id, body):
    _listing(db, listing_id, lock=True)
    slots = _slots(db, listing_id)
    prior = slots.get("published")
    if prior and prior.id != body.published_id:
        raise _changed()
    if prior:
        db.delete(prior)
        del slots["published"]
        db.commit()
    return _state(slots)


def _public_query(db, listing_id):
    return db.query(Plan).join(Listing, Listing.id == Plan.listing_id).filter(
        Plan.listing_id == listing_id, Plan.stage == "published", Listing.status == "active",
    )


def public_read(db, listing_id):
    row = _public_query(db, listing_id).one_or_none()
    if row is None:
        raise _unavailable()
    return _asset(row)


def read_content(db, listing_id, asset_id, *, draft=False):
    query = (db.query(Plan).filter(Plan.listing_id == listing_id, Plan.stage == "draft")
             if draft else _public_query(db, listing_id))
    row = query.options(undefer(Plan.payload)).filter(Plan.id == asset_id).one_or_none()
    if row is None:
        raise _unavailable()
    if len(row.payload) != row.byte_count or hashlib.sha256(row.payload).hexdigest() != row.sha256:
        raise HTTPException(503, "Floor plan integrity could not be verified.")
    return row.payload, row.content_type
