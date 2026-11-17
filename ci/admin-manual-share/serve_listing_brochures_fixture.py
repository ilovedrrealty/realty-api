"""Seed one actual exported listing and constrain its synthetic test transitions."""

import base64
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_editorial_fixture as editorial

fixture = editorial.fixture
migrate_base = fixture.migrate_and_seed
LISTING_ID = UUID("10000000-0000-0000-0000-000000000001")
PHOTO_PATH = "/__brochure_fixture/exterior.png"
PHOTO = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAABgAAAAQCAIAAACDRijCAAAAJklEQVR4nGNQ9sukCmIYNYiOBoVXd1EFjRpET4NOLc+iChrGBgEAAZ3wkJPAtCYAAAAASUVORK5CYII="
)
ACTIVE_TITLE = "Synthetic Villa Mar Azul"
HIDDEN_TITLE = "SYNTHETIC_BROCHURE_HIDDEN_ADDRESS"
CONTROLS = {
    "/__brochure_fixture/fresh-price": "fresh-price",
    "/__brochure_fixture/hidden-rent": "hidden-rent",
    "/__brochure_fixture/withdraw": "withdraw",
}


def seed_brochure():
    from database import SessionLocal
    from models.listing import Listing
    from models.user import User

    data = migrate_base()
    fixture.advance("seed_listing_brochure")
    with SessionLocal.begin() as db:
        owner = db.query(User).filter(User.email == data["accounts"]["owner"]["email"], User.role == "owner").one()
        fixture.require(db.get(Listing, LISTING_ID) is None, "Exported synthetic listing ID must be unused")
        db.add(Listing(
            id=LISTING_ID, title=ACTIVE_TITLE, location="Sosúa", type="villa", transaction="both", status="active",
            currency="DOP", price=Decimal("15550000.00"), price_per_month=Decimal("45750.50"), price_per_day=Decimal("1450.00"),
            bedrooms=3, bathrooms=Decimal("2.5"), area_sqft=2340, lot_size_sqft=4050, year_built=2021,
            latitude=Decimal("19.719482"), longitude=Decimal("-70.519482"),
            description="SYNTHETIC_BROCHURE_UNSCOPED_DESCRIPTION",
            description_es="SYNTHETIC_BROCHURE_UNSCOPED_DESCRIPTION_ES",
            co_listing_agent_email="internal-brochure@example.invalid",
            co_listing_agreement_url="https://fixture.invalid/brochure-private-agreement",
            maps_url="https://maps.example.invalid/private-brochure",
            submitted_by=owner.id, owner_id=owner.id,
            images=[f"http://127.0.0.1:{fixture.API_PORT}{PHOTO_PATH}"],
        ))
    return {"listing_id": str(LISTING_ID), "photo_path": PHOTO_PATH}


def transition_listing(action):
    from database import SessionLocal
    from models.listing import Listing
    from models.listing_location_review import ListingLocationReview

    with SessionLocal.begin() as db:
        listing = db.query(Listing).filter_by(id=LISTING_ID, status="active").with_for_update().one_or_none()
        if listing is None:
            return False
        if action == "fresh-price" and listing.title == ACTIVE_TITLE and listing.price == Decimal("15550000.00"):
            listing.price = Decimal("16000000.25")
            return True
        if action == "hidden-rent" and listing.title == ACTIVE_TITLE and listing.price == Decimal("16000000.25"):
            listing.title = HIDDEN_TITLE
            listing.transaction, listing.currency, listing.price = "rent", "USD", Decimal("15000.00")
            listing.price_per_month, listing.price_per_day = Decimal("700.00"), None
            listing.latitude = listing.longitude = listing.maps_url = None
            db.add(ListingLocationReview(
                listing_id=LISTING_ID, public_precision="hidden",
                latitude=Decimal("19.719482"), longitude=Decimal("-70.519482"),
                review_note="SYNTHETIC_BROCHURE_PRIVATE_REVIEW",
            ))
            return True
        if action == "withdraw" and listing.title == HIDDEN_TITLE and listing.price == Decimal("15000.00"):
            listing.status = "archived"
            return True
        return False


def confined_brochure_api(api):
    from starlette.requests import Request
    from starlette.responses import Response

    async def serve(scope, receive, send):
        if scope["type"] != "http":
            return await api(scope, receive, send)
        method, path = scope["method"], scope["path"]
        if method == "GET" and path == PHOTO_PATH:
            return await Response(PHOTO, media_type="image/png")(scope, receive, send)
        if method in {"GET", "HEAD", "OPTIONS"}:
            return await api(scope, receive, send)
        reply = Response(status_code=405)
        if method == "POST" and path in CONTROLS:
            client = scope.get("client")
            if client and client[0] == "127.0.0.1" and await Request(scope, receive).body() == b"":
                status = 204 if transition_listing(CONTROLS[path]) else 409
                reply = Response(status_code=status, headers={"Cache-Control": "no-store"})
        return await reply(scope, receive, send)

    return serve


if __name__ == "__main__":
    try:
        fixture.API_PORT = 8918
        fixture.APP_COMMIT = editorial.pin("ILDR_BROCHURE_APP_COMMIT", 40)
        fixture.APP_TREE = editorial.pin("ILDR_BROCHURE_APP_TREE", 40)
        fixture.MIGRATION_HEAD = editorial.pin("ILDR_BROCHURE_MIGRATION", 12)
        fixture.migrate_and_seed = seed_brochure
        fixture.confined_api = confined_brochure_api
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
