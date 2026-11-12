"""Exercise a synthetic room-tour submission and Admin review on disposable PostgreSQL."""
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import sys
import traceback
from unittest.mock import patch
from urllib import request as urllib_request
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_service_visit_fixture as visit


MIGRATION_HEAD = "d8f3a7c2e615"
PHOTO_ORIGIN = "https://synthetic-room-tour.supabase.co"
GALLERY = PHOTO_ORIGIN + "/storage/v1/object/public/listing-images/fixture-owner/"
IMAGES = [GALLERY + name for name in ("exterior-cover.jpg?", "living-room.jpg", "primary-bedroom.jpg")]
TITLE = "Synthetic villa for room photo tour"
ACCOUNTS = {"owner": "owner", "admin": "admin", "outsider": "buyer"}
REVIEW_PATH = "/admin/listing-edits"
NO_STORE = {"Cache-Control": "no-store"}


def migrate_and_seed():
    import bootstrap_db
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from database import SessionLocal
    from models.listing import Listing
    from models.user import User
    from utils.security import hash_password

    visit.advance("bootstrap_database")
    bootstrap_db.main()
    visit.advance("apply_alembic")
    command.upgrade(Config("alembic.ini"), "head")
    visit.advance("seed_listing_room_tour")
    accounts = {key: {"email": f"browser-room-tour-{key}@example.com", "password": secrets.token_urlsafe(24)}
                for key in ACCOUNTS}
    identities = {key: uuid4() for key in ACCOUNTS}
    listing_id = uuid4()
    with SessionLocal() as db:
        visit.require(db.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [MIGRATION_HEAD]
                      and db.query(User).count() == db.query(Listing).count() == 0,
                      "Fresh reviewed schema required")
        for key, role in ACCOUNTS.items():
            db.add(User(id=identities[key], role=role, status="active", email=accounts[key]["email"],
                        email_verified=True, display_name=f"Synthetic room tour {key}",
                        password_hash=hash_password(accounts[key]["password"])))
        db.flush()
        db.add(Listing(id=listing_id, title=TITLE, type="villa", transaction="rent", price=1000, currency="USD",
                       location="Synthetic listing area", status="active", images=IMAGES,
                       owner_id=identities["owner"], submitted_by=identities["owner"]))
        db.commit()
    return {"accounts": accounts, "roomTour": {"listingId": str(listing_id), "title": TITLE,
                                             "images": IMAGES, "photoOrigin": PHOTO_ORIGIN}}, listing_id, identities


def verify_proposal(proposal):
    from schemas.listing_room_tour import RoomPhotoTour

    model = RoomPhotoTour.model_validate(proposal)
    visit.require(len(model.groups) == 2 and all(len(group.photos) == 1 for group in model.groups)
                  and {group.photos[0].url for group in model.groups} == set(IMAGES[1:])
                  and model.model_dump(mode="json") == proposal, "Exact two-room interior proposal required")


def read_state(listing_id, ids, expected_status, proposal=None):
    from database import SessionLocal
    from models.activity_log import ActivityLog
    from models.inquiry import Inquiry
    from models.lead import Lead
    from models.lead_delivery import LeadDeliveryJob
    from models.listing import Listing
    from models.listing_edit import ListingEdit
    from models.listing_event import ListingEvent
    from utils.listing_room_tour import gallery_digest

    approved = expected_status == "approved"
    digest = gallery_digest(IMAGES)
    with SessionLocal() as db:
        listing = db.query(Listing).one()
        edit = db.query(ListingEdit).one()
        visit.require(listing.id == listing_id and listing.title == TITLE and listing.status == "active"
                      and listing.owner_id == listing.submitted_by == ids["owner"] and listing.images == IMAGES
                      and listing.share_image_url is None and listing.approved_by is None,
                      "Synthetic listing and exterior cover must be preserved")
        visit.require(edit.listing_id == listing_id and edit.submitted_by == ids["owner"]
                      and edit.room_photo_tour_base_gallery_sha256 == digest and edit.status == expected_status
                      and edit.reviewed_by == (ids["admin"] if approved else None)
                      and (edit.reviewed_at is not None) == approved
                      and edit.rejection_reason is None and set(edit.proposed_data) == {"room_photo_tour"},
                      "Exact single synthetic edit required")
        stored = edit.proposed_data["room_photo_tour"]
        verify_proposal(stored)
        visit.require(proposal is None or stored == proposal, "Review must preserve the submitted room metadata")
        expected_snapshot = {"gallery_sha256": digest, "tour": stored} if approved else None
        visit.require(listing.room_photo_tour_snapshot == expected_snapshot, "Exact published snapshot required")

        expected_events = [("edit_submitted", ids["owner"])]
        if approved:
            expected_events.append(("edit_approved", ids["admin"]))
        events = db.query(ListingEvent).order_by(ListingEvent.created_at).all()
        visit.require(all(event.listing_id == listing_id for event in events)
                      and [(event.event_type, event.actor_id) for event in events] == expected_events
                      and (not approved or events[-1].snapshot_after == {"room_photo_tour": stored}),
                      "Exact scoped listing history required")
        visit.require(db.query(ActivityLog).count() == int(approved)
                      and not any(db.query(model).count() for model in (Inquiry, Lead, LeadDeliveryJob)),
                      "No synthetic lead, inquiry or delivery should be created")
        return str(edit.id), stored


