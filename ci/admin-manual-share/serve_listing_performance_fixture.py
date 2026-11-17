"""Permit performance writes only for one seeded listing in the disposable app."""

from datetime import datetime, time, timedelta, timezone
import json
import re
import sys
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_seller_valuation_fixture as valuation


fixture = valuation.fixture
synthetic_listing = None


def migrate_and_bind():
    from database import SessionLocal
    from models.lead import Lead
    from models.listing import Listing
    from models.listing_event import ListingEvent
    from models.listing_performance import ListingViewCoverage
    from models.user import User
    from models.viewing import PropertyViewing, ViewingItinerary

    global synthetic_listing
    data = valuation.migrate_and_bind()
    fixture.advance("seed_performance")
    country_timezone = timezone(timedelta(hours=-4))
    observed = datetime.now(country_timezone)
    yesterday = observed.date() - timedelta(days=1)
    appointment = datetime.combine(yesterday, time(11), country_timezone)
    with SessionLocal.begin() as db:
        lead = db.get(Lead, UUID(data["shareLeadId"]))
        listing = db.get(Listing, lead.property_id)
        owner = db.query(User).filter(User.email == data["accounts"]["owner"]["email"], User.role == "owner").one()
        realtor = db.query(User).filter(User.email == data["accounts"]["realtor"]["email"], User.role == "realtor").one()
        admin = db.query(User).filter(User.email == data["accounts"]["admin"]["email"], User.role == "admin").one()
        fixture.require(
            listing.status == "active" and listing.title == "Browser rehearsal villa" and listing.owner_id == owner.id
            and listing.assigned_realtor_id == realtor.id and db.get(ListingViewCoverage, 1) is None,
            "An owned synthetic listing without historical daily view rows is required",
        )
        listing.view_count = 9
        db.add(Lead(id=uuid4(), type="property_inquiry", name="Synthetic internal test visitor",
                    email="browser-internal-performance@example.com", property_id=listing.id, status="new",
                    tracking={"internal_test": True}))
        itinerary = ViewingItinerary(id=uuid4(), buyer_id=lead.from_user_id, request_id=uuid4(),
                                     request_sha256="a" * 64, title="S02_PRIVATE_ITINERARY", created_at=appointment)
        db.add(itinerary)
        db.flush()
        db.add(PropertyViewing(
            id=uuid4(), itinerary_id=itinerary.id, listing_id=listing.id, agent_id=realtor.id,
            listing_title=listing.title, listing_location=listing.location, agent_name="S02_PRIVATE_AGENT",
            attendee_name="S02_PRIVATE_ATTENDEE", note="S02_PRIVATE_APPOINTMENT_NOTE", status="confirmed",
            mode="in_person", starts_at=appointment, version=1, meeting_details="S02_PRIVATE_MEETING",
            availability_checked_at=appointment - timedelta(hours=1),
        ))
        db.add(ListingEvent(id=uuid4(), listing_id=listing.id, event_type="approved", actor_id=admin.id,
                            note="S02_PRIVATE_MILESTONE", snapshot_before={"private": "S02_PRIVATE_SNAPSHOT"},
                            created_at=appointment))
        synthetic_listing = str(listing.id)
    data["performanceListingId"] = synthetic_listing
    data["performanceRecordedDay"] = yesterday.isoformat()
    data["performanceWindowEnd"] = (observed.date() + timedelta(days=1)).isoformat()
    return data


def confined_performance_api(api):
    fixture.require(synthetic_listing is not None and re.fullmatch(valuation.UUID, synthetic_listing),
                    "The owned performance listing is required")
    baseline = valuation.confined_valuation_api(api)
    listing = f"/listings/{synthetic_listing}"
    allowed = {f"{listing}/view", f"{listing}/performance/entries"}

    async def serve(scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "POST" and scope["path"] in allowed:
            return await api(scope, receive, send)
        return await baseline(scope, receive, send)

    return serve


if __name__ == "__main__":
    try:
        pin = valuation.development.editorial.pin
        fixture.APP_COMMIT = pin("ILDR_PERFORMANCE_APP_COMMIT", 40)
        fixture.APP_TREE = pin("ILDR_PERFORMANCE_APP_TREE", 40)
        fixture.MIGRATION_HEAD = pin("ILDR_PERFORMANCE_MIGRATION", 12)
        fixture.migrate_and_seed = migrate_and_bind
        fixture.confined_api = confined_performance_api
        fixture.run()
    except Exception as error:
        kind = type(error).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", kind):
            kind = "UnhandledError"
        sqlstate = getattr(getattr(error, "orig", None), "pgcode", None)
        if fixture.stage != "apply_alembic" or not isinstance(sqlstate, str) or not re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
            sqlstate = None
        print("BROWSER_API_FIXTURE_FAILURE " + json.dumps({
            "stage": fixture.stage, "error_type": kind, "fixture_line": None, "sqlstate": sqlstate,
        }), flush=True)
        raise SystemExit(1) from None
