import logging
import re
from datetime import date, datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from typing import List
from uuid import UUID

from config import settings
from database import get_db
from models.booking import Booking
from models.lead import Lead
from models.listing import Listing
from models.user import User
from schemas.booking import BookingCreate, BookingResponse, CreatePaymentAuthRequest, CreatePaymentAuthResponse
from utils.auth import get_current_user, get_optional_user
from utils.permission import require_role
from utils import ghl
from utils.email import (
    send_lead_notification,
    send_payout_request_email,
    send_booking_submitted_email,
    send_booking_confirmed_email,
    send_booking_declined_email,
    send_owner_new_booking_email,
)
from models.site_settings import SiteSettings
import services.paypal as paypal

log = logging.getLogger(__name__)

_DATE_RANGE_RE = re.compile(r"Check-in: (\d{4}-\d{2}-\d{2}), Check-out: (\d{4}-\d{2}-\d{2})")
_GUESTS_RE = re.compile(r"Guests: (\d+)")

router = APIRouter(prefix="/bookings", tags=["bookings"])


def _ghl_url(contact_id: str | None) -> str | None:
    if contact_id and settings.ghl_location_id:
        return f"https://app.gohighlevel.com/v2/location/{settings.ghl_location_id}/contacts/detail/{contact_id}"
    return None


def _notify_email(db: Session) -> str:
    row = db.query(SiteSettings).filter(SiteSettings.id == 1).first()
    if row and row.data and row.data.get("notify_email"):
        return row.data["notify_email"]
    return settings.notify_email


def _lead_to_booking_response(lead: Lead, listing: Listing) -> BookingResponse | None:
    """Convert a guest booking lead (no platform account) to BookingResponse."""
    if not lead.message:
        return None
    m = _DATE_RANGE_RE.search(lead.message)
    if not m:
        return None
    gm = _GUESTS_RE.search(lead.message)
    if lead.status == "closed":
        booking_status = "confirmed"
    elif lead.status == "cancelled":
        booking_status = "cancelled"
    else:
        booking_status = "pending"
    return BookingResponse(
        id=str(lead.id),
        listing_id=str(listing.id),
        listing_title=listing.title,
        listing_location=listing.location,
        listing_images=listing.images or [],
        check_in=m.group(1),
        check_out=m.group(2),
        guests=int(gm.group(1)) if gm else 1,
        total_price=None,
        notes=None,
        status=booking_status,
        created_at=lead.created_at,
        guest_name=lead.name,
        guest_email=lead.email,
        guest_phone=lead.phone,
        ghl_contact_url=_ghl_url(lead.ghl_contact_id),
        payment_status="unpaid",
        payout_status=None,
        booked_price_per_day=None,
    )


def _booking_response(
    b: Booking,
    listing: Listing,
    guest_name: str | None = None,
    guest_email: str | None = None,
    guest_phone: str | None = None,
    ghl_contact_url: str | None = None,
    owner_name: str | None = None,
) -> BookingResponse:
    return BookingResponse(
        id=str(b.id),
        listing_id=str(b.listing_id),
        listing_title=listing.title,
        listing_location=listing.location,
        listing_images=listing.images or [],
        check_in=b.check_in,
        check_out=b.check_out,
        guests=b.guests,
        total_price=float(b.total_price) if b.total_price else None,
        notes=b.notes,
        status=b.status,
        created_at=b.created_at,
        guest_name=guest_name,
        guest_email=guest_email or b.guest_email,
        guest_phone=guest_phone,
        ghl_contact_url=ghl_contact_url,
        payment_status=b.payment_status,
        payout_status=b.payout_status,
        booked_price_per_day=float(b.booked_price_per_day) if b.booked_price_per_day else None,
        platform_fee=float(b.platform_fee) if b.platform_fee else None,
        payout_amount=float(b.payout_amount) if b.payout_amount else None,
        owner_name=owner_name,
    )


