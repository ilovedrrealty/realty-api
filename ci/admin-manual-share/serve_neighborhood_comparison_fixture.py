"""Serve exact editorial routes and two synthetic listing locations in hosted CI."""

import json
from pathlib import Path
import re
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_editorial_fixture as editorial

fixture = editorial.fixture
migrate_base = fixture.migrate_and_seed


def migrate_and_bind():
    from database import SessionLocal
    from models.editorial import EditorialPage
    from models.lead import Lead
    from models.listing import Listing

    data = migrate_base()
    fixture.advance("seed_neighborhood_listings")
    with SessionLocal.begin() as db:
        fixture.require(db.query(EditorialPage).count() == 0, "Editorial fixture must start empty")
        lead = db.get(Lead, UUID(data["shareLeadId"]))
        bay = db.get(Listing, lead.property_id)
        ridge = db.get(Listing, UUID(data["foreignListingId"]))
        fixture.require(db.query(Listing).count() == 2 and bay.id != ridge.id
                        and bay.status == ridge.status == "active", "Exactly two owned synthetic listings required")
        bay.title, bay.location, bay.transaction = "Fixture Bay residence", "Fixture Bay", "sale"
        ridge.title, ridge.location = "Fixture Ridge residence", "Fixture Ridge"
        data["comparisonListingIds"] = {"bay": str(bay.id), "ridge": str(ridge.id)}
    return data


if __name__ == "__main__":
    try:
        fixture.APP_COMMIT = editorial.pin("ILDR_NEIGHBORHOOD_APP_COMMIT", 40)
        fixture.APP_TREE = editorial.pin("ILDR_NEIGHBORHOOD_APP_TREE", 40)
        fixture.MIGRATION_HEAD = editorial.pin("ILDR_NEIGHBORHOOD_MIGRATION", 12)
        fixture.migrate_and_seed = migrate_and_bind
        fixture.confined_api = editorial.confined_editorial_api
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
