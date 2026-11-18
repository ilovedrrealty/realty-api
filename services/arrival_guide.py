from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select

from models.arrival_guide import ListingArrivalGuide as Guide
from models.booking import Booking
from models.listing import Listing
from schemas.arrival_guide import GuestArrivalGuide, OwnerArrivalGuide
from services.service_visit import DR_TIMEZONE


def utc_now():
    return datetime.now(timezone.utc)


def stay_window(check_in, check_out):
    try:
        start = date.fromisoformat(check_in)
        end = date.fromisoformat(check_out)
        if start.isoformat() != check_in or end.isoformat() != check_out or end <= start:
            return None
        opens = datetime.combine(start - timedelta(days=2), time.min, DR_TIMEZONE)
        closes = datetime.combine(end + timedelta(days=1), time.min, DR_TIMEZONE)
        return opens.astimezone(timezone.utc), closes.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _owner_scope(user_id, listing_id):
    return (Listing.id == listing_id, Listing.owner_id == user_id, Listing.transaction == "rent")


def read_owner_guide(db, user_id, listing_id):
    row = db.execute(
        select(Guide.owner_id, Guide.version, Guide.content)
        .select_from(Listing)
        .outerjoin(Guide, Guide.listing_id == Listing.id)
        .where(*_owner_scope(user_id, listing_id))
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "Rental listing not found")
    return OwnerArrivalGuide(version=row.version or 0, content=row.content if row.owner_id == user_id else None)


def update_owner_guide(db, user_id, listing_id, body):
    # A single lock order also covers the missing-row case and listing transfers.
    listing = db.execute(
        select(Listing.id).where(*_owner_scope(user_id, listing_id)).with_for_update()
    ).scalar_one_or_none()
    if listing is None:
        raise HTTPException(404, "Rental listing not found")

    row = db.get(Guide, listing_id)
    previous_version = row.version if row is not None else 0
    if previous_version != body.expected_version:
        raise HTTPException(409, "Arrival guide changed. Refresh before saving.")

    if row is None:
        row = Guide(listing_id=listing_id)
        db.add(row)
    row.owner_id = user_id
    row.version = previous_version + 1
    row.content = body.content.model_dump() if body.content is not None else None
    result = OwnerArrivalGuide(version=row.version, content=body.content)
    db.commit()
    return result


def read_guest_guide(db, user_id, booking_id):
    row = db.execute(
        select(Booking.check_in, Booking.check_out, Guide.content)
        .select_from(Booking)
        .join(Listing, Listing.id == Booking.listing_id)
        .join(Guide, Guide.listing_id == Listing.id)
        .where(Booking.id == booking_id, Booking.buyer_id == user_id, Booking.status == "confirmed",
               Guide.owner_id == Listing.owner_id, Guide.content.is_not(None))
    ).one_or_none()
    window = stay_window(row.check_in, row.check_out) if row is not None else None
    if window is None or not window[0] <= utc_now() < window[1]:
        raise HTTPException(404, "Arrival guide not available")
    return GuestArrivalGuide(content=row.content, available_until=window[1])
