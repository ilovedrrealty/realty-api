"""Private seller comparables, manual reviews, and staff follow-up."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import or_

from models.lead import Lead
from models.listing import Listing
from models.seller_valuation import SellerValuationComparable, SellerValuationFollowUp, SellerValuationIntake, SellerValuationWorkspace
from models.user import User
from schemas.seller_valuation import AskingCandidate, Comparable, FollowUp, Report, ReportFields, SellerValuationSubject, SoldReview, ValuationDraftSubject, ValuationWorkspace
from utils.listing_search import escape_like, normalized_location, normalized_location_column


STAFF_ROLES = ("admin", "realtor")
DOMINICAN_TIME = timezone(timedelta(hours=-4))
MAX_COMPARABLES = 100
MAX_FOLLOW_UPS = 100
SQFT_TO_M2 = Decimal("0.09290304")


def scoped_seller(db, actor, lead_id, *, write=False):
    query = db.query(Lead).filter(Lead.id == lead_id, Lead.type == "seller_interest")
    if actor.role == "realtor":
        query = query.filter(Lead.assigned_realtor_id == actor.id)
    elif actor.role != "admin":
        raise HTTPException(404, "Seller valuation unavailable")
    # Keep authorization and this operation ordered against the existing assignment update.
    lead = query.with_for_update(of=Lead, read=not write).populate_existing().one_or_none()
    role = db.query(User.role).filter(
        User.id == actor.id, User.status == "active", User.email_verified.is_(True),
    ).scalar()
    if lead is None or role not in STAFF_ROLES or (role == "realtor" and lead.assigned_realtor_id != actor.id):
        raise HTTPException(404, "Seller valuation unavailable")
    return lead


def _intake(db, lead_id):
    intake = db.get(SellerValuationIntake, lead_id)
    return SellerValuationSubject.model_validate(intake.subject) if intake is not None else None


def _comparables(db, lead_id):
    return db.query(SellerValuationComparable).filter(SellerValuationComparable.lead_id == lead_id)


def _follow_ups(db, lead_id):
    return db.query(SellerValuationFollowUp).filter(SellerValuationFollowUp.lead_id == lead_id)


def _projection(db, lead_id, state):
    intake = _intake(db, lead_id)
    if state is None:
        return ValuationWorkspace(
            lead_id=lead_id, version=0, started=False, intake_subject=intake,
            subject=ValuationDraftSubject.model_validate(intake.model_dump() if intake else {}),
            comparables=[], review=None, follow_ups=[],
        )
    report = None
    if state.review is not None:
        status = "current" if state.review_evidence_version == state.evidence_version else "expired"
        report = Report.model_validate({**state.review, "status": status})
    comparables = _comparables(db, lead_id).order_by(SellerValuationComparable.captured_at, SellerValuationComparable.id)
    follow_ups = _follow_ups(db, lead_id).order_by(SellerValuationFollowUp.follow_up_on, SellerValuationFollowUp.id)
    return ValuationWorkspace(
        lead_id=lead_id, version=state.version, started=True, intake_subject=intake,
        subject=ValuationDraftSubject.model_validate(state.subject),
        comparables=[Comparable.model_validate(row) for row in comparables], review=report,
        follow_ups=[FollowUp.model_validate(row) for row in follow_ups],
    )


def read(db, lead_id):
    return _projection(db, lead_id, db.get(SellerValuationWorkspace, lead_id))


def _editable(db, lead_id, version, *, can_start=False):
    state = db.get(SellerValuationWorkspace, lead_id)
    if version != (state.version if state else 0):
        raise HTTPException(409, "Valuation changed. Reload and try again.")
    if state is None and not can_start:
        raise HTTPException(409, "Start a valuation draft first.")
    return state


def _save(db, state, *, evidence_changed=False):
    state.version += 1
    if evidence_changed:
        state.evidence_version += 1
    db.flush()
    result = _projection(db, state.lead_id, state)
    db.commit()
    return result


def save_draft(db, lead_id, body):
    state = _editable(db, lead_id, body.expected_version, can_start=True)
    if state is None:
        state = SellerValuationWorkspace(lead_id=lead_id, version=0, evidence_version=0,
                                         subject=body.subject.model_dump(mode="json"))
        db.add(state)
    elif ValuationDraftSubject.model_validate(state.subject) == body.subject:
        return _projection(db, lead_id, state)
    else:
        state.subject = body.subject.model_dump(mode="json")
    return _save(db, state, evidence_changed=True)


def _public_asking(db):
    return db.query(Listing.id, Listing.title, Listing.type, Listing.location, Listing.price, Listing.currency,
                    Listing.area_sqft, Listing.bedrooms, Listing.bathrooms).filter(
        Listing.status == "active", Listing.transaction.in_(("sale", "both")),
        Listing.price > 0, Listing.currency.in_(("USD", "DOP")), Listing.location.is_not(None),
    )


def _candidate(row):
    area_m2 = (row.area_sqft * SQFT_TO_M2).quantize(Decimal("0.01")) if row.area_sqft and row.area_sqft > 0 else None
    if area_m2 is not None and area_m2 > Decimal("99999999.99"):
        area_m2 = None
    return AskingCandidate(
        listing_id=row.id, title=row.title, property_type=row.type, location=row.location, price=row.price,
        currency=row.currency, area_m2=area_m2,
        bedrooms=row.bedrooms if row.bedrooms is not None and row.bedrooms >= 0 else None,
        bathrooms=row.bathrooms if row.bathrooms is not None and row.bathrooms >= 0 else None,
    )


def asking_candidates(db, property_type, q, limit):
    query = _public_asking(db)
    if property_type is not None:
        query = query.filter(Listing.type == property_type)
    if q:
        location_term = f"%{escape_like(normalized_location(q))}%"
        title_term = f"%{escape_like(q)}%"
        query = query.filter(or_(Listing.title.ilike(title_term, escape="\\"),
                                 normalized_location_column().like(location_term, escape="\\")))
    rows = query.order_by(Listing.updated_at.desc(), Listing.id).limit(limit)
    return [_candidate(row) for row in rows]


def _comparable_limit(db, lead_id):
    if _comparables(db, lead_id).count() >= MAX_COMPARABLES:
        raise HTTPException(422, "Remove a comparable before adding another.")


def add_asking(db, lead_id, body):
    state = _editable(db, lead_id, body.expected_version)
    _comparable_limit(db, lead_id)
    if _comparables(db, lead_id).filter(SellerValuationComparable.listing_id == body.listing_id).count():
        raise HTTPException(409, "This asking listing is already captured.")
    row = _public_asking(db).filter(Listing.id == body.listing_id).one_or_none()
    if row is None:
        raise HTTPException(404, "Active sale listing unavailable.")
    snapshot = _candidate(row)
    db.add(SellerValuationComparable(lead_id=lead_id, kind="asking", **snapshot.model_dump()))
    return _save(db, state, evidence_changed=True)


def add_sold(db, lead_id, body):
    state = _editable(db, lead_id, body.expected_version)
    _comparable_limit(db, lead_id)
    if body.closed_on > datetime.now(DOMINICAN_TIME).date():
        raise HTTPException(422, "A closed-sale date cannot be in the future.")
    db.add(SellerValuationComparable(lead_id=lead_id, kind="sold", **body.model_dump(exclude={"expected_version"})))
    return _save(db, state, evidence_changed=True)


def _find_comparable(db, lead_id, comparator_id):
    item = _comparables(db, lead_id).filter(SellerValuationComparable.id == comparator_id).one_or_none()
    if item is None:
        raise HTTPException(404, "Comparable unavailable.")
    return item


def review_sold(db, actor, lead_id, comparator_id, body):
    state = _editable(db, lead_id, body.expected_version)
    item = _find_comparable(db, lead_id, comparator_id)
    if item.kind != "sold":
        raise HTTPException(422, "Only closed-sale evidence can be verified.")
    item.sold_review = SoldReview(
        decision=body.decision, review_note=body.review_note, reviewed_by=actor.id,
        reviewed_at=datetime.now(timezone.utc),
    ).model_dump(mode="json")
    return _save(db, state, evidence_changed=True)


def remove_comparable(db, lead_id, comparator_id, version):
    state = _editable(db, lead_id, version)
    db.delete(_find_comparable(db, lead_id, comparator_id))
    return _save(db, state, evidence_changed=True)


def review(db, actor, lead_id, body):
    state = _editable(db, lead_id, body.expected_version)
    if body.outcome == "range":
        subject = ValuationDraftSubject.model_validate(state.subject)
        evidence = _comparables(db, lead_id).filter(or_(
            SellerValuationComparable.kind == "asking",
            SellerValuationComparable.sold_review["decision"].astext == "verified",
        )).count()
        if subject.property_type is None or subject.location is None or not evidence:
            raise HTTPException(422, "A reviewed range requires property type, location, and a captured asking or verified sale.")
    fields = body.model_dump(exclude={"expected_version"})
    report = ReportFields.model_validate(fields).model_dump(mode="json")
    state.review = {**report, "reviewed_at": datetime.now(timezone.utc).isoformat(), "reviewed_by": str(actor.id)}
    state.review_evidence_version = state.evidence_version
    return _save(db, state)


def add_follow_up(db, lead_id, body):
    state = _editable(db, lead_id, body.expected_version)
    if _follow_ups(db, lead_id).count() >= MAX_FOLLOW_UPS:
        raise HTTPException(422, "This valuation has reached its follow-up limit.")
    db.add(SellerValuationFollowUp(lead_id=lead_id, follow_up_on=body.follow_up_on, note=body.note))
    return _save(db, state)


def complete_follow_up(db, lead_id, follow_up_id, body):
    state = _editable(db, lead_id, body.expected_version)
    item = _follow_ups(db, lead_id).filter(SellerValuationFollowUp.id == follow_up_id).one_or_none()
    if item is None:
        raise HTTPException(404, "Follow-up unavailable.")
    if item.completed_at is not None:
        return _projection(db, lead_id, state)
    item.completed_at = datetime.now(timezone.utc)
    return _save(db, state)