def guarded_application(app, listing_id, ids, observed, refused):
    from fastapi.responses import JSONResponse
    from starlette.requests import Request
    from utils.auth import _decode_token

    users = {str(identity): role for role, identity in ids.items()}
    detail = f"/listings/{listing_id}"
    editor = detail + "/room-photo-tour"

    async def wrapped(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        request = Request(scope, receive)
        path, method = request.url.path, request.method
        token = request.cookies.get("ildr_token")
        bearer = request.headers.get("authorization")
        role = users.get(_decode_token(token)) if token and not bearer else None
        origin = request.headers.get("origin")
        pending = observed.get("pending")
        approved = observed.get("approved", False)
        stage = (method, path, role) == ("PUT", editor, "owner") and pending is None
        approve = (pending is not None and not approved and observed.get("admin_reviewed") is True
                   and (method, path, role) == ("POST", f"{REVIEW_PATH}/{pending[0]}/approve", "admin"))
        if (method not in {"GET", "HEAD", "OPTIONS"} and (method, path) != ("POST", "/auth/login")
                and (origin != visit.DASHBOARD_ORIGIN or not (stage or approve))):
            refused.append(True)
            return await JSONResponse({"detail": "Fixture mutation refused"}, status_code=403,
                                      headers=NO_STORE)(scope, receive, send)
        if approve:
            # Replay exactly the acknowledged body through FastAPI's normal validation.
            upstream = receive
            buffered = bytearray()
            async for chunk in request.stream():
                buffered.extend(chunk)
                visit.require(len(buffered) <= 128, "Bounded review acknowledgement required")
            payload = bytes(buffered)
            acknowledgement = json.loads(payload)
            visit.require(isinstance(acknowledgement, dict) and set(acknowledgement) == {"room_photo_tour_reviewed"}
                          and acknowledgement["room_photo_tour_reviewed"] is True,
                          "Exact explicit review acknowledgement required")

            async def replay():
                nonlocal payload
                if payload is None:
                    return await upstream()
                message = {"type": "http.request", "body": payload, "more_body": False}
                payload = None
                return message

            receive = replay

        phase = None
        if (method, path) == ("GET", detail) and not token and not bearer:
            phase = "public_exact_after" if approved else "public_null_pending" if pending else "public_null_before"
        queue = (method, path, role) == ("GET", REVIEW_PATH, "admin") and pending is not None and not approved
        if not (phase or stage or approve or queue):
            return await app(scope, receive, send)

        status = None
        headers = {}
        body = bytearray()
        messages = []

        async def observe(message):
            nonlocal status, headers
            if message["type"] == "http.response.start":
                status, headers = message["status"], dict(message["headers"])
            elif message["type"] == "http.response.body":
                body.extend(message.get("body", b""))
                visit.require(len(body) <= 262144, "Bounded synthetic response required")
            messages.append(message)

        await app(scope, receive, observe)
        if stage and status == 202:
            visit.require(headers.get(b"cache-control") == b"no-store"
                          and json.loads(body)["listing_id"] == str(listing_id), "Exact stage receipt required")
            observed["pending"] = read_state(listing_id, ids, "pending")
        if queue and status == 200:
            rows = json.loads(body)
            visit.require(headers.get(b"cache-control") == b"no-store" and len(rows) == 1
                          and rows[0]["id"] == pending[0] and rows[0]["listing_id"] == str(listing_id)
                          and rows[0]["status"] == "pending" and rows[0]["current_data"]["images"] == IMAGES
                          and rows[0]["current_data"]["room_photo_tour"] is None
                          and rows[0]["proposed_data"] == {"room_photo_tour": pending[1]},
                          "Exact Admin review projection required")
            observed["admin_reviewed"] = True
        if approve and status == 204:
            visit.require(headers.get(b"cache-control") == b"no-store"
                          and read_state(listing_id, ids, "approved", pending[1])[0] == pending[0],
                          "Only the reviewed edit may be approved")
            observed["approved"] = True
        if phase and status == 200:
            item = json.loads(body)
            expected = pending[1] if approved else None
            visit.require(headers.get(b"cache-control") == b"no-store" and item["id"] == str(listing_id)
                          and item["images"] == IMAGES and item["room_photo_tour"] == expected,
                          "Exact anonymous room tour projection required")
            observed[phase] = True
        for message in messages:
            await send(message)

    return wrapped


def run():
    commit = os.environ.get("ILDR_LISTING_ROOM_TOUR_APP_COMMIT", "")
    tree = os.environ.get("ILDR_LISTING_ROOM_TOUR_APP_TREE", "")
    visit.require(all(re.fullmatch(r"[a-f0-9]{40}", value) for value in (commit, tree))
                  and os.environ.get("ILDR_LISTING_ROOM_TOUR_MIGRATION_HEAD") == MIGRATION_HEAD,
                  "Exact room tour app source required")
    visit.APP_COMMIT, visit.APP_TREE, visit.MIGRATION_HEAD = commit, tree, MIGRATION_HEAD
    application = visit.verified_checkout()
    visit.advance("claim_database")
    url = visit.claim_disposable_database()
    visit.advance("configure_application")
    fixture_file = visit.FIXTURE_FILE
    visit.require(fixture_file.parent.resolve(strict=True) == fixture_file.parent and not fixture_file.exists()
                  and not fixture_file.is_symlink(), "Empty private fixture directory required")
    visit.configure_application(application, url)
    os.environ.update(SUPABASE_URL=PHOTO_ORIGIN, STORAGE_BUCKET="listing-images")
    visit.advance("load_application")
    import httpx
    import requests
    import uvicorn

    transport_attempts, refused = [], []
    observed = {}

    def refuse_transport(*_args, **_kwargs):
        transport_attempts.append(True)
        raise RuntimeError("Fixture provider transport is disabled")

    with (
        patch.object(httpx.HTTPTransport, "handle_request", refuse_transport),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse_transport),
        patch.object(requests.Session, "send", refuse_transport),
        patch.object(urllib_request, "urlopen", refuse_transport),
    ):
        from main import app

        fixture, listing_id, ids = migrate_and_seed()
        visit.advance("write_fixture")
        fd = os.open(fixture_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        owned = os.fstat(fd)
        previous_handlers = {}
        try:
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                json.dump(fixture, stream, ensure_ascii=True)
                stream.write("\n")
            visit.advance("serve_application")
            server = uvicorn.Server(uvicorn.Config(guarded_application(app, listing_id, ids, observed, refused),
                host=visit.HOST, port=visit.API_PORT, lifespan="off", access_log=False, log_level="warning"))

            def finish_shutdown(_number, _frame):
                server.should_exit = True

            for number in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[number] = signal.signal(number, finish_shutdown)
            server.run()
            visit.require(server.started, "Synthetic room tour API failed to start")
            visit.advance("verify_listing_room_tour")
            phases = ("public_null_before", "public_null_pending", "public_exact_after", "admin_reviewed", "approved")
            visit.require(all(observed.get(phase) is True for phase in phases) and observed.get("pending")
                          and not refused and not transport_attempts, "Exact private review sequence required")
            pending_id, proposal = observed["pending"]
            visit.require(read_state(listing_id, ids, "approved", proposal)[0] == pending_id,
                          "Final approved listing readback required")
        finally:
            try:
                if fixture_file.exists() or fixture_file.is_symlink():
                    current = fixture_file.lstat()
                    if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                        fixture_file.unlink()
            finally:
                for number, handler in previous_handlers.items():
                    signal.signal(number, handler)

    visit.require(not fixture_file.exists() and not fixture_file.is_symlink(), "Synthetic login file cleanup failed")
    result = {"status": "PASS", "public_null_before": True, "public_null_pending": True, "public_exact_after": True,
              "owner_staged": True, "admin_reviewed": True, "admin_approved": True, "approved_gallery_refs": 2,
              "cover_preserved": True, "db_edits": 1, "db_events": 2,
              "unexpected_mutations": len(refused), "external_transport_attempts": len(transport_attempts)}
    print("LISTING_ROOM_TOUR_BROWSER_FIXTURE_RESULT " + json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        error_type = type(error).__name__
        frames = [frame.lineno for frame in traceback.extract_tb(error.__traceback__)
                  if frame.filename in {__file__, visit.__file__}]
        print("BROWSER_API_FIXTURE_FAILURE " + json.dumps({"stage": visit.stage,
              "error_type": error_type if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", error_type) else "UnhandledError",
              "fixture_line": frames[-1] if frames else None}), flush=True)
        raise SystemExit(1) from None
