"""Use the real private referral API against one owned service request."""

import json
import re
import sys
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_reviewed_service_matches_fixture as matching


fixture = matching.fixture
PREFIX = "/property-network"
IDENTIFIER = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"
request_id = None
sponsor_recipient_id = None
target_id = None
target_code = None


def migrate_and_seed():
    from database import SessionLocal
    from models.user import User
    from schemas.service_workflow import RequestCreate
    from services import service_workflow

    global request_id, sponsor_recipient_id, target_id, target_code
    data = matching.migrate_and_seed()
    fixture.advance("seed_referral")
    sponsor_code = data["serviceCodes"]["english"]
    target_code = data["serviceCodes"]["north"]
    with SessionLocal() as db:
        owner = db.query(User).filter(User.email == data["accounts"]["owner"]["email"], User.role == "owner").one()
        target_id = db.query(User.id).filter(User.user_code == target_code).scalar()
        original = RequestCreate(
            submission_id=uuid4(), listing_id=UUID(data["serviceSourceId"]),
            property_class=matching.PROPERTY_CLASS, category_id=matching.CATEGORY,
            title="Synthetic pool referral request", brief="Private fixture brief for scheduled pool maintenance.",
            area_label="Synthetic coastal area", requester_alias="Synthetic requester alias",
            recipient_user_codes=[sponsor_code], shared_brief_acknowledged=True,
            reviewed_match={"area_id": "cabarete", "language": "en"},
        )
        created = service_workflow.create_request(db, owner, original)
        fixture.require(created.get("status") == "open" and len(created.get("recipients", [])) == 1,
                        "One owned original request required")
        recipient = created["recipients"][0]
        fixture.require(recipient["user_code"] == sponsor_code and recipient["status"] == "pending"
                        and target_id is not None, "Verified sponsor and target required")
        request_id = created["id"]
        sponsor_recipient_id = recipient["id"]
    return {**data, "referralRequestId": request_id, "referralSponsorRecipientId": sponsor_recipient_id}


def owned_referral(referral_id):
    from database import SessionLocal
    from models.service_referral import NetworkServiceReferral

    with SessionLocal() as db:
        return db.query(NetworkServiceReferral.id).filter(
            NetworkServiceReferral.id == referral_id, NetworkServiceReferral.request_id == UUID(request_id),
            NetworkServiceReferral.provider_id == target_id,
        ).scalar() is not None


def confined_referral_api(api):
    from fastapi.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse

    fixture.require(request_id is not None and sponsor_recipient_id is not None and target_id is not None,
                    "An exact synthetic request and named target are required")
    baseline = matching.base_api(api)
    original = f"{PREFIX}/service-requests/{request_id}"
    action = re.compile(rf"{PREFIX}/service-referrals/({IDENTIFIER})/(?:offer|accept|revoke)")

    async def reject(scope, receive, send):
        response = JSONResponse({"detail": "Mutation unavailable in browser rehearsal"}, status_code=405,
                                headers={"Cache-Control": "no-store"})
        return await response(scope, receive, send)

    async def serve(scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await baseline(scope, receive, send)
        path = scope["path"]
        if path == f"{original}/recipients/{sponsor_recipient_id}/accept":
            return await api(scope, receive, send)
        match = action.fullmatch(path)
        if match is not None and await run_in_threadpool(owned_referral, UUID(match[1])):
            return await api(scope, receive, send)
        if path != f"{original}/referrals":
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
        if not isinstance(body, dict) or type(body.get("provider_user_code")) is not int or body["provider_user_code"] != target_code:
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


if __name__ == "__main__":
    try:
        fixture.APP_COMMIT = matching.pin("ILDR_REFERRAL_APP_COMMIT", 40)
        fixture.APP_TREE = matching.pin("ILDR_REFERRAL_APP_TREE", 40)
        fixture.MIGRATION_HEAD = matching.pin("ILDR_REFERRAL_MIGRATION", 12)
        fixture.migrate_and_seed = migrate_and_seed
        fixture.confined_api = confined_referral_api
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
