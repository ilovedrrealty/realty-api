"""Confine viewing tests to two synthetic listing assignments."""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_admin_share_fixture as fixture


seed_base = fixture.migrate_and_seed
base_confined_api = fixture.confined_api
CREATE_OR_ACT = re.compile(r"/viewings(?:/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}/action)?")


def seed_viewings():
    from database import SessionLocal
    from models.listing import Listing
    from models.user import User
    from services.service_visit import DR_TIMEZONE

    initial = seed_base()
    fixture.advance("seed_viewings")
    with SessionLocal.begin() as db:
        agent = db.query(User).filter(User.email == initial["accounts"]["realtor"]["email"], User.role == "realtor").one()
        admin = db.query(User).filter(User.email == initial["accounts"]["admin"]["email"], User.role == "admin").one()
        realtor_listing = db.query(Listing).filter(
            Listing.title == "Browser rehearsal villa", Listing.assigned_realtor_id == agent.id,
            Listing.status == "active",
        ).one()
        agent.calendly_url = "https://calendly.com/isolated-fixture/property-viewing"
        admin_listing = Listing(
            id=uuid4(), title="Synthetic virtual coast listing", type="villa", transaction="sale",
            price=200, location="Cabarete", status="active", submitted_by=admin.id, assigned_realtor_id=admin.id, images=[],
        )
        unknown_listing = Listing(
            id=uuid4(), title="Synthetic unassigned viewing listing", type="villa", transaction="sale",
            price=200, location="Cabarete", status="active", submitted_by=admin.id, images=[],
        )
        db.add_all((admin_listing, unknown_listing))
        db.flush()
        day = (datetime.now(DR_TIMEZONE).date() + timedelta(days=4)).isoformat()
        return {
            "accounts": initial["accounts"], "realtorListing": str(realtor_listing.id),
            "adminListing": str(admin_listing.id), "unassignedListing": str(unknown_listing.id), "day": day,
        }


def confined_viewings_api(api):
    default = base_confined_api(api)

    async def serve(scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "POST" and CREATE_OR_ACT.fullmatch(scope["path"]):
            return await api(scope, receive, send)
        return await default(scope, receive, send)

    return serve


def pin(name, size):
    value = os.environ.get(name, "")
    fixture.require(re.fullmatch(rf"[a-f0-9]{{{size}}}", value), "Exact viewing application pin required")
    return value


if __name__ == "__main__":
    try:
        fixture.APP_COMMIT = pin("ILDR_VIEWING_APP_COMMIT", 40)
        fixture.APP_TREE = pin("ILDR_VIEWING_APP_TREE", 40)
        fixture.MIGRATION_HEAD = pin("ILDR_VIEWING_MIGRATION", 12)
        fixture.migrate_and_seed = seed_viewings
        fixture.confined_api = confined_viewings_api
        fixture.run()
    except Exception as error:
        kind = type(error).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", kind):
            kind = "UnhandledError"
        print("BROWSER_API_FIXTURE_FAILURE " + json.dumps({
            "stage": fixture.stage, "error_type": kind, "fixture_line": None,
        }), flush=True)
        raise SystemExit(1) from None
