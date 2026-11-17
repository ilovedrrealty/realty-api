from collections import defaultdict
from datetime import timedelta, timezone
import re
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased

from models.listing import Listing
from models.user import User
from models.viewing import PropertyViewing as Stop, ViewingItinerary as Itinerary
from schemas.viewing import AssignedPage, ContactView, ItineraryPage, ItineraryView, ListingView, ViewingView
from services.property_network import now
from services.service_visit import DR_TIMEZONE
from services.service_workflow import fingerprint, lock_users
from utils.agents import eligible_agent_query


SLOT = timedelta(minutes=30)
AGENT_ROLES = ("realtor", "admin")
OPEN_STATES = ("requested", "proposed", "confirmed")
AGENT = aliased(User, name="viewing_agent")
BUYER = aliased(User, name="viewing_buyer")
LIVE = and_(
    Listing.status == "active", Listing.assigned_realtor_id == Stop.agent_id, Stop.status != "unavailable",
    AGENT.role.in_(AGENT_ROLES), AGENT.status == "active", AGENT.email_verified.is_(True),
    BUYER.role == "buyer", BUYER.status == "active", BUYER.email_verified.is_(True),
)


def unavailable():
    raise HTTPException(404, "Viewing or assigned contact is unavailable")


def conflict():
    raise HTTPException(409, "Viewing changed or this time is unavailable. Refresh before trying again.")


def _url_parts(raw):
    if not raw or len(raw) > 600 or "\\" in raw or any(not 33 <= ord(char) <= 126 for char in raw):
        return None
    try:
        parts = urlsplit(raw)
        if parts.scheme != "https" or not parts.hostname or parts.port not in (None, 443) or (
                parts.username is not None or parts.password is not None or parts.fragment):
            return None
        return parts
    except ValueError:
        return None


def scheduling_url(raw):
    parts = _url_parts(raw)
    if parts is None:
        return None
    host, path = parts.hostname, parts.path
    if host in ("calendly.com", "www.calendly.com", "calendar.app.google") and path.strip("/"):
        return raw
    if host == "calendar.google.com" and re.fullmatch(r"/calendar/(?:u/\d+/)?appointments/schedules/[^/]+/?", path):
        return raw
    return None


def meeting_url(raw):
    parts = _url_parts(raw)
    if parts is None:
        return None
    host, path = parts.hostname, parts.path
    if host == "meet.google.com" and re.fullmatch(r"/[a-z]{3}-[a-z]{4}-[a-z]{3}/?", path):
        return raw
    if (host == "zoom.us" or host.endswith(".zoom.us")) and re.fullmatch(r"/(?:j/\d+|my/[A-Za-z0-9_.-]+)/?", path):
        return raw
    if host in ("teams.microsoft.com", "teams.live.com") and (
            path.startswith("/l/meetup-join/") or re.fullmatch(r"/meet/[^/]+/?", path)):
        return raw
    return None


def _eligible(user, roles):
    return user is not None and user.role in roles and user.status == "active" and user.email_verified is True


def contact(db, listing_id):
    row = (eligible_agent_query(db).add_entity(Listing)
           .join(Listing, Listing.assigned_realtor_id == User.id)
           .filter(Listing.id == listing_id, Listing.status == "active").first())
    if row is None:
        unavailable()
    agent, listing = row
    return ContactView(listing=ListingView(id=listing.id, title=listing.title, location=listing.location),
                       agent_name=agent.display_name, scheduling_url=scheduling_url(agent.calendly_url))


def _details(db):
    return (db.query(Stop, LIVE.label("live"), AGENT.calendly_url)
            .select_from(Stop).join(Itinerary, Itinerary.id == Stop.itinerary_id)
            .join(Listing, Listing.id == Stop.listing_id)
            .join(AGENT, AGENT.id == Stop.agent_id).join(BUYER, BUYER.id == Itinerary.buyer_id))