def _blocked_ranges(
    db: Session,
    listing_id,
    exclude_booking_id=None,
    exclude_lead_id=None,
) -> list[tuple[str, str]]:
    """(check_in, check_out) ISO ranges already committed on a listing.

    A stay is committed once the owner accepts it: a confirmed Booking row, or an
    accepted (status "closed") guest-request Lead that has no Booking row of its own.
    Leads linked to a Booking row are skipped so the Booking's status stays the
    single source of truth (a cancelled booking must free its dates even if the
    lead was later closed from GHL).
    """
    bq = db.query(Booking.check_in, Booking.check_out).filter(
        Booking.listing_id == listing_id, Booking.status == "confirmed"
    )
    if exclude_booking_id:
        bq = bq.filter(Booking.id != exclude_booking_id)
    ranges = [(str(ci), str(co)) for ci, co in bq.all()]

    linked_lead_ids = db.query(Booking.lead_id).filter(Booking.lead_id.isnot(None))
    lq = db.query(Lead).filter(
        Lead.type == "booking",
        Lead.property_id == listing_id,
        Lead.status == "closed",
        ~Lead.id.in_(linked_lead_ids),
    )
    if exclude_lead_id:
        lq = lq.filter(Lead.id != exclude_lead_id)
    for lead in lq.all():
        m = _DATE_RANGE_RE.search(lead.message or "")
        if m:
            ranges.append((m.group(1), m.group(2)))
    return ranges


def _dates_available(db: Session, listing_id, check_in, check_out, **exclude) -> bool:
    """True if [check_in, check_out) overlaps no committed stay.

    Ranges are half-open, so a guest may check in on the day another checks out.
    ISO date strings compare correctly as text.
    """
    ci, co = str(check_in), str(check_out)
    return not any(ci < b_out and co > b_in for b_in, b_out in _blocked_ranges(db, listing_id, **exclude))


def _lock_listing(db: Session, listing_id) -> None:
    """Serialize accept decisions per listing so two concurrent accepts of
    overlapping requests can't both pass the availability check."""
    db.query(Listing).filter(Listing.id == listing_id).with_for_update().first()


DATES_TAKEN_MSG = "Those dates are no longer available for this listing"


@router.get("/unavailable/{listing_id}")
def get_unavailable_dates(listing_id: UUID, db: Session = Depends(get_db)):
    """Public — returns check-in/check-out ranges already committed on a listing."""
    return [{"check_in": ci, "check_out": co} for ci, co in _blocked_ranges(db, listing_id)]


@router.post("/create-payment-auth", response_model=CreatePaymentAuthResponse)
def create_payment_auth(body: CreatePaymentAuthRequest, db: Session = Depends(get_db)):
    """
    Public — creates a PayPal order (intent=AUTHORIZE) for the booking form.
    Called before the guest approves payment so the PayPal JS SDK has an order ID.
    """
    if not settings.paypal_client_id:
        raise HTTPException(status_code=503, detail="Payment processing is not configured")

    listing = db.query(Listing).filter(Listing.id == body.listing_id, Listing.status == "active").first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if listing.transaction != "rent":
        raise HTTPException(status_code=400, detail="Payments are only for rental listings")
    if not listing.price_per_day:
        raise HTTPException(status_code=400, detail="Listing has no nightly rate set")

    nights = (body.check_out - body.check_in).days
    if nights <= 0:
        raise HTTPException(status_code=400, detail="check_out must be after check_in")
    # Don't open a PayPal order for dates that can no longer be booked
    if not _dates_available(db, body.listing_id, body.check_in, body.check_out):
        raise HTTPException(status_code=409, detail=DATES_TAKEN_MSG)

    amount_usd = float(listing.price_per_day) * nights
    result = paypal.create_order(amount_usd, str(body.listing_id))
    return CreatePaymentAuthResponse(paypal_order_id=result["order_id"], amount_usd=amount_usd)


