"""Confine the real communications application to synthetic loopback journeys."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import secrets
import sys
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_admin_share_fixture as fixture

APP_COMMIT = "7f63fec927f75af3f901f74297973253ad5c743b"
APP_TREE = "1a87c3d4c96e2df0c956e18f0f26c982e7ef3289"
MIGRATION = "d721f0b6a84c"
LISTING_ID = UUID("10000000-0000-0000-0000-000000000001")
TEAM = [f"communications-team-{i}@example.com" for i in range(1, 5)]
SIGNUPS = {f"communications-signup-{locale}@example.com" for locale in ("en", "es")}
CONTACTS = {f"communications-{kind}-{locale}@example.com" for kind in ("buy", "sell", "rent", "property")
            for locale in ("en", "es")}
ALLOWED_EMAILS = set(TEAM) | SIGNUPS | CONTACTS | {
    "browser-admin@example.com", "browser-owner@example.com", "browser-buyer@example.com",
    "browser-realtor@example.com", "browser-share+ci@example.com", "browser-foreign@example.com",
    "browser-legacy@example.com", "communications-fallback@example.com",
}
CONTROL = secrets.token_urlsafe(24)
CALLS = []
original_configure = fixture.configure_application


def configure(application, database_url):
    original_configure(application, database_url)
    import os
    os.environ.update(LEAD_DELIVERY_ENABLED="true", CUSTOMER_MESSAGES_ENABLED="true",
                      PROPERTY_INQUIRY_REQUIRE_CONSENT="true", GHL_WEBHOOK_SECRET="", EMAIL_FROM="fixture@example.com")
    # This functional journey excludes rate-window acceptance. Keep multiple
    # real forms on one loopback IP deterministic without changing source limits.
    from utils.limiter import limiter
    limiter.enabled = False


def fake_mail():
    import resend
    from config import settings

    def send(payload, *_args, **_kwargs):
        targets = payload.get("to") if isinstance(payload, dict) else None
        if isinstance(targets, str):
            targets = [targets]
        fixture.require(isinstance(targets, list) and 1 <= len(targets) <= 4 and
                        all(value in ALLOWED_EMAILS for value in targets), "Synthetic mail recipients required")
        fixture.require(isinstance(payload.get("html"), str) and len(payload["html"]) < 131072,
                        "Bounded synthetic mail required")
        CALLS.append(payload)
        return {"id": "synthetic-communications-acceptance"}

    resend.Emails.send = send
    settings.resend_api_key = "synthetic-sdk-boundary-only"


def migrate_and_seed():
    import bootstrap_db
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from database import SessionLocal
    from models.inquiry import Inquiry
    from models.lead import Lead
    from models.lead_delivery import LeadDeliveryJob
    from models.listing import Listing
    from models.site_settings import SiteSettings
    from models.user import User
    from services.lead_delivery import enqueue_lead_delivery
    from utils.security import hash_password

    fixture.advance("bootstrap_database")
    bootstrap_db.main()
    fixture.advance("apply_alembic")
    command.upgrade(Config("alembic.ini"), "head")
    fake_mail()
    accounts = {role: {"email": f"browser-{role}@example.com", "password": secrets.token_urlsafe(24)}
                for role in ("admin", "owner", "buyer", "realtor")}
    with SessionLocal.begin() as db:
        fixture.require(db.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [MIGRATION],
                        "Exact narrow migration required")
        fixture.require(all(db.query(model).count() == 0 for model in (User, Lead, Inquiry, Listing, LeadDeliveryJob)),
                        "Empty synthetic database required")
        configuration = db.query(SiteSettings).all()
        fixture.require(len(configuration) == 1 and configuration[0].id == 1 and configuration[0].data == {},
                        "Exact migrated settings required")
        configuration[0].data = {"notify_email": ",".join(TEAM)}
        users = {}
        for role, account in accounts.items():
            users[role] = User(id=uuid4(), email=account["email"], password_hash=hash_password(account["password"]),
                               role=role, status="active", display_name=f"Browser {role.title()}", email_verified=True)
            db.add(users[role])
        db.flush()
        listing = Listing(id=LISTING_ID, title="Browser communications villa", type="villa", transaction="sale",
                          price=250000, currency="USD", location="Cabarete", status="active", images=[],
                          bedrooms=3, bathrooms=3, submitted_by=users["owner"].id, owner_id=users["owner"].id,
                          assigned_realtor_id=users["realtor"].id)
        foreign = Listing(id=uuid4(), title="Foreign private property sentinel", type="villa", transaction="sale",
                          price=1000, currency="USD", location="Cabarete", status="active", images=[],
                          submitted_by=users["admin"].id)
        db.add_all((listing, foreign))
        db.flush()
        shared = Lead(id=uuid4(), type="property_inquiry", name="Browser Share Contact",
                      email="browser-share+ci@example.com", phone="+12025550119",
                      message="Synthetic private lead note sentinel", property_id=listing.id,
                      assigned_realtor_id=users["realtor"].id, from_user_id=users["buyer"].id, status="new")
        other = Lead(id=uuid4(), type="property_inquiry", name="Foreign private contact sentinel",
                     email="browser-foreign@example.com", message="Foreign private note sentinel", property_id=foreign.id)
        legacy = Inquiry(id=uuid4(), listing_id=listing.id, name="Browser Legacy Contact",
                         email="browser-legacy@example.com", message="Synthetic historical inquiry sentinel",
                         from_user_id=users["buyer"].id)
        db.add_all((shared, other, legacy))
        db.flush()
        tracked = Lead(id=uuid4(), type="property_inquiry", name="Browser Tracked Contact",
                       email="browser-share+ci@example.com", phone="+18095551234", property_id=listing.id,
                       from_user_id=users["buyer"].id, assigned_realtor_id=users["realtor"].id, status="new",
                       tracking={"form": "property_inquiry", "page_path": f"/listing/{listing.id}/", "locale": "en",
                                 "intent": "buy", "contact_consent": True, "measurement_consent": "denied"})
        db.add(tracked)
        db.flush()
        enqueue_lead_delivery(db, tracked)
        db.flush()
        jobs = db.query(LeadDeliveryJob).filter_by(lead_id=tracked.id).all()
        fixture.require(len(jobs) == 9, "Exact tracked seed required")
        failed = next(job for job in jobs if job.step == "email_0")
        failed_es = next(job for job in jobs if job.step == "email_2")
        uncertain = next(job for job in jobs if job.step == "email_1")
        now = datetime.now(timezone.utc)
        for job in (failed, failed_es):
            job.status = "failed"
            job.attempt_count = 1
            job.safe_to_retry = True
            job.error_code = "resend_http_503"
            job.next_attempt_at = now - timedelta(seconds=60)
            job.first_attempt_at = job.last_attempt_at = now - timedelta(minutes=5)
        uncertain.status = "needs_review"
        uncertain.attempt_count = 1
        uncertain.safe_to_retry = False
        uncertain.outcome_uncertain = True
        uncertain.error_code = "provider_outcome_unknown"
        return {"scope": "f01-f03-communications-only", "accounts": accounts, "control": CONTROL,
                "shareLeadId": str(shared.id), "foreignLeadId": str(other.id), "trackedLeadId": str(tracked.id),
                "failedJobId": str(failed.id), "failedEsJobId": str(failed_es.id),
                "uncertainJobId": str(uncertain.id), "listingId": str(LISTING_ID)}


def confined_api(api):
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    async def serve(scope, receive, send):
        if scope["type"] != "http":
            return await api(scope, receive, send)
        method, path = scope["method"], scope["path"]
        if path.startswith("/__fixture/"):
            request = Request(scope, receive)
            if request.headers.get("X-ILDR-Rehearsal") != CONTROL:
                return await JSONResponse({"detail": "Denied"}, 403)(scope, receive, send)
            value = None
            if method == "GET" and re.fullmatch(r"/__fixture/verification/(en|es)", path):
                from database import SessionLocal
                from models.pending_user import PendingUser
                with SessionLocal() as db:
                    pending = db.query(PendingUser).filter_by(email=f"communications-signup-{path[-2:]}@example.com").one()
                    value = {"code": pending.verification_code}
            elif method == "POST" and path == "/__fixture/pause":
                from config import settings
                settings.lead_delivery_enabled = settings.customer_messages_enabled = False
                settings.property_inquiry_require_consent = False
                value = {"paused": True}
            if value is not None:
                return await JSONResponse(value, headers={"Cache-Control": "no-store"})(scope, receive, send)
            return await JSONResponse({"detail": "Denied"}, 405)(scope, receive, send)
        if method in {"GET", "HEAD", "OPTIONS"}:
            return await api(scope, receive, send)
        allowed = method == "POST" and path in {"/auth/login", "/auth/logout", "/auth/register", "/auth/verify", "/leads"}
        allowed = allowed or method == "POST" and bool(re.fullmatch(
            r"/admin/(?:lead-deliveries|communications)/[a-f0-9-]{36}/retry", path))
        if not allowed:
            return await JSONResponse({"detail": "Synthetic mutation refused"}, 405)(scope, receive, send)
        request = Request(scope, receive)
        raw = await request.body()
        body = None
        if len(raw) <= 8192:
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                pass
        safe = isinstance(body, dict)
        if safe and path in {"/auth/login", "/auth/register", "/auth/verify"}:
            safe = body.get("email") in ALLOWED_EMAILS
        if safe and path == "/leads":
            safe = body.get("email") in ALLOWED_EMAILS and str(body.get("name", "")).startswith("Communications ")
        if safe and path.endswith("/retry"):
            safe = body == {}
        if not safe:
            return await JSONResponse({"detail": "Synthetic input refused"}, 422)(scope, receive, send)
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
        fixture.APP_COMMIT, fixture.APP_TREE, fixture.MIGRATION_HEAD = APP_COMMIT, APP_TREE, MIGRATION
        fixture.API_PORT = 8918
        fixture.configure_application = configure
        fixture.migrate_and_seed = migrate_and_seed
        fixture.confined_api = confined_api
        fixture.run()
        print("COMMUNICATIONS_BROWSER_PROVIDER_COUNTS " + json.dumps({"fake_sdk_calls": len(CALLS), "live_calls": 0}))
    except Exception as error:
        kind = type(error).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", kind):
            kind = "UnhandledError"
        print("COMMUNICATIONS_BROWSER_API_FAILURE " + json.dumps({"stage": fixture.stage, "error_type": kind}), flush=True)
        raise SystemExit(1) from None
