"""Run the real reviewed service request workflow with owned synthetic records."""
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_admin_share_fixture as fixture


base_seed = fixture.migrate_and_seed
base_api = fixture.confined_api
source_id = None
authorized_quotes = {}
CATEGORY = "pool_service"
PROPERTY_CLASS = "house_villa"


def migrate_and_seed():
    from database import SessionLocal
    from models.lead import Lead
    from models.listing import Listing
    from models.professional_profile import ProfessionalProfile
    from models.service_workflow import NetworkServicePreference, NetworkServiceRequest
    from models.user import User
    from utils.security import hash_password

    global source_id
    data = base_seed()
    fixture.advance("seed_service_matching")
    owners = {}
    codes = {}
    published_on = datetime.now(timezone.utc)
    with SessionLocal.begin() as db:
        listing = db.get(Listing, db.get(Lead, UUID(data["shareLeadId"])).property_id)
        fixture.require(listing and listing.title == "Browser rehearsal villa" and listing.status == "active"
                        and listing.owner_id and listing.assigned_realtor_id
                        and db.query(NetworkServiceRequest.id).count() == 0
                        and db.query(ProfessionalProfile.id).count() == 0, "Owned service source required")
        source_id = str(listing.id)
        for key, alias, area, lang, visibility, opted, property_class in (
            ("north", "Synthetic Reviewed North", "north_coast", "es", "published", True, PROPERTY_CLASS),
            ("english", "Synthetic Reviewed English", "cabarete", "en", "published", True, PROPERTY_CLASS),
            ("hidden", "Synthetic Hidden Provider", "cabarete", "es", "hidden", True, PROPERTY_CLASS),
            ("non_opted", "Synthetic Non Opted Provider", "cabarete", "es", "published", False, PROPERTY_CLASS),
            ("elsewhere", "Synthetic Other Area Provider", "east", "es", "published", True, PROPERTY_CLASS),
            ("wrong_class", "Synthetic Wrong Class Provider", "cabarete", "es", "published", True, "hotel_hospitality"),
        ):
            account = {"email": f"browser-reviewed-{key.replace('_', '-')}@example.com",
                       "password": secrets.token_urlsafe(24)}
            user = User(id=uuid4(), email=account["email"], password_hash=hash_password(account["password"]),
                        role="owner", status="active", email_verified=True, display_name=alias)
            db.add(user)
            db.flush()
            snapshot = dict(public_contact_consent=True, category_ids=[CATEGORY],
                            service_area_ids=[area], languages=[lang], business_name=alias,
                            public_email="synthetic-unshared@example.com")
            db.add(ProfessionalProfile(id=uuid4(), user_id=user.id, draft_data=snapshot, published_data=snapshot,
                                       review_status="approved", listing_status=visibility, published_at=published_on))
            db.add(NetworkServicePreference(user_id=user.id, member_alias=alias, category_ids=[CATEGORY],
                                            property_class_ids=[property_class], service_opt_in=opted, version=1))
            codes[key] = user.user_code
            if key in {"north", "english"}:
                owners[key] = account
        db.add(ProfessionalProfile(id=uuid4(), user_id=None, review_status="draft", listing_status="hidden",
                                   draft_data={"business_name": "Synthetic Held Import", "public_contact_consent": False,
                                               "category_ids": [CATEGORY], "service_area_ids": ["cabarete"], "languages": ["es"]}))
        fixture.require(all(type(code) is int and code > 0 for code in codes.values()), "Verified synthetic codes required")
    authorized_quotes.update({
        "Synthetic owner reviewed request": (codes["north"], "es", "Synthetic owner pool maintenance brief", "Synthetic owner"),
        "Synthetic realtor reviewed request": (codes["english"], "en", "Synthetic realtor pool maintenance brief", "Synthetic realtor"),
    })
    return {**data, "serviceSourceId": source_id, "serviceProviders": owners, "serviceCodes": codes}


def confined_matching_api(api):
    from starlette.responses import JSONResponse

    fixture.require(source_id is not None and len(authorized_quotes) == 2, "Owned matching fixture required")
    baseline = base_api(api)
    allowed = {
        "submission_id", "property_id", "listing_id", "property_class", "category_id", "title", "brief",
        "area_label", "requester_alias", "desired_start_on", "recipient_user_codes",
        "shared_brief_acknowledged", "reviewed_match",
    }

    def allowed_body(body):
        if set(body) != allowed or not isinstance(body.get("title"), str):
            return False
        expected = authorized_quotes.get(body["title"])
        if expected is None:
            return False
        code, language, brief, alias = expected
        return (
            isinstance(body["submission_id"], str)
            and re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", body["submission_id"])
            and body["property_id"] is None and body["listing_id"] == source_id
            and body["property_class"] == PROPERTY_CLASS and body["category_id"] == CATEGORY
            and body["brief"] == brief and body["requester_alias"] == alias
            and body["area_label"] == "" and body["desired_start_on"] is None
            and body["recipient_user_codes"] == [code] and body["shared_brief_acknowledged"] is True
            and body["reviewed_match"] == {"area_id": "cabarete", "language": language}
        )

    async def reject(scope, receive, send):
        response = JSONResponse({"detail": "Mutation unavailable in browser rehearsal"}, status_code=405,
                                headers={"Cache-Control": "no-store"})
        return await response(scope, receive, send)

    async def serve(scope, receive, send):
        if (scope["type"] != "http" or scope["method"] != "POST"
                or scope["path"] != "/property-network/service-requests"):
            return await baseline(scope, receive, send)
        buffer = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return
            buffer.extend(message.get("body", b""))
            if len(buffer) > 4096:
                return await reject(scope, receive, send)
            if not message.get("more_body", False):
                break
        try:
            body = json.loads(buffer)
        except (UnicodeError, ValueError):
            return await reject(scope, receive, send)
        if not isinstance(body, dict) or not allowed_body(body):
            return await reject(scope, receive, send)
        returned = False

        async def replay():
            nonlocal returned
            if not returned:
                returned = True
                return {"type": "http.request", "body": bytes(buffer), "more_body": False}
            return await receive()

        return await api(scope, replay, send)

    return serve


def pin(name, length):
    value = os.environ.get(name, "")
    fixture.require(re.fullmatch(rf"[a-f0-9]{{{length}}}", value), "Exact matching application pin required")
    return value


if __name__ == "__main__":
    try:
        fixture.APP_COMMIT = pin("ILDR_MATCH_APP_COMMIT", 40)
        fixture.APP_TREE = pin("ILDR_MATCH_APP_TREE", 40)
        fixture.MIGRATION_HEAD = pin("ILDR_MATCH_MIGRATION", 12)
        fixture.migrate_and_seed = migrate_and_seed
        fixture.confined_api = confined_matching_api
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