@router.post("", response_model=BookingResponse, status_code=201)
def create_booking(
    body: BookingCreate,
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    listing = db.query(Listing).filter(Listing.id == body.listing_id, Listing.status == "active").first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if listing.transaction != "rent":
        raise HTTPException(status_code=400, detail="Bookings are only for rental listings")
    # Reject before anything is created or any payment is authorized
    if not _dates_available(db, body.listing_id, body.check_in, body.check_out):
        raise HTTPException(status_code=409, detail=DATES_TAKEN_MSG)

    # Resolve contact info — use account data for logged-in users, body fields for guests
    if user:
        guest_name = user.display_name or user.email.split("@")[0]
        guest_email = user.email
        guest_phone = getattr(user, "phone", None)
    else:
        guest_name = (body.name or "").strip() or None
        guest_email = body.email
        guest_phone = body.phone
        if not guest_name or not guest_email:
            raise HTTPException(status_code=422, detail="name and email are required for guest bookings")

    message = f"Check-in: {body.check_in}, Check-out: {body.check_out}, Guests: {body.guests}"
    if body.notes:
        message += f"\nNotes: {body.notes}"

    # Auto-assign to the listing owner (owner-managed) or the submitting realtor (realtor-submitted)
    auto_assigned_id = listing.owner_id or listing.submitted_by or None
    lead = Lead(
        type="booking",
        name=guest_name,
        email=guest_email,
        phone=guest_phone,
        property_id=body.listing_id,
        from_user_id=user.id if user else None,
        message=message,
        status="assigned" if auto_assigned_id else "new",
        assigned_realtor_id=auto_assigned_id,
        assigned_at=datetime.now(timezone.utc) if auto_assigned_id else None,
    )
    db.add(lead)

    nights = (body.check_out - body.check_in).days
    # Use price_per_day so the amount matches what create_payment_auth charged PayPal.
    # For monthly-only bookings (no paypal_order_id) there is no daily rate so total stays None.
    price_per_day = float(listing.price_per_day) if listing.price_per_day else None
    total = (price_per_day * nights) if price_per_day else None

    def _authorize_paypal(order_id: str) -> str:
        """Authorize a PayPal order and return the authorization ID."""
        try:
            auth_result = paypal.authorize_order(order_id)
            auth_id = auth_result["authorization_id"]
            if total is not None:
                reported = auth_result.get("amount_usd", 0)
                currency = auth_result.get("currency", "")
                if currency != "USD" or abs(reported - total) > 0.01:
                    paypal.void_authorization(auth_id)
                    raise HTTPException(
                        status_code=422,
                        detail="Payment amount mismatch — please refresh and try again",
                    )
            return auth_id
        except HTTPException:
            raise
        except Exception:
            log.exception("Failed to authorize PayPal order %s", order_id)
            raise HTTPException(status_code=402, detail="Payment authorization failed — please try again")

    booking = None
    if user:
        duplicate = db.query(Booking).filter(
            Booking.buyer_id == user.id,
            Booking.listing_id == body.listing_id,
            Booking.status != "cancelled",
            Booking.check_in < str(body.check_out),
            Booking.check_out > str(body.check_in),
        ).first()
        if duplicate:
            raise HTTPException(status_code=409, detail="You already have a booking for this listing on overlapping dates")

        paypal_authorization_id = _authorize_paypal(body.paypal_order_id) if body.paypal_order_id and settings.paypal_client_id else None
        booking = Booking(
            listing_id=body.listing_id,
            buyer_id=user.id,
            check_in=str(body.check_in),
            check_out=str(body.check_out),
            guests=body.guests,
            notes=body.notes,
            status="pending",
            total_price=total,
            paypal_order_id=body.paypal_order_id,
            paypal_authorization_id=paypal_authorization_id,
            payment_status="authorized" if paypal_authorization_id else "unpaid",
            booked_price_per_day=price_per_day,
        )
        db.add(booking)
    elif body.paypal_order_id and settings.paypal_client_id:
        # Guest paid via PayPal — create a Booking row so payout flow works
        paypal_authorization_id = _authorize_paypal(body.paypal_order_id)
        booking = Booking(
            listing_id=body.listing_id,
            buyer_id=None,
            guest_name=guest_name,
            guest_email=guest_email,
            check_in=str(body.check_in),
            check_out=str(body.check_out),
            guests=body.guests,
            notes=body.notes,
            status="pending",
            total_price=total,
            paypal_order_id=body.paypal_order_id,
            paypal_authorization_id=paypal_authorization_id,
            payment_status="authorized",
            booked_price_per_day=price_per_day,
        )
        db.add(booking)

    # Lead notification jobs share the original lead/booking transaction. They
    # are not a payment retry and do not change guest/owner booking messages.
    from services import lead_delivery
    property_info = {
        "id": str(listing.id),
        "title": listing.title,
        "price": float(listing.price) if listing.price else None,
        "location": listing.location,
        "bedrooms": listing.bedrooms,
        "bathrooms": float(listing.bathrooms) if listing.bathrooms else None,
        "listing_type": listing.type,
    }
    queued_lead_delivery = lead_delivery.enabled()
    try:
        db.flush()
        if booking:
            # The booking, its lead link and notification jobs are one intake
            # transaction. A worker must never observe an unlinked booking.
            booking.lead_id = lead.id
        if queued_lead_delivery:
            lead_delivery.enqueue_lead_delivery(db, lead, property_info)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        # Driver repr/statement parameters may include the full private outbox.
        raise HTTPException(503, "Booking could not be confirmed. Refresh before trying again.",
                            headers={"Cache-Control": "no-store"}) from None
    db.refresh(lead)
    if booking:
        db.refresh(booking)

    if not queued_lead_delivery:
        ghl.create_contact(lead, property_info, db)
        try:
            send_lead_notification(lead, property_info, notify_email=_notify_email(db))
        except Exception:
            pass
    try:
        send_booking_submitted_email(
            to_email=guest_email,
            guest_name=guest_name or "Guest",
            listing_title=listing.title or "listing",
            check_in=str(body.check_in),
            check_out=str(body.check_out),
            guests=body.guests,
            total_price=float(booking.total_price) if (booking and booking.total_price) else None,
            is_paypal=bool(booking and booking.paypal_authorization_id),
        )
    except Exception:
        pass
    try:
        # Notify owner (if they have a platform account)
        if listing.owner_id:
            owner_user = db.query(User).filter(User.id == listing.owner_id).first()
            if owner_user and owner_user.email:
                send_owner_new_booking_email(
                    to_email=owner_user.email,
                    owner_name=owner_user.display_name or owner_user.email,
                    guest_name=guest_name or "Guest",
                    listing_title=listing.title or "listing",
                    check_in=str(body.check_in),
                    check_out=str(body.check_out),
                    guests=body.guests,
                )
        # Notify realtor/submitter if they are a different person from the owner
        if listing.submitted_by and listing.submitted_by != listing.owner_id:
            submitter = db.query(User).filter(User.id == listing.submitted_by).first()
            if submitter and submitter.email:
                send_owner_new_booking_email(
                    to_email=submitter.email,
                    owner_name=submitter.display_name or submitter.email,
                    guest_name=guest_name or "Guest",
                    listing_title=listing.title or "listing",
                    check_in=str(body.check_in),
                    check_out=str(body.check_out),
                    guests=body.guests,
                )
    except Exception:
        pass

    if booking:
        return _booking_response(booking, listing)

    return BookingResponse(
        id=str(lead.id),
        listing_id=str(listing.id),
        listing_title=listing.title,
        listing_location=listing.location,
        listing_images=listing.images or [],
        check_in=str(body.check_in),
        check_out=str(body.check_out),
        guests=body.guests,
        total_price=None,
        notes=body.notes,
        status="pending",
        created_at=lead.created_at,
        payment_status="unpaid",
    )


@router.get("/mine", response_model=List[BookingResponse])
def get_my_bookings(user=Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(Booking, Listing)
        .join(Listing, Listing.id == Booking.listing_id)
        .filter(Booking.buyer_id == user.id)
        .order_by(Booking.created_at.desc())
        .all()
    )
    return [_booking_response(b, listing) for b, listing in rows]


@router.get("/for-owner", response_model=List[BookingResponse])
def get_bookings_for_owner(user=Depends(get_current_user), db: Session = Depends(get_db)):
    # All bookings (logged-in users and PayPal guests) — outerjoin so null buyer_id / lead_id works
    rows = (
        db.query(Booking, Listing, User, Lead)
        .join(Listing, Listing.id == Booking.listing_id)
        .outerjoin(User, User.id == Booking.buyer_id)
        .outerjoin(Lead, Lead.id == Booking.lead_id)
        .filter(or_(Listing.submitted_by == user.id, Listing.owner_id == user.id))
        .order_by(Booking.check_in.asc())
        .all()
    )
    results = [
        _booking_response(
            b, listing,
            guest_name=(guest.display_name if guest else b.guest_name),
            guest_email=(guest.email if guest else b.guest_email),
            guest_phone=(guest.phone if guest else None),
            ghl_contact_url=_ghl_url(booking_lead.ghl_contact_id) if booking_lead else None,
        )
        for b, listing, guest, booking_lead in rows
    ]

    # Guest booking leads (no platform account, no PayPal — Lead-only records)
    # Exclude leads that already have a backing Booking row (lead_id link)
    owner_listings = (
        db.query(Listing)
        .filter(or_(Listing.submitted_by == user.id, Listing.owner_id == user.id))
        .all()
    )
    listing_map = {str(l.id): l for l in owner_listings}
    booked_lead_ids = {
        str(b.lead_id) for b, _, _, _ in rows if b.lead_id is not None
    }
    guest_leads = (
        db.query(Lead)
        .filter(
            Lead.type == "booking",
            Lead.from_user_id == None,
            Lead.property_id.in_(listing_map.keys()),
        )
        .order_by(Lead.created_at.asc())
        .all()
    )
    for lead in guest_leads:
        if str(lead.id) in booked_lead_ids:
            continue  # already represented as a Booking row
        resp = _lead_to_booking_response(lead, listing_map[str(lead.property_id)])
        if resp:
            results.append(resp)

    results.sort(key=lambda r: r.check_in)
    return results


@router.put("/{booking_id}/cancel", status_code=204)
def cancel_booking(booking_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    booking = db.query(Booking).filter(Booking.id == booking_id, Booking.buyer_id == user.id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status not in ("pending", "confirmed"):
        raise HTTPException(status_code=400, detail="Only pending or confirmed bookings can be cancelled")

    # Void authorization if payment was authorized but not yet captured
    if booking.payment_status == "authorized" and booking.paypal_authorization_id and settings.paypal_client_id:
        try:
            paypal.void_authorization(booking.paypal_authorization_id)
            booking.payment_status = "voided"
        except Exception:
            log.exception("Failed to void authorization %s for booking %s", booking.paypal_authorization_id, booking_id)

    # Captured payments are non-refundable per policy.
    # Guest cancellations on confirmed bookings are flagged for admin review —
    # refunds are only issued in exceptional cases (natural disaster, travel ban,
    # medical emergency) and must be approved manually by I Love DR Realty staff.
    elif booking.payment_status == "captured":
        booking.needs_admin_review = True

    booking.status = "cancelled"
    db.commit()


@router.put("/{booking_id}/accept", status_code=204)
def accept_booking(booking_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    row = (
        db.query(Booking, Listing)
        .join(Listing, Listing.id == Booking.listing_id)
        .filter(
            Booking.id == booking_id,
            or_(Listing.submitted_by == user.id, Listing.owner_id == user.id),
        )
        .first()
    )
    if row:
        b, listing = row
        if b.status != "pending":
            raise HTTPException(status_code=400, detail="Only pending bookings can be accepted")
        # Lock + re-check before capturing payment: another request may have been accepted since this one was made
        _lock_listing(db, b.listing_id)
        if not _dates_available(db, b.listing_id, b.check_in, b.check_out, exclude_booking_id=b.id):
            raise HTTPException(status_code=409, detail=DATES_TAKEN_MSG)
        if b.payment_status == "authorized" and b.paypal_authorization_id and settings.paypal_client_id:
            try:
                result = paypal.capture_authorization(b.paypal_authorization_id)
                b.paypal_capture_id = result["capture_id"]
                b.payment_status = "captured"
            except Exception:
                log.exception("Failed to capture authorization %s for booking %s", b.paypal_authorization_id, booking_id)
                raise HTTPException(status_code=502, detail="Payment capture failed — please try again")
        b.status = "confirmed"
        db.commit()
        try:
            guest_user = db.query(User).filter(User.id == b.buyer_id).first() if b.buyer_id else None
            guest_email_addr = (guest_user.email if guest_user else None) or b.guest_email
            guest_display = (guest_user.display_name if guest_user else None) or b.guest_name
            if guest_email_addr:
                send_booking_confirmed_email(
                    to_email=guest_email_addr,
                    guest_name=guest_display or "Guest",
                    listing_title=listing.title or "listing",
                    check_in=str(b.check_in),
                    check_out=str(b.check_out),
                    total_price=float(b.total_price) if b.total_price else None,
                )
        except Exception:
            pass
        return

    # Guest booking lead (no platform account — ID is a Lead UUID)
    lead = db.query(Lead).filter(Lead.id == booking_id, Lead.type == "booking").first()
    if not lead or not lead.property_id:
        raise HTTPException(status_code=404, detail="Booking not found")
    listing = db.query(Listing).filter(
        Listing.id == lead.property_id,
        or_(Listing.submitted_by == user.id, Listing.owner_id == user.id),
    ).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Booking not found")
    if lead.status == "closed":
        raise HTTPException(status_code=400, detail="Only pending bookings can be accepted")
    m = _DATE_RANGE_RE.search(lead.message or "")
    if m:
        _lock_listing(db, listing.id)
        if not _dates_available(db, listing.id, m.group(1), m.group(2), exclude_lead_id=lead.id):
            raise HTTPException(status_code=409, detail=DATES_TAKEN_MSG)
    lead.status = "closed"
    db.commit()
    try:
        m = _DATE_RANGE_RE.search(lead.message or "")
        send_booking_confirmed_email(
            to_email=lead.email,
            guest_name=lead.name or "Guest",
            listing_title=listing.title or "listing",
            check_in=m.group(1) if m else "",
            check_out=m.group(2) if m else "",
            total_price=None,
        )
    except Exception:
        pass


@router.put("/{booking_id}/decline", status_code=204)
def decline_booking(booking_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    row = (
        db.query(Booking, Listing)
        .join(Listing, Listing.id == Booking.listing_id)
        .filter(
            Booking.id == booking_id,
            or_(Listing.submitted_by == user.id, Listing.owner_id == user.id),
        )
        .first()
    )
    if row:
        b, listing = row
        if b.status != "pending":
            raise HTTPException(status_code=400, detail="Only pending bookings can be declined")
        if b.payment_status == "authorized" and b.paypal_authorization_id and settings.paypal_client_id:
            try:
                paypal.void_authorization(b.paypal_authorization_id)
                b.payment_status = "voided"
            except Exception:
                log.exception("Failed to void authorization %s for booking %s", b.paypal_authorization_id, booking_id)
        b.status = "cancelled"
        db.commit()
        try:
            guest_user = db.query(User).filter(User.id == b.buyer_id).first() if b.buyer_id else None
            guest_email_addr = (guest_user.email if guest_user else None) or b.guest_email
            guest_display = (guest_user.display_name if guest_user else None) or b.guest_name
            if guest_email_addr:
                send_booking_declined_email(
                    to_email=guest_email_addr,
                    guest_name=guest_display or "Guest",
                    listing_title=listing.title or "listing",
                    check_in=str(b.check_in),
                    check_out=str(b.check_out),
                )
        except Exception:
            pass
        return

    # Guest booking lead (no platform account — ID is a Lead UUID)
    lead = db.query(Lead).filter(Lead.id == booking_id, Lead.type == "booking").first()
    if not lead or not lead.property_id:
        raise HTTPException(status_code=404, detail="Booking not found")
    listing = db.query(Listing).filter(
        Listing.id == lead.property_id,
        or_(Listing.submitted_by == user.id, Listing.owner_id == user.id),
    ).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Booking not found")
    if lead.status == "cancelled":
        raise HTTPException(status_code=400, detail="Only pending bookings can be declined")
    lead.status = "cancelled"
    db.commit()
    try:
        m = _DATE_RANGE_RE.search(lead.message or "")
        send_booking_declined_email(
            to_email=lead.email,
            guest_name=lead.name or "Guest",
            listing_title=listing.title or "listing",
            check_in=m.group(1) if m else "",
            check_out=m.group(2) if m else "",
        )
    except Exception:
        pass


@router.post("/{booking_id}/release-payout", status_code=204)
def release_payout(
    booking_id: UUID,
    user=Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Admin-only: manually trigger a payout for a confirmed booking (retry failed payouts)."""
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.payment_status != "captured":
        raise HTTPException(status_code=400, detail="Booking has no captured payment to pay out")
    if booking.payout_status == "paid":
        raise HTTPException(status_code=400, detail="Payout already completed")

    listing = db.query(Listing).filter(Listing.id == booking.listing_id).first()
    payout_email = (listing.owner_paypal_email or None) if listing else None
    if not payout_email:
        payout_recipient_id = (listing.owner_id or listing.submitted_by) if listing else None
        recipient = db.query(User).filter(User.id == payout_recipient_id).first() if payout_recipient_id else None
        payout_email = recipient.paypal_email if recipient else None
    if not payout_email:
        raise HTTPException(status_code=400, detail="Recipient has no PayPal email configured")

    total = float(booking.total_price) if booking.total_price else 0
    fee = round(total * settings.platform_fee_pct, 2)
    payout = round(total - fee, 2)
    try:
        paypal.create_payout(payout_email, payout, str(booking_id))
        booking.platform_fee = fee
        booking.payout_amount = payout
        booking.payout_status = "paid"
        db.commit()
    except Exception:
        log.exception("Manual payout failed for booking %s", booking_id)
        booking.payout_status = "failed"
        db.commit()
        raise HTTPException(status_code=502, detail="Payout failed — check PayPal dashboard")


@router.post("/{booking_id}/request-payout", status_code=204)
def request_payout(
    booking_id: UUID,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Owner/realtor: notify admin that a payout needs manual review."""
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    listing = db.query(Listing).filter(Listing.id == booking.listing_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    # Only the owner or submitter of the listing can request a payout
    if str(listing.owner_id) != str(user.id) and str(listing.submitted_by) != str(user.id):
        raise HTTPException(status_code=403, detail="Not authorised")

    if booking.payout_status != "failed":
        raise HTTPException(status_code=400, detail="Payout is not in a failed state")

    notify = _notify_email(db)
    if not notify:
        raise HTTPException(status_code=503, detail="Admin notification email is not configured")

    total = float(booking.total_price) if booking.total_price else 0
    payout_amt = float(booking.payout_amount) if booking.payout_amount else round(total * (1 - settings.platform_fee_pct), 2)

    send_payout_request_email(
        to_email=notify,
        owner_name=user.display_name or user.email,
        listing_title=listing.title or "Untitled listing",
        check_in=str(booking.check_in),
        check_out=str(booking.check_out),
        total_price=total,
        payout_amount=payout_amt,
    )
