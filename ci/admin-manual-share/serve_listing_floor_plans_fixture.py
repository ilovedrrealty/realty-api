"""Seed one synthetic listing and two in-memory floor plan images for hosted rehearsal."""

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_admin_share_fixture as fixture


LISTING_ID = UUID("10000000-0000-0000-0000-000000000001")
LISTING_TITLE = "Synthetic Floor Plan Villa"
ADMIN = "/admin/listings/" + str(LISTING_ID) + "/floor-plan"
seed_base = fixture.migrate_and_seed
confine_base = fixture.confined_api
files = {}


def image(fmt, size, color):
    from PIL import Image, ImageDraw

    output = BytesIO()
    with Image.new("RGB", size, color) as plan:
        with_line = ImageDraw.Draw(plan)
        with_line.rectangle((5, 5, size[0] - 6, size[1] - 6), outline="#111827", width=2)
        with_line.line((5, size[1] // 2, size[0] - 6, size[1] // 2), fill="#111827", width=2)
        plan.save(output, format=fmt)
    return output.getvalue()


def migrate_and_seed():
    from database import SessionLocal
    from models.listing import Listing
    from models.listing_floor_plan import ListingFloorPlan
    from models.user import User

    original = seed_base()
    fixture.advance("seed_floor_plan_listing")
    accounts = {role: original["accounts"][role] for role in ("admin", "owner")}
    files.update({
        "image/png": image("PNG", (64, 48), "#ecefff"),
        "image/jpeg": image("JPEG", (72, 52), "#ffe8cd"),
    })
    with SessionLocal.begin() as db:
        owner = db.query(User).filter_by(email=accounts["owner"]["email"], role="owner").one()
        fixture.require(db.query(Listing).count() == 2 and db.query(ListingFloorPlan).count() == 0
                        and db.get(Listing, LISTING_ID) is None, "Exact synthetic listing precondition required")
        db.add(Listing(
            id=LISTING_ID, title=LISTING_TITLE, description="Synthetic private floor plan browser review listing.",
            type="villa", transaction="sale", price=250000, currency="USD", bedrooms=3,
            bathrooms=3, area_sqft=1800, location="Cabarete", status="active",
            submitted_by=owner.id, owner_id=owner.id, images=[],
        ))
    return {
        "accounts": accounts, "listing_id": str(LISTING_ID), "listing_title": LISTING_TITLE,
        "original_png": base64.b64encode(files["image/png"]).decode("ascii"),
        "replacement_jpeg": base64.b64encode(files["image/jpeg"]).decode("ascii"),
    }


def confined_floor_plans(api):
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    baseline = confine_base(api)

    async def serve(scope, receive, send):
        if scope["type"] != "http":
            return await baseline(scope, receive, send)
        method, path = scope["method"], scope["path"]
        if (method, path) not in {
            ("PUT", ADMIN + "/draft"), ("POST", ADMIN + "/publish"), ("POST", ADMIN + "/withdraw"),
        }:
            return await baseline(scope, receive, send)

        request = Request(scope, receive)
        raw = await request.body()
        allowed = False
        if method == "PUT":
            expected = files.get(request.headers.get("content-type"))
            allowed = expected is not None and raw == expected
        elif len(raw) <= 512:
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                body = None
            expected_keys = ({"draft_id", "expected_published_id", "owner_permission_confirmed"}
                             if path.endswith("/publish") else {"published_id"})
            allowed = isinstance(body, dict) and set(body) <= expected_keys
        if not allowed:
            return await JSONResponse({"detail": "Synthetic floor plan payload refused"}, status_code=405,
                                      headers={"Cache-Control": "no-store"})(scope, receive, send)

        consumed = False

        async def replay():
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": raw, "more_body": False}

        return await api(scope, replay, send)

    return serve


def pin(name, digits):
    value = os.environ.get(name, "")
    fixture.require(re.fullmatch(rf"[a-f0-9]{{{digits}}}", value), "Exact floor plan application pin required")
    return value


if __name__ == "__main__":
    try:
        fixture.API_PORT = 8918
        fixture.APP_COMMIT = pin("ILDR_FLOOR_PLAN_APP_COMMIT", 40)
        fixture.APP_TREE = pin("ILDR_FLOOR_PLAN_APP_TREE", 40)
        fixture.MIGRATION_HEAD = pin("ILDR_FLOOR_PLAN_MIGRATION", 12)
        fixture.migrate_and_seed = migrate_and_seed
        fixture.confined_api = confined_floor_plans
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
