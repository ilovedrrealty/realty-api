"""Exercise the exact old/new apps against one nonce-owned upgrade database.

Contact-bearing fixtures and snapshots remain in memory. Each invocation emits
one fixed aggregate marker. Application startup workers are never entered.
"""

import asyncio
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from hashlib import sha1, sha256
import io
import json
import logging
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from uuid import UUID


LIVE = Path("/live")
CANDIDATE = Path("/application")
ROLLBACK = Path("/rollback")
LIVE_COMMIT = "7f63fec927f75af3f901f74297973253ad5c743b"
LIVE_TREE = "1a87c3d4c96e2df0c956e18f0f26c982e7ef3289"
APP_COMMIT = "796f25ff52724db56313916e129df4b252a682fd"
APP_TREE = "10d76a3de555b567fde93a74b5f90b8d9ba76978"
MIGRATION = "alembic/versions/d721f0b6a84c_live_transactional_notifications.py"
HEAD = "b19d8e4c720a"
ROOTS = {"seed_live": LIVE, "seed_candidate": CANDIDATE, "verify_old": ROLLBACK}
ROLES = ("admin", "owner", "buyer", "realtor")
USER_IDS = {role: UUID(int=100 + index) for index, role in enumerate(ROLES, 1)}
LISTING_ID = UUID(int=201)
HISTORICAL_LEADS = (UUID(int=301), UUID(int=302))
HISTORICAL_INQUIRY = UUID(int=401)
HISTORICAL_BOOKING = UUID(int=501)
TEAM = tuple(f"f01-team-{number}@example.com" for number in range(1, 5))
ROLE_EMAILS = {role: f"f01-{role}@example.com" for role in ROLES}
SIGNUP_EMAIL = "f01-new-signup@example.com"
EMAIL_ONLY = "f01-email-only@example.com"
PROPERTY_EMAIL = "f01-property@example.com"
OLD_EMAIL = "f01-old-intake@example.com"
OLD_NAME = "F01 old client intake"
PHONE_NAME = "F01 phone only"
EMAIL_NAME = "F01 email only"
PROPERTY_NAME = "F01 consented property"
ORIGIN = "http://127.0.0.1:4175"
PROVIDER_KEYS = (
    "RESEND_API_KEY", "GHL_API_KEY", "GHL_LOCATION_ID", "GHL_WEBHOOK_SECRET",
    "SUPABASE_SERVICE_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_WEBHOOK_ID",
)


class RehearsalFailure(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise RehearsalFailure(reason)


def git(root, *arguments):
    return subprocess.check_output(
        ["git", "--no-optional-locks", "-c", f"safe.directory={root}", "-C", str(root), *arguments],
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
             "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"},
        stderr=subprocess.DEVNULL, timeout=15,
    )


