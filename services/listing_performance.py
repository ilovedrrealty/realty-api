"""Private listing performance assembled from observed activity."""

from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import DateTime, and_, false, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from models.inquiry import Inquiry
from models.lead import Lead
from models.listing import Listing
from models.listing_event import ListingEvent
from models.listing_performance import ListingDailyView, ListingPerformanceEntry, ListingViewCoverage
from models.user import User
from models.viewing import PropertyViewing
from schemas.listing_performance import DailyViews, ListingPerformance, PerformanceEntry, PerformanceMilestone, PerformanceRange, PerformanceTotals, PerformanceViews
from services.service_visit import DR_TIMEZONE


TIME_ZONE = "America/Santo_Domingo"
ONE_DAY = timedelta(days=1)
MAX_RANGE_DAYS = 366
MAX_TIMELINE_ITEMS = 100
MILESTONE_KINDS = ("submitted", "approved", "archived", "rejected", "edit_approved")


def now():
    return datetime.now(timezone.utc)


def record_view(db, listing_id):
    # The conditional increment preserves the existing active-listing gate and avoids lost counts.
    observed_at = db.execute(
        update(Listing).where(Listing.id == listing_id, Listing.status == "active")
        .values(view_count=Listing.view_count + 1).returning(func.now())
    ).scalar_one_or_none()
    if observed_at is None:
        return
    day = observed_at.astimezone(DR_TIMEZONE).date()
    daily = insert(ListingDailyView).values(listing_id=listing_id, view_date=day, view_count=1)
    db.execute(daily.on_conflict_do_update(
        index_elements=(ListingDailyView.listing_id, ListingDailyView.view_date),
        set_={"view_count": ListingDailyView.view_count + 1},
    ))
    # Set the global coverage marker only after an observed increment. If the first
    # requests overlap, the earliest timestamp still wins across different listings.
    observed = literal(observed_at, type_=DateTime(timezone=True))
    coverage = insert(ListingViewCoverage).from_select(
        ("id", "started_at"),
        select(literal(1), observed).where(~select(ListingViewCoverage.id).where(
            ListingViewCoverage.id == 1, ListingViewCoverage.started_at <= observed,
        ).exists()),
    )
    db.execute(coverage.on_conflict_do_update(
        index_elements=(ListingViewCoverage.id,), set_={"started_at": coverage.excluded.started_at},
        where=coverage.excluded.started_at < ListingViewCoverage.started_at,
    ))
    db.commit()


def _scope(db, actor, listing_id, *, write=False):
    query = db.query(Listing).filter(Listing.id == listing_id)
    if actor.role == "owner":
        query = query.filter(or_(Listing.owner_id == actor.id,
                                 and_(Listing.owner_id.is_(None), Listing.submitted_by == actor.id)))
    elif actor.role == "realtor":
        query = query.filter(Listing.assigned_realtor_id == actor.id)
    elif actor.role != "admin":
        raise HTTPException(404, "Listing performance unavailable.")
    listing = query.with_for_update(read=not write, of=Listing).populate_existing().one_or_none()
    role = db.query(User.role).filter(User.id == actor.id, User.status == "active",
                                     User.email_verified.is_(True)).scalar()
    if listing is None or not (
        role == "admin" or (role == "owner" and (listing.owner_id or listing.submitted_by) == actor.id)
        or (role == "realtor" and listing.assigned_realtor_id == actor.id)
    ):
        raise HTTPException(404, "Listing performance unavailable.")
    can_record = role in ("admin", "realtor")
    if write and not can_record:
        raise HTTPException(403, "Only assigned staff can record performance activity.")
    return listing, can_record


def _window(start, end, today):
    end = end or today
    start = start or date.fromordinal(max(1, end.toordinal() - 29))
    if end < start or (end - start).days >= MAX_RANGE_DAYS or end > today + timedelta(days=MAX_RANGE_DAYS):
        raise HTTPException(422, "Choose a reporting period of up to 366 days.")
    bounds = (datetime.combine(start, time.min, DR_TIMEZONE), datetime.combine(end + ONE_DAY, time.min, DR_TIMEZONE))
    return start, end, bounds


def _views(db, listing, start, end, today):
    tracked_from = db.query(ListingViewCoverage.started_at).filter(ListingViewCoverage.id == 1).scalar()
    first_day = tracked_from.astimezone(DR_TIMEZONE).date() if tracked_from is not None else None
    counts = dict(db.query(ListingDailyView.view_date, ListingDailyView.view_count).filter(
        ListingDailyView.listing_id == listing.id, ListingDailyView.view_date.between(start, end),
    ).all())
    days = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        if first_day is None or day < first_day or day > today:
            days.append(DailyViews(date=day, count=None, coverage="unavailable"))
        else:
            coverage = "partial" if day in (first_day, today) else "complete"
            days.append(DailyViews(date=day, count=counts.get(day, 0), coverage=coverage))
    covered_days = [day.count for day in days if day.count is not None]
    total = sum(covered_days) if covered_days else None
    coverage = "unavailable" if total is None else "complete" if all(day.coverage == "complete" for day in days) else "partial"
    return PerformanceViews(lifetime=listing.view_count, tracked_from=tracked_from, range_total=total,
                            coverage=coverage, days=days)


