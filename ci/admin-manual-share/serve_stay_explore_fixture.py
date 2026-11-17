"""Limit shortlist and itinerary writes to this owned rehearsal's records."""

import json
import re
import secrets
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent))

import serve_admin_share_fixture as fixture
import serve_viewings_fixture as viewings


OWNED_TITLE = "Synthetic owned shortlist route"
RECEIVED_TITLE = "Synthetic shared shortlist route"
PRIVATE_NOTES = "SYNTHETIC_STAY_EXPLORE_PRIVATE_NOTES"
PRIVATE_PREFERENCES = "SYNTHETIC_STAY_EXPLORE_PRIVATE_PREFERENCES"
ITINERARY_TITLE = "Synthetic shortlist viewing request"
ID = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"
GRANT_PATH = re.compile(rf"/shortlists/owned/(?P<record>{ID})/(?P<action>share|shares/(?P<grant>{ID})/revoke)")
seed = None


def seed_stay_explore():
    from database import SessionLocal
    from models.listing import Listing
    from models.user import User
    from utils.security import hash_password

    global seed
    seed = viewings.seed_viewings()
    fixture.advance("seed_stay_explore")
    others = {"email": "browser-other-buyer@example.com", "password": secrets.token_urlsafe(24)}
    with SessionLocal.begin() as db:
        buyer = db.query(User).filter_by(email=seed["accounts"]["buyer"]["email"], role="buyer").one()
        realtor = db.query(User).filter_by(email=seed["accounts"]["realtor"]["email"], role="realtor").one()
        fixture.require(type(buyer.user_code) is int and buyer.user_code > 0, "Synthetic buyer code required")
        buyer.assigned_realtor_id = realtor.id
        db.add(User(
            role="buyer", email=others["email"], password_hash=hash_password(others["password"]),
            display_name="Synthetic unrelated buyer", status="active", email_verified=True,
        ))
        db.add(Listing(
            title="Synthetic withdrawn shortlist property", location="Cabarete", type="villa",
            transaction="sale", status="archived", price=100, submitted_by=realtor.id, images=[],
        ))
        seed["buyerCode"] = buyer.user_code
        seed["accounts"]["otherBuyer"] = others
    return seed


def allowed_payload(path, data):
    if type(data) is not dict:
        return False
    if path == "/shortlists/owned":
        for title, listing in ((OWNED_TITLE, seed["realtorListing"]), (RECEIVED_TITLE, seed["adminListing"])):
            if data == {"title": title, "shared_notes": PRIVATE_NOTES,
                        "shared_preferences": PRIVATE_PREFERENCES, "listing_ids": [listing]}:
                return True
        return False
    if path == "/viewings":
        request_id = data.get("request_id")
        return (isinstance(request_id, str) and re.fullmatch(ID, request_id) and data == {
            "request_id": request_id,
            "title": ITINERARY_TITLE,
            "stops": [{
                "listing_id": seed["realtorListing"], "mode": "in_person",
                "starts_at": f'{seed["day"]}T10:00:00-04:00', "note": "",
            }],
        })
    match = GRANT_PATH.fullmatch(path)
    if not match:
        return False

    from database import SessionLocal
    from models.shortlist import ClientShortlist, ClientShortlistGrant
    from models.user import User

    with SessionLocal() as db:
        owned = db.query(ClientShortlist).join(User, ClientShortlist.owner_id == User.id).filter(
            ClientShortlist.id == UUID(match["record"]), ClientShortlist.title == RECEIVED_TITLE,
            User.email == seed["accounts"]["realtor"]["email"],
        ).one_or_none()
        if owned is None:
            return False
        if match["action"] == "share":
            return (type(data.get("recipient_user_code")) is int and data == {
                "recipient_user_code": seed["buyerCode"], "expires_in_days": 7,
            })
        return data == {} and db.query(ClientShortlistGrant).filter_by(
            shortlist_id=owned.id, id=UUID(match["grant"]), recipient_user_code=seed["buyerCode"],
        ).one_or_none() is not None


def confined_stay_explore_api(api):
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    baseline = viewings.base_confined_api(api)

    async def serve(scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await baseline(scope, receive, send)
        path = scope["path"]
        if path not in {"/shortlists/owned", "/viewings"} and not GRANT_PATH.fullmatch(path):
            return await baseline(scope, receive, send)
        raw = await Request(scope, receive).body()
        try:
            payload = json.loads(raw) if len(raw) <= 2048 else None
        except (ValueError, UnicodeError):
            payload = None
        if not allowed_payload(path, payload):
            response = JSONResponse({"detail": "Synthetic rehearsal write refused"}, status_code=405,
                                    headers={"Cache-Control": "no-store"})
            return await response(scope, receive, send)
        consumed = False

        async def replay():
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": raw, "more_body": False}

        return await api(scope, replay, send)

    return serve


if __name__ == "__main__":
    try:
        fixture.APP_COMMIT = viewings.pin("ILDR_VIEWING_APP_COMMIT", 40)
        fixture.APP_TREE = viewings.pin("ILDR_VIEWING_APP_TREE", 40)
        fixture.MIGRATION_HEAD = viewings.pin("ILDR_VIEWING_MIGRATION", 12)
        fixture.migrate_and_seed = seed_stay_explore
        fixture.confined_api = confined_stay_explore_api
        fixture.run()
    except Exception as error:
        kind = type(error).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", kind):
            kind = "UnhandledError"
        print("BROWSER_API_FIXTURE_FAILURE " + json.dumps({
            "stage": fixture.stage, "error_type": kind, "fixture_line": None,
        }), flush=True)
        raise SystemExit(1) from None