def verified_checkout(root, commit, tree):
    require(root.resolve(strict=True) == root and os.statvfs(root).f_flag & os.ST_RDONLY,
            "read_only_source_required")
    require(not (root / ".env").exists() and not (root / ".env").is_symlink(), "source_env_refused")
    require(git(root, "rev-parse", "HEAD", "HEAD^{tree}").decode().splitlines() == [commit, tree],
            "exact_source_required")
    require(not git(root, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "clean_source_required")


def verified_rollback():
    verified_checkout(LIVE, LIVE_COMMIT, LIVE_TREE)
    verified_checkout(CANDIDATE, APP_COMMIT, APP_TREE)
    require(ROLLBACK.resolve(strict=True) == ROLLBACK and os.statvfs(ROLLBACK).f_flag & os.ST_RDONLY,
            "read_only_source_required")
    entries = git(LIVE, "ls-tree", "-rz", LIVE_COMMIT).split(b"\0")
    require(entries[-1] == b"", "exact_source_required")
    names = set()
    for entry in entries[:-1]:
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        name = raw_name.decode("utf-8")
        path = ROLLBACK / name
        require(kind == b"blob" and mode in (b"100644", b"100755") and path.is_file()
                and not path.is_symlink(), "rollback_source_required")
        payload = path.read_bytes()
        require(sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest().encode() == object_id,
                "rollback_source_required")
        names.add(name)
    additions = git(CANDIDATE, "diff", "--name-only", "--diff-filter=A", LIVE_COMMIT, APP_COMMIT,
                    "--", "alembic/versions").decode().splitlines()
    require(len(additions) == 32, "rollback_revision_count_required")
    for name in additions:
        require(name.startswith("alembic/versions/") and name.endswith(".py")
                and (ROLLBACK / name).read_bytes() == git(CANDIDATE, "cat-file", "blob", APP_COMMIT + ":" + name),
                "rollback_migration_required")
        names.add(name)
    actual = set()
    for path in ROLLBACK.rglob("*"):
        require(not path.is_symlink(), "rollback_source_required")
        if path.is_file():
            actual.add(path.relative_to(ROLLBACK).as_posix())
    require(actual == names, "rollback_source_required")


def guard(mode):
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true" and
            os.environ.get("GITHUB_REPOSITORY") == "ilovedrrealty/realty-api", "hosted_isolation_required")
    require({path.name for path in Path("/sys/class/net").iterdir()} == {"lo"}, "loopback_only_required")
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    require(re.fullmatch(r"[a-f0-9]{32}", nonce), "owned_database_required")
    database = "ildr_f01_upgrade_" + nonce
    require(os.environ.get("DATABASE_URL") == f"postgresql+psycopg2://postgres@127.0.0.1:55449/{database}",
            "owned_database_required")
    require(all(not os.environ.get(key) for key in PROVIDER_KEYS), "provider_credentials_refused")
    root = ROOTS[mode]
    require(Path.cwd() == root, "exact_source_required")
    if mode == "verify_old":
        verified_rollback()
    elif mode == "seed_live":
        verified_checkout(root, LIVE_COMMIT, LIVE_TREE)
    else:
        verified_checkout(root, APP_COMMIT, APP_TREE)

    import psycopg2

    with closing(psycopg2.connect(
        host="127.0.0.1", port=55449, dbname=database, user="postgres", password="",
        connect_timeout=4, options="-cstatement_timeout=10000",
    )) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT current_database(), host(inet_server_addr()), inet_server_port(),
                current_setting('server_version_num')::integer, shobj_description(oid,'pg_database')
                FROM pg_database WHERE datname=current_database()""")
            require(cursor.fetchone() == (
                database, "127.0.0.1", 55449, 170006, f"ildr-live-communications-ci:{nonce}:upgrade",
            ), "owned_database_required")
            cursor.execute("SELECT version_num FROM alembic_version")
            expected_head = "d721f0b6a84c" if mode == "seed_live" else HEAD
            require(cursor.fetchall() == [(expected_head,)], "exact_migration_required")
    return root


def configure(root, candidate):
    os.environ.update({
        "JWT_SECRET": secrets.token_urlsafe(48), "ENVIRONMENT": "development", "COOKIE_DOMAIN": "",
        "ALLOWED_ORIGINS": ORIGIN, "LANDING_URL": "http://127.0.0.1:8917",
        "RESEND_API_KEY": "", "EMAIL_FROM": "rehearsal@example.com", "NOTIFY_EMAIL": "",
        "GHL_ENABLED": "false", "GHL_API_KEY": "", "GHL_LOCATION_ID": "", "GHL_WEBHOOK_SECRET": "",
        "LEAD_DELIVERY_ENABLED": "true" if candidate else "false",
        "CUSTOMER_MESSAGES_ENABLED": "true" if candidate else "false",
        "PROPERTY_INQUIRY_REQUIRE_CONSENT": "false", "SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": "",
        "PAYPAL_CLIENT_ID": "", "PAYPAL_CLIENT_SECRET": "", "PAYPAL_WEBHOOK_ID": "",
    })
    sys.path.insert(0, str(root))
    logging.disable(logging.CRITICAL)


def fake_provider():
    import resend
    from config import settings

    allowed = set(TEAM) | set(ROLE_EMAILS.values()) | {SIGNUP_EMAIL, EMAIL_ONLY, PROPERTY_EMAIL, OLD_EMAIL}
    calls = []

    def send(payload, *arguments, **keywords):
        require(isinstance(payload, dict), "fake_provider_payload")
        targets = payload.get("to")
        if isinstance(targets, str):
            targets = [targets]
        require(isinstance(targets, list) and 1 <= len(targets) <= 4 and
                all(isinstance(value, str) and value in allowed for value in targets), "fake_provider_recipient")
        require(isinstance(payload.get("subject"), str) and isinstance(payload.get("html"), str),
                "fake_provider_payload")
        require(len(payload["subject"]) <= 300 and len(payload["html"]) <= 131072, "fake_provider_payload")
        calls.append(payload)
        return {"id": "ci-owned-fake-acceptance"}

    # This token is a fixed fake inside a verified loopback-only subprocess.
    # The SDK boundary is replaced before any actual endpoint can use it.
    resend.Emails.send = send
    settings.resend_api_key = "ci-owned-fake-provider"
    return calls


def accounts(db):
    from models.user import User

    rows = {role: db.get(User, identifier) for role, identifier in USER_IDS.items()}
    require(all(row is not None and row.role == role and row.email == ROLE_EMAILS[role] and
                row.status == "active" and row.email_verified for role, row in rows.items()),
            "historical_accounts_required")
    return rows


def seed_live():
    from database import SessionLocal
    from models.booking import Booking
    from models.inquiry import Inquiry
    from models.lead import Lead
    from models.listing import Listing
    from models.site_settings import SiteSettings
    from models.trade_contact import TradeContact, TradeContactSource
    from models.user import User
    from utils.security import hash_password

    history = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with SessionLocal.begin() as db:
        require(all(db.query(model).count() == 0 for model in (
            User, Lead, Inquiry, Booking, Listing, SiteSettings, TradeContact, TradeContactSource,
        )),
                "empty_history_required")
        for role, identifier in USER_IDS.items():
            db.add(User(
                id=identifier, role=role, status="active", email=ROLE_EMAILS[role],
                display_name="F01 " + role, email_verified=True,
                password_hash=hash_password(secrets.token_urlsafe(24)), created_at=history,
            ))
        db.flush()
        db.add(Listing(
            id=LISTING_ID, title="F01 synthetic public rental", type="villa", transaction="rent",
            price=3000, price_per_day=100, currency="USD", location="Cabarete", status="active",
            submitted_by=USER_IDS["owner"], owner_id=USER_IDS["owner"],
            assigned_realtor_id=USER_IDS["realtor"], images=[], bedrooms=3, bathrooms=3,
        ))
        db.flush()
        start = date.today() - timedelta(days=60)
        end = start + timedelta(days=3)
        for identifier, kind, name, assigned in (
            (HISTORICAL_LEADS[0], "property_inquiry", "F01 historical inquiry lead", USER_IDS["realtor"]),
            (HISTORICAL_LEADS[1], "booking", "F01 historical booking lead", USER_IDS["owner"]),
        ):
            message = "F01 historical message" if kind != "booking" else f"Check-in: {start}, Check-out: {end}, Guests: 2"
            db.add(Lead(
                id=identifier, type=kind, name=name, email=ROLE_EMAILS["buyer"], message=message,
                property_id=LISTING_ID, from_user_id=USER_IDS["buyer"],
                assigned_realtor_id=assigned, status="assigned", created_at=history,
            ))
        db.flush()
        db.add(Inquiry(
            id=HISTORICAL_INQUIRY, listing_id=LISTING_ID, name="F01 historical inquiry",
            email=ROLE_EMAILS["buyer"], message="F01 original inquiry note",
            from_user_id=USER_IDS["buyer"], created_at=history,
        ))
        db.add(Booking(
            id=HISTORICAL_BOOKING, listing_id=LISTING_ID, buyer_id=USER_IDS["buyer"],
            lead_id=HISTORICAL_LEADS[1], check_in=str(start), check_out=str(end), guests=2,
            total_price=300, notes="F01 historical booking note", status="confirmed", created_at=history,
        ))
        db.add(SiteSettings(id=1, data={"notify_email": ",".join(TEAM), "f01_history_sentinel": "preserve"}))
        db.add(TradeContact(
            id=UUID(int=601), identity_key=sha256(b"F01 historical trade").hexdigest(),
            name="F01 historical trade", category="electrical", province="Puerto Plata", locality="Cabarete",
            details={"email": "f01-trade@example.com", "fixture": True}, created_at=history, updated_at=history,
        ))
        db.flush()
        db.add(TradeContactSource(
            id=UUID(int=602), contact_id=UUID(int=601), source_key=sha256(b"F01 historical source").hexdigest(),
            evidence={"url": "https://example.com/synthetic-f01-trade", "fixture": True},
            created_at=history, updated_at=history,
        ))
    seed_current_live_records(history)
    return {"historical_users": 4, "historical_leads": 3, "historical_inquiries": 1,
            "historical_bookings": 1, "configured_team_recipients": 4,
            "retained_accepted_jobs": 9, "worker_rows": 1, "owner_trade_contacts": 82,
            "trade_contacts": 85, "trade_sources": 86, "private_profiles": 46, "cms_version": 7}


def seed_current_live_records(history):
    """Synthetic equivalents of the current retained live rows, never real contacts."""
    from unittest.mock import patch
    from config import settings
    from database import SessionLocal
    from models.lead import Lead
    from models.lead_delivery import LeadDeliveryWorkerState
    from models.professional_profile import ProfessionalProfile, ProfessionalProfileRevision
    from models.trade_contact import TradeContact, TradeContactSource
    from models.website_settings import WebsiteSettings, WebsiteSettingsRevision
    from schemas.professionals import ProfessionalDraft
    from schemas.website_settings import WebsiteContentSettings
    from services import lead_delivery

    with SessionLocal.begin() as db:
        # Plus the existing public row in seed_live: 82 protected owner rows,
        # three public contacts, and one independently observed second source.
        for index in range(84):
            identifier = UUID(int=1000 + index)
            owner = index < 82
            db.add(TradeContact(id=identifier, identity_key=sha256(f"network-trade-{index}".encode()).hexdigest(),
                name=f"Synthetic retained business {index}", category="Synthetic property services",
                province="Puerto Plata", locality="Cabarete",
                details={"verification_status": "owner_source" if owner else "public_source", "notes": ["retain", index]},
                created_at=history, updated_at=history))
            db.flush()
            db.add(TradeContactSource(id=UUID(int=2000 + index), contact_id=identifier,
                source_key=sha256(f"network-source-{index}".encode()).hexdigest(),
                evidence={"source_url": "https://example.invalid/synthetic", "source_details": {"notes": ["retain", index]}},
                created_at=history, updated_at=history))
        db.add(TradeContactSource(id=UUID(int=2999), contact_id=UUID(int=1083),
            source_key=sha256(b"network-second-source").hexdigest(), evidence={"notes": ["independent", "retain"]},
            created_at=history, updated_at=history))
        for index in range(46):
            identifier = UUID(int=3000 + index)
            draft = ProfessionalDraft(business_name=f"Synthetic private business {index}").model_dump(mode="json")
            db.add(ProfessionalProfile(id=identifier, user_id=None, version=1, draft_data=draft,
                published_data=None, review_status="draft", listing_status="hidden", published_at=None,
                created_at=history, updated_at=history))
            db.flush()
            db.add(ProfessionalProfileRevision(id=UUID(int=4000 + index), profile_id=identifier, version=1,
                action="create_managed", snapshot={"draft_data": draft, "listing_status": "hidden"},
                actor_id=USER_IDS["admin"], created_at=history))
        links = [{"label": {"en": en, "es": es}, "route": route, "enabled": True} for en, es, route in (
            ("Home", "Inicio", "landing"), ("Buying", "Comprar", "buying"),
            ("Selling", "Vender", "selling"), ("Renting", "Alquilar", "rentals"),
            ("Team", "Equipo", "team"), ("Properties", "Propiedades", "search"),
            ("Blog", "Blog", "blog"), ("Contact", "Contacto", "contact"))]
        cms = WebsiteContentSettings(navigation=links).model_dump(mode="json")
        db.add(WebsiteSettings(id=1, version=7, data=cms, updated_by=USER_IDS["admin"], updated_at=history))
        for version in range(8):
            db.add(WebsiteSettingsRevision(version=version, snapshot=cms, actor_id=USER_IDS["admin"], created_at=history))
        # The actual enqueue function creates its immutable nine payloads. Status
        # metadata models the retained accepted canary, not a provider-delivery claim.
        canary = Lead(id=UUID(int=303), type="buyer_interest", name="Synthetic retained canary",
            email=ROLE_EMAILS["buyer"], message="Synthetic retained owner canary", from_user_id=USER_IDS["buyer"],
            tracking={"contact_consent": True, "locale": "en", "intent": "buy"}, created_at=history)
        with patch.object(settings, "lead_delivery_enabled", True), patch.object(settings, "customer_messages_enabled", True), \
                patch.object(settings, "ghl_enabled", True), patch.object(settings, "ghl_location_id", "synthetic-location"):
            db.add(canary)
            db.flush()
            jobs = lead_delivery.enqueue_lead_delivery(db, canary, recipients=list(TEAM), now=history)
        require(len(jobs) == 9, "retained_canary_shape_required")
        for index, row in enumerate(jobs):
            row.status = "accepted"
            row.attempt_count = 1
            row.first_attempt_at = history + timedelta(seconds=1)
            row.last_attempt_at = history + timedelta(seconds=1)
            row.accepted_at = history + timedelta(seconds=2)
            row.provider_id = f"synthetic-retained-acceptance-{index}"
            row.error_code = None
            row.updated_at = history + timedelta(seconds=2)
        db.add(LeadDeliveryWorkerState(id=1, last_poll_at=history + timedelta(minutes=1),
            last_completed_at=history + timedelta(minutes=1), last_batch_count=9))


def tokens():
    from database import SessionLocal
    from utils.jwt import create_access_token

    with SessionLocal() as db:
        return {role: create_access_token(user) for role, user in accounts(db).items()}


async def call(client, method, path, expected=200, data=None, token=None):
    headers = {"Origin": ORIGIN}
    if token:
        headers["Authorization"] = "Bearer " + token
    response = await client.request(method, path, json=data, headers=headers)
    require(response.status_code == expected, "actual_endpoint_status")
    require(len(response.content) <= 1024 * 1024, "bounded_response_required")
    return response.json()


def tracking(form, intent="general", locale="en"):
    page = f"/listing/{LISTING_ID}/" if form == "property_inquiry" else "/contact/"
    return {"form": form, "page_path": page, "locale": locale, "intent": intent,
            "contact_consent": True, "measurement_consent": "denied", "internal_test": False}


async def seed_candidate():
    import httpx
    from main import app
    from database import SessionLocal
    from models.booking import Booking
    from models.lead import Lead
    from models.lead_delivery import LeadDeliveryJob
    from models.pending_user import PendingUser
    from models.user import User
    from services.lead_delivery import payload_digest

    calls = fake_provider()
    roles = tokens()
    with SessionLocal() as db:
        require(db.query(LeadDeliveryJob).count() == 9 and db.query(User).count() == 4 and
                db.query(Lead).count() == 3, "candidate_seed_precondition")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000", timeout=10) as client:
        for body in (
            {"type": "buyer_interest", "name": PHONE_NAME, "phone": "+18095551234",
             "tracking": tracking("contact", "buy")},
            {"type": "seller_interest", "name": EMAIL_NAME, "email": EMAIL_ONLY,
             "tracking": tracking("get_started", "sell", "es")},
            {"type": "property_inquiry", "name": PROPERTY_NAME, "email": PROPERTY_EMAIL,
             "property_id": str(LISTING_ID), "tracking": tracking("property_inquiry", "rent")},
        ):
            await call(client, "POST", "/leads", 201, body, roles["buyer"])
        await call(client, "POST", "/auth/register", 201, {
            "display_name": "F01 new verified signup", "email": SIGNUP_EMAIL,
            "password": secrets.token_urlsafe(24),
        })
        with SessionLocal() as db:
            pending = db.query(PendingUser).filter_by(email=SIGNUP_EMAIL).one()
            code = pending.verification_code
        await call(client, "POST", "/auth/verify", 200, {"email": SIGNUP_EMAIL, "code": code, "locale": "es"})
        client.cookies.clear()
        start = date.today() + timedelta(days=60)
        await call(client, "POST", "/bookings", 201, {
            "listing_id": str(LISTING_ID), "check_in": str(start),
            "check_out": str(start + timedelta(days=3)), "guests": 2, "notes": "",
        }, roles["buyer"])
    with SessionLocal() as db:
        phone = db.query(Lead).filter_by(name=PHONE_NAME).one()
        email = db.query(Lead).filter_by(name=EMAIL_NAME).one()
        property_lead = db.query(Lead).filter_by(name=PROPERTY_NAME).one()
        signup = db.query(User).filter_by(email=SIGNUP_EMAIL).one()
        booking = db.query(Booking).filter(Booking.id != HISTORICAL_BOOKING).one()
        jobs = db.query(LeadDeliveryJob).filter(LeadDeliveryJob.lead_id != UUID(int=303)).all()
        jobs += db.query(LeadDeliveryJob).filter(LeadDeliveryJob.lead_id.is_(None)).all()
        require(phone.email is None and bool(phone.phone) and phone.message is None and
                email.email == EMAIL_ONLY and email.phone is None and email.message is None, "contact_shapes_preserved")
        require(property_lead.property_id == LISTING_ID and property_lead.message is None
                and property_lead.tracking["contact_consent"] is True,
                "property_consent_preserved")
        require(signup.email_verified and signup.user_code is not None and signup.status == "active",
                "verified_signup_required")
        require(booking.buyer_id == USER_IDS["buyer"] and booking.lead_id is not None and
                db.get(Lead, booking.lead_id).type == "booking", "linked_booking_required")
        require(len(jobs) == 39 and all(row.attempt_count == 0 and row.status in {"pending", "skipped"}
                                      and row.payload_sha256 == payload_digest(row.payload) for row in jobs),
                "new_outbox_required")
        require(sum(row.step == "customer_email" for row in jobs) == 3, "customer_jobs_required")
        require(all(row.lead_id not in HISTORICAL_LEADS and row.user_id not in USER_IDS.values() for row in jobs),
                "historical_replay_refused")
    network_records = await seed_network_records(roles)
    require(len(calls) == 3, "fake_provider_count")
    return {"phone_only_inquiries": 1, "email_only_inquiries": 1, "consented_property_inquiries": 1,
            "verified_signups": 1, "linked_bookings": 1, "delivery_jobs": 39, "customer_email_jobs": 3,
            "network_records": network_records}


async def seed_network_records(roles):
    """Use the real network endpoints; imported businesses stay private/unowned."""
    import httpx
    from main import app
    from database import SessionLocal
    from models.user import User
    from services.property_network import today_dr
    from uuid import uuid4

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000", timeout=10) as client:
        registry = await call(client, "POST", "/property-network/registry", 201, {
            "title": "Synthetic retained private villa", "location": "Cabarete", "property_class": "house_villa",
            "relationship": "owner", "notes": "Original private property history", "submission_id": str(uuid4()),
            "authority_acknowledged": True,
        }, roles["owner"])
        project = await call(client, "POST", "/property-network/projects", 201, {
            "property_id": registry["id"], "submission_id": str(uuid4()), "title": "Synthetic property maintenance",
            "property_class": "house_villa", "service_category_ids": ["pool_service"],
        }, roles["owner"])
        with SessionLocal() as db:
            code = db.get(User, USER_IDS["realtor"]).user_code
        await call(client, "POST", f"/property-network/projects/{project['id']}/members", 200, {
            "expected_version": project["version"], "user_code": code, "role": "professional",
        }, roles["owner"])
        await call(client, "POST", f"/property-network/projects/{project['id']}/milestones", 201,
                   {"title": "Synthetic completed inspection"}, roles["owner"])
        update = await call(client, "POST", f"/property-network/projects/{project['id']}/updates", 201, {
            "submission_id": str(uuid4()), "body": "Synthetic retained work progress",
            "occurred_on": today_dr().isoformat(), "media_ids": [],
        }, roles["realtor"])
        await call(client, "POST", f"/property-network/updates/{update['id']}/comments", 201,
                   {"submission_id": str(uuid4()), "body": "Synthetic property owner comment"}, roles["owner"])
        profile = await call(client, "PUT", "/property-network/me/profile", 200, {
            "expected_version": 0, "display_name": "Synthetic property owner", "status_text": "Work in progress",
            "visibility": "members", "wall_policy": "own_only", "comments_enabled": True,
        }, roles["owner"])
        with SessionLocal() as db:
            owner_code = db.get(User, USER_IDS["owner"]).user_code
        await call(client, "POST", f"/property-network/profiles/{owner_code}/posts", 201, {
            "submission_id": str(uuid4()), "body": "Synthetic retained profile status", "media_ids": [],
        }, roles["owner"])
    from PIL import Image
    from models.listing_floor_plan import ListingFloorPlan

    buffer = io.BytesIO()
    Image.new("RGB", (3, 2), (21, 64, 98)).save(buffer, format="PNG")
    payload = buffer.getvalue()
    now = datetime.now(timezone.utc)
    with SessionLocal.begin() as db:
        for stage in ("draft", "published"):
            db.add(ListingFloorPlan(id=uuid4(), listing_id=LISTING_ID, stage=stage, content_type="image/png",
                byte_count=len(payload), sha256=sha256(payload).hexdigest(), payload=payload,
                created_at=now, staged_by_id=USER_IDS["owner"],
                approved_at=now if stage == "published" else None,
                approved_by_id=USER_IDS["admin"] if stage == "published" else None))
    return 8


def private_job_snapshot(db):
    from sqlalchemy import text

    jobs = db.execute(text("SELECT to_jsonb(j) FROM lead_delivery_jobs j ORDER BY id")).scalars().all()
    workers = db.execute(text("SELECT to_jsonb(w) FROM lead_delivery_worker_state w ORDER BY id")).scalars().all()
    return jobs, workers


async def verify_old():
    import httpx
    from main import app
    from database import SessionLocal
    from models.lead import Lead
    from models.user import User
    from sqlalchemy import text
    from utils.jwt import create_access_token

    calls = fake_provider()
    roles = tokens()
    with SessionLocal() as db:
        before = private_job_snapshot(db)
        require(len(before[0]) == 48 and len(before[1]) == 1, "outbox_precondition")
        lead_ids = {str(row.id) for row in db.query(Lead).all()}
        require(len(lead_ids) == 7, "old_read_precondition")
        signup = db.query(User).filter_by(email=SIGNUP_EMAIL).one()
        require(signup.role == "buyer" and signup.email_verified and signup.status == "active",
                "verified_signup_required")
        signup_token = create_access_token(signup)
    checks = 0
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000", timeout=10) as client:
        for role, token in roles.items():
            identity_token = signup_token if role == "buyer" else token
            expected_email = SIGNUP_EMAIL if role == "buyer" else ROLE_EMAILS[role]
            identity = await call(client, "GET", "/auth/me", token=identity_token)
            require(identity["role"] == role and identity["email"] == expected_email, "old_role_read_required")
            checks += 1
        rows = await call(client, "GET", "/admin/leads", token=roles["admin"])
        require({row["id"] for row in rows} == lead_ids, "old_nullable_leads_required")
        phone = next(row for row in rows if row["name"] == PHONE_NAME)
        email = next(row for row in rows if row["name"] == EMAIL_NAME)
        property_lead = next(row for row in rows if row["name"] == PROPERTY_NAME)
        require(phone["email"] is None and phone["message"] is None and email["phone"] is None and
                email["message"] is None and property_lead["message"] is None,
                "old_nullable_leads_required")
        checks += 1
        for path, role in (
            ("/leads/mine", "buyer"), ("/realtor/leads", "realtor"),
            ("/bookings/mine", "buyer"), ("/bookings/for-owner", "owner"),
            ("/inquiries/mine", "buyer"), ("/inquiries/for-owner", "owner"),
            ("/inquiries/for-realtor", "realtor"),
        ):
            value = await call(client, "GET", path, token=roles[role])
            require(isinstance(value, list) and len(value) >= 1, "old_role_read_required")
            checks += 1
        configuration = await call(client, "GET", "/admin/settings", token=roles["admin"])
        require(configuration["notify_email"] == ",".join(TEAM), "old_configuration_preserved")
        checks += 1
        require(not calls, "historical_provider_replay")
        receipt = await call(client, "POST", "/leads", 201, {
            "type": "property_inquiry", "name": OLD_NAME, "email": OLD_EMAIL,
            "message": "", "property_id": str(LISTING_ID),
        })
        require(isinstance(receipt.get("id"), str), "old_client_intake_required")
    require(checks == 13 and len(calls) == 1, "old_fallback_required")
    require(calls[0]["to"] == list(TEAM) and OLD_NAME in calls[0]["subject"], "old_fallback_required")
    with SessionLocal() as db:
        after = private_job_snapshot(db)
        require(after == before, "delivery_jobs_changed")
        require(db.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [HEAD],
                "exact_migration_required")
        lead = db.query(Lead).filter_by(name=OLD_NAME, email=OLD_EMAIL).one()
        require(str(lead.id) == receipt["id"] and lead.property_id == LISTING_ID and lead.tracking is None and
                lead.submission_id is None and db.query(Lead).count() == 8, "old_client_intake_required")
        require(all(row["lead_id"] not in {str(value) for value in HISTORICAL_LEADS} and
                    row["user_id"] not in {str(value) for value in USER_IDS.values()} for row in after[0] if row["lead_id"] != str(UUID(int=303))),
                "historical_replay_refused")
    return {"role_read_checks": 13, "old_client_intakes": 1, "fake_owner_notices": 1,
            "head_preserved": True, "delivery_jobs_unchanged": True, "no_historical_replay": True}


def main(mode):
    root = guard(mode)
    configure(root, mode == "seed_candidate")
    if mode == "seed_live":
        return seed_live()
    if mode == "seed_candidate":
        return asyncio.run(seed_candidate())
    return asyncio.run(verify_old())


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) == 2 and sys.argv[1] in ROOTS else "invalid_mode"
    try:
        require(mode in ROOTS, "mode_refused")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            counts = main(mode)
        marker = {"status": "PASS", "stage": mode, **counts}
    except Exception as error:
        kind = type(error).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", kind):
            kind = "UnhandledError"
        reason = str(error) if isinstance(error, RehearsalFailure) else "unexpected_failure"
        if not re.fullmatch(r"[a-z_]{1,50}", reason):
            reason = "unexpected_failure"
        marker = {"status": "FAIL", "stage": mode, "error_type": kind, "reason": reason}
    print("LIVE_DELIVERY_ROLLBACK_STAGE " + json.dumps(marker, sort_keys=True, separators=(",", ":")))
    raise SystemExit(0 if marker["status"] == "PASS" else 1)