def _view(row):
    stop, live, calendar_url = row
    status = stop.status if live or stop.status not in OPEN_STATES else "unavailable"
    return ViewingView(
        id=stop.id, version=stop.version,
        listing=ListingView(id=stop.listing_id, title=stop.listing_title, location=stop.listing_location),
        status=status, mode=stop.mode, starts_at=stop.starts_at.astimezone(DR_TIMEZONE),
        timezone="America/Santo_Domingo", agent_name=stop.agent_name, attendee_name=stop.attendee_name, note=stop.note,
        meeting_details=stop.meeting_details if live else None, meeting_url=stop.meeting_url if live else None,
        scheduling_url=scheduling_url(calendar_url) if live and status in OPEN_STATES else None,
    )


def _itineraries(db, itineraries):
    views = defaultdict(list)
    if itineraries:
        ids = [item.id for item in itineraries]
        rows = _details(db).filter(Stop.itinerary_id.in_(ids)).order_by(Stop.starts_at, Stop.id).all()
        for row in rows:
            views[row[0].itinerary_id].append(_view(row))
    return [ItineraryView(id=row.id, title=row.title, items=views[row.id]) for row in itineraries]


def mine(db, user, page, page_size):
    itineraries = (db.query(Itinerary).filter(Itinerary.buyer_id == user.id)
                   .order_by(Itinerary.created_at.desc(), Itinerary.id)
                   .offset((page - 1) * page_size).limit(page_size).all())
    return ItineraryPage(items=_itineraries(db, itineraries))


def assigned(db, user, page, page_size):
    rows = (_details(db).filter(Stop.agent_id == user.id, LIVE)
            .order_by(Stop.starts_at, Stop.id).offset((page - 1) * page_size).limit(page_size).all())
    return AssignedPage(items=[_view(row) for row in rows])


def _matching_request(db, actor_id, request_id, digest):
    row = db.query(Itinerary).filter(Itinerary.buyer_id == actor_id, Itinerary.request_id == request_id).first()
    if row is not None and row.request_sha256 != digest:
        raise HTTPException(409, "This request identifier was already used for different viewing details")
    return row


def _future(value, instant):
    try:
        start = value.astimezone(timezone.utc)
        if start <= instant:
            raise ValueError()
        return start, start + SLOT
    except (OverflowError, ValueError):
        raise HTTPException(422, "Choose a future viewing time") from None


def create(db, user, body):
    digest = fingerprint("request_property_viewings", body.request_id, body)
    prior = _matching_request(db, user.id, body.request_id, digest)
    if prior is not None:
        return _itineraries(db, [prior])[0]

    wanted = {stop.listing_id for stop in body.stops}
    candidates = dict(db.execute(select(Listing.id, Listing.assigned_realtor_id)
                      .where(Listing.id.in_(wanted), Listing.status == "active")).all())
    if set(candidates) != wanted or None in candidates.values():
        unavailable()
    buyer = lock_users(db, user, candidates.values())
    if buyer.role != "buyer":
        unavailable()
    prior = _matching_request(db, buyer.id, body.request_id, digest)
    if prior is not None:
        return _itineraries(db, [prior])[0]

    listings = {row.id: row for row in db.query(Listing).filter(Listing.id.in_(wanted), Listing.status == "active")
                .order_by(Listing.id).populate_existing().with_for_update().all()}
    if set(listings) != wanted or any(row.assigned_realtor_id != candidates[row.id] for row in listings.values()):
        conflict()
    agents = {agent_id: db.get(User, agent_id) for agent_id in set(candidates.values())}
    if any(not _eligible(agent, AGENT_ROLES) for agent in agents.values()):
        unavailable()

    instant = now()
    itinerary = Itinerary(id=uuid4(), buyer_id=buyer.id, request_id=body.request_id, request_sha256=digest,
                          title=body.title, created_at=instant)
    db.add(itinerary)
    db.flush()
    for item in body.stops:
        listing = listings[item.listing_id]
        agent = agents[listing.assigned_realtor_id]
        start, _ = _future(item.starts_at, instant)
        db.add(Stop(id=uuid4(), itinerary_id=itinerary.id, listing_id=listing.id, agent_id=agent.id,
                    listing_title=listing.title, listing_location=listing.location, agent_name=agent.display_name,
                    attendee_name=buyer.display_name, note=item.note, status="requested", mode=item.mode,
                    starts_at=start, version=1))
    db.flush()
    result = _itineraries(db, [itinerary])[0]
    db.commit()
    return result


