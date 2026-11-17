"""Seed only synthetic listing prices for the real public market report endpoint."""
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sys
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_admin_share_fixture as fixture


base_seed = fixture.migrate_and_seed


def migrate_and_seed():
    from database import SessionLocal
    from models.lead import Lead
    from models.listing import Listing
    from models.listing_location_review import ListingLocationReview

    seeded = base_seed()
    fixture.advance("seed_market_listings")
    with SessionLocal.begin() as db:
        first = db.get(Listing, db.get(Lead, UUID(seeded["shareLeadId"])).property_id)
        second = db.get(Listing, UUID(seeded["foreignListingId"]))
        fixture.require(first and second and first.id != second.id and db.query(Listing).count() == 2
                        and first.status == second.status == "active", "Two synthetic seed listings required")
        first.title = "D02_SYNTHETIC_PRIVATE_TITLE_ONE"
        first.location, first.transaction = "Sosúa", "sale"
        first.price, first.area_sqft = Decimal("210000.00"), 1000
        second.title = "D02_SYNTHETIC_PRIVATE_TITLE_TWO"
        second.location, second.transaction = "Sosúa", "both"
        second.price, second.area_sqft = Decimal("210000.01"), None
        second.is_deal, second.deal_discount_value = True, Decimal("20")
        second.price_per_month, second.price_per_day = Decimal("850.00"), Decimal("100.00")

        def add(number, price, **fields):
            data = dict(id=uuid4(), title=f"D02_SYNTHETIC_PRIVATE_TITLE_{number}", location="Sosúa",
                        type="villa", transaction="sale", currency="USD", status="active",
                        price=Decimal(price), images=[], submitted_by=first.submitted_by, owner_id=first.owner_id)
            row = Listing(**{**data, **fields})
            db.add(row)
            return row

        add(3, "995000.50", currency="DOP")
        add(4, "0.00", type="land", currency="DOP", lot_size_sqft=800)
        add(5, "2500000.00", type="condo", currency="EUR")
        hidden = add(6, "900000.00")
        add(7, "500000.00", location="D02_SYNTHETIC_UNCATALOGED_SCOPE")
        add(8, "10000.00", transaction="rent")
        add(9, "20000.00", status="archived")
        db.flush()
        db.add(ListingLocationReview(listing_id=hidden.id, public_precision="hidden",
                                     source_reference="D02_SYNTHETIC_PRIVATE_LOCATION_EVIDENCE"))
        fixture.require(db.query(Listing).count() == 9, "Exact market fixture listing count required")
    return {"stage": "market_report", "local_scope": "Sosúa", "empty_scope": "Azua"}


def readonly_api(api):
    from starlette.responses import JSONResponse

    async def serve(scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            prohibited = JSONResponse({"detail": "Read-only synthetic market fixture"}, status_code=405,
                                      headers={"Cache-Control": "no-store"})
            return await prohibited(scope, receive, send)
        return await api(scope, receive, send)

    return serve


def pin(name, length):
    value = os.environ.get(name, "")
    fixture.require(re.fullmatch(rf"[a-f0-9]{{{length}}}", value), "Exact market report application pin required")
    return value


if __name__ == "__main__":
    try:
        fixture.APP_COMMIT = pin("ILDR_MARKET_APP_COMMIT", 40)
        fixture.APP_TREE = pin("ILDR_MARKET_APP_TREE", 40)
        fixture.MIGRATION_HEAD = pin("ILDR_MARKET_MIGRATION", 12)
        fixture.API_PORT = 8918
        fixture.migrate_and_seed = migrate_and_seed
        fixture.confined_api = readonly_api
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
