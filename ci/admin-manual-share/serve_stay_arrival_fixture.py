"""Seed synthetic stay cases on the existing confined browser test server."""

import json
import os
import re
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_admin_share_fixture as fixture


seed_base = fixture.migrate_and_seed
base_confined_api = fixture.confined_api
owner_guide_path = None


def seed_stays():
    global owner_guide_path
    from database import SessionLocal
    from models.booking import Booking
    from models.listing import Listing
    from models.user import User
    from services.service_visit import DR_TIMEZONE
    from utils.security import hash_password

    data = seed_base()
    today = datetime.now(timezone.utc).astimezone(DR_TIMEZONE).date()
    fixture.advance("seed_stays")
    with SessionLocal.begin() as session:
        fixture.require(session.query(Booking).count() == 0, "A fresh stay booking fixture is required")
        users = {
            role: session.query(User).filter(User.email == account["email"], User.role == role).one()
            for role, account in data["accounts"].items()
        }
        foreign_owner = User(
            id=uuid4(), email="arrival-other-owner@example.com",
            password_hash=hash_password(secrets.token_urlsafe(24)),
            role="owner", status="active", display_name="Other synthetic host", email_verified=True,
        )
        session.add(foreign_owner)
        session.flush()
        rental = Listing(
            id=uuid4(), title="Synthetic arrival house", transaction="rent", type="villa", status="active",
            price=100, location="Cabarete", images=[],
            submitted_by=users["owner"].id, owner_id=users["owner"].id, assigned_realtor_id=users["realtor"].id,
        )
        foreign = Listing(
            id=uuid4(), title="Other synthetic house", transaction="rent", type="villa", status="active",
            price=100, location="Cabarete", images=[],
            submitted_by=foreign_owner.id, owner_id=foreign_owner.id,
        )
        session.add_all((rental, foreign))
        session.flush()
        owner_guide_path = f"/bookings/listings/{rental.id}/arrival-guide"
        cases = (
            ("inWindow", "confirmed", users["buyer"].id, 1),
            ("outsideWindow", "confirmed", users["buyer"].id, 12),
            ("pending", "pending", users["buyer"].id, 0),
            ("cancelled", "cancelled", users["buyer"].id, 2),
            ("unclaimed", "confirmed", None, -1),
        )
        stays = {}
        for key, state, buyer_id, days_until_arrival in cases:
            arrival = today + timedelta(days=days_until_arrival)
            stay = Booking(
                id=uuid4(), listing_id=rental.id, buyer_id=buyer_id, guest_name="Synthetic guide guest",
                check_in=arrival.isoformat(), check_out=(arrival + timedelta(days=1)).isoformat(),
                status=state, guests=1, total_price=100,
            )
            session.add(stay)
            stays[key] = str(stay.id)
        return {
            "accounts": data["accounts"], "stays": stays,
            "listingId": str(rental.id), "foreignListingId": str(foreign.id),
        }


def confined_stay_api(api):
    fixture.require(owner_guide_path is not None, "An exact seeded listing is required")
    default = base_confined_api(api)

    async def serve(scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "PUT" and scope["path"] == owner_guide_path:
            return await api(scope, receive, send)
        return await default(scope, receive, send)

    return serve


def required_pin(name, chars):
    value = os.environ.get(name, "")
    fixture.require(re.fullmatch(rf"[a-f0-9]{{{chars}}}", value), "Exact stay-guide checkout pin required")
    return value


if __name__ == "__main__":
    try:
        fixture.APP_COMMIT = required_pin("ILDR_STAY_GUIDE_APP_COMMIT", 40)
        fixture.APP_TREE = required_pin("ILDR_STAY_GUIDE_APP_TREE", 40)
        fixture.MIGRATION_HEAD = required_pin("ILDR_STAY_GUIDE_MIGRATION", 12)
        fixture.migrate_and_seed = seed_stays
        fixture.confined_api = confined_stay_api
        fixture.run()
    except Exception as error:
        kind = type(error).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", kind):
            kind = "UnhandledError"
        print("BROWSER_API_FIXTURE_FAILURE " + json.dumps({
            "stage": fixture.stage, "error_type": kind, "fixture_line": None,
        }), flush=True)
        raise SystemExit(1) from None