def _requested_context(db, user, identifier):
    hint = db.execute(select(Stop.listing_id, Stop.agent_id, Itinerary.buyer_id)
                      .join(Itinerary, Itinerary.id == Stop.itinerary_id)
                      .where(Stop.id == identifier, or_(Stop.agent_id == user.id, Itinerary.buyer_id == user.id))).one_or_none()
    if hint is None:
        unavailable()
    actor = lock_users(db, user, (hint.buyer_id, hint.agent_id))
    buyer = db.get(User, hint.buyer_id)
    agent = db.get(User, hint.agent_id)
    if not _eligible(buyer, ("buyer",)) or not _eligible(agent, AGENT_ROLES):
        unavailable()
    listing = db.execute(select(Listing.id).where(Listing.id == hint.listing_id, Listing.status == "active",
                         Listing.assigned_realtor_id == agent.id).with_for_update()).scalar_one_or_none()
    if listing is None:
        unavailable()
    stop = db.query(Stop).filter(Stop.id == identifier, Stop.agent_id == agent.id).populate_existing().with_for_update().first()
    if stop is None or stop.status == "unavailable":
        unavailable()
    return stop, buyer.id, actor.id == agent.id


def _no_overlap(db, stop, buyer_id, start, end):
    # User locks serialize both sides for the entire check and state change.
    existing = (_details(db).filter(Stop.id != stop.id, Stop.status == "confirmed", LIVE,
                Stop.starts_at > start - SLOT, Stop.starts_at < end,
                or_(Stop.agent_id == stop.agent_id, Itinerary.buyer_id == buyer_id))
                .with_entities(Stop.id).first())
    if existing is not None:
        conflict()


def _schedule(db, stop, buyer_id, body, instant):
    start, end = _future(body.starts_at or stop.starts_at, instant)
    if body.action == "confirm" and start != stop.starts_at:
        conflict()
    if stop.mode == "virtual":
        url = meeting_url(body.meeting_url)
        if url is None:
            raise HTTPException(422, "Provide a supported HTTPS meeting link")
    else:
        if body.meeting_details is None or body.meeting_url is not None:
            raise HTTPException(422, "Provide an in-person meeting place")
        url = None
    _no_overlap(db, stop, buyer_id, start, end)
    stop.starts_at = start
    stop.meeting_url = url
    stop.meeting_details = body.meeting_details
    stop.availability_checked_at = instant
    stop.status = "confirmed" if body.action == "confirm" else "proposed"


def action(db, user, identifier, body):
    stop, buyer_id, is_agent = _requested_context(db, user, identifier)
    if stop.version != body.expected_version:
        conflict()
    if is_agent:
        allowed = {
            "requested": ("propose", "confirm", "decline", "cancel"),
            "proposed": ("propose", "decline", "cancel"),
            "confirmed": ("cancel",),
        }
    else:
        allowed = {"requested": ("cancel",), "proposed": ("accept", "decline", "cancel"), "confirmed": ("cancel",)}
    if body.action not in allowed.get(stop.status, ()):
        conflict()

    instant = now()
    if body.action in ("propose", "confirm"):
        _schedule(db, stop, buyer_id, body, instant)
    elif body.action == "accept":
        start, end = _future(stop.starts_at, instant)
        _no_overlap(db, stop, buyer_id, start, end)
        stop.status = "confirmed"
    else:
        stop.status = "declined" if body.action == "decline" else "cancelled"
        stop.meeting_details = stop.meeting_url = stop.availability_checked_at = None
    stop.version += 1
    db.flush()
    result = _view(_details(db).filter(Stop.id == stop.id).one())
    db.commit()
    return result