def _totals(db, listing_id, start, end, lower, upper):
    inquiries = select(func.count()).select_from(Lead).where(
        Lead.property_id == listing_id, Lead.type == "property_inquiry",
        Lead.created_at >= lower, Lead.created_at < upper,
        ~func.coalesce(Lead.tracking.contains({"internal_test": True}), false()),
    )
    # The legacy inquiry endpoint persists its own requests and never writes a lead.
    legacy_inquiries = select(func.count()).select_from(Inquiry).where(
        Inquiry.listing_id == listing_id, Inquiry.created_at >= lower, Inquiry.created_at < upper,
    )
    confirmed = select(func.count()).select_from(PropertyViewing).where(
        PropertyViewing.listing_id == listing_id, PropertyViewing.status == "confirmed",
        PropertyViewing.starts_at >= lower, PropertyViewing.starts_at < upper,
    )
    completed = select(func.count()).select_from(ListingPerformanceEntry).where(
        ListingPerformanceEntry.listing_id == listing_id, ListingPerformanceEntry.kind == "completed_showing",
        ListingPerformanceEntry.occurred_on.between(start, end),
    )
    counts = db.execute(select(
        (inquiries.scalar_subquery() + legacy_inquiries.scalar_subquery()).label("property_inquiries"),
        confirmed.scalar_subquery().label("confirmed_viewings"),
        completed.scalar_subquery().label("recorded_completed_showings"),
    )).mappings().one()
    return PerformanceTotals.model_validate(counts)


def report(db, actor, listing_id, start, end):
    listing, can_record = _scope(db, actor, listing_id)
    today = now().astimezone(DR_TIMEZONE).date()
    start, end, (lower, upper) = _window(start, end, today)
    event_rows = db.query(ListingEvent.event_type, ListingEvent.created_at).filter(
        ListingEvent.listing_id == listing_id, ListingEvent.event_type.in_(MILESTONE_KINDS),
        ListingEvent.created_at >= lower, ListingEvent.created_at < upper,
    ).order_by(ListingEvent.created_at.desc(), ListingEvent.id).limit(MAX_TIMELINE_ITEMS + 1).all()
    entry_rows = db.query(ListingPerformanceEntry).filter(
        ListingPerformanceEntry.listing_id == listing_id, ListingPerformanceEntry.occurred_on.between(start, end),
    ).order_by(ListingPerformanceEntry.occurred_on.desc(), ListingPerformanceEntry.id).limit(MAX_TIMELINE_ITEMS + 1).all()
    return ListingPerformance(
        listing_id=listing_id, title=listing.title, status=listing.status, can_record=can_record,
        range=PerformanceRange(start=start, end=end, time_zone=TIME_ZONE),
        views=_views(db, listing, start, end, today),
        totals=_totals(db, listing_id, start, end, lower, upper),
        milestones=[PerformanceMilestone(kind=kind, occurred_on=stamp.astimezone(DR_TIMEZONE).date())
                    for kind, stamp in event_rows[:MAX_TIMELINE_ITEMS]],
        entries=[PerformanceEntry.model_validate(row) for row in entry_rows[:MAX_TIMELINE_ITEMS]],
        milestones_truncated=len(event_rows) > MAX_TIMELINE_ITEMS, entries_truncated=len(entry_rows) > MAX_TIMELINE_ITEMS,
    )


def add_entry(db, actor, listing_id, body):
    _scope(db, actor, listing_id, write=True)
    existing = db.get(ListingPerformanceEntry, (listing_id, body.request_id))
    if existing is not None:
        if (existing.kind, existing.occurred_on, existing.text) != (body.kind, body.occurred_on, body.text):
            raise HTTPException(409, "This request identifier was already used for another entry.")
        return PerformanceEntry.model_validate(existing)
    if body.occurred_on > now().astimezone(DR_TIMEZONE).date():
        raise HTTPException(422, "Record activity dated today or earlier.")
    row = ListingPerformanceEntry(listing_id=listing_id, id=body.request_id, kind=body.kind,
                                  occurred_on=body.occurred_on, text=body.text, created_by=actor.id)
    db.add(row)
    result = PerformanceEntry.model_validate(row)
    db.commit()
    return result
