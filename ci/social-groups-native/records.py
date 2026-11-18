"""Synthetic retained-record and group lifecycle checks in nonce-owned databases."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO, StringIO
import json
import logging
import os
from pathlib import Path
import secrets
import sys
from unittest.mock import patch
from uuid import UUID, uuid4

sys.path.insert(0, "/tools")
from catalog import require, query

IDS = {role: UUID(int=100 + index) for index, role in enumerate(("admin", "owner", "buyer", "realtor"), 1)}
LISTING = UUID(int=201)
TEAM = [f"groups-retained-team-{index}@example.com" for index in range(4)]
HISTORY = datetime(2025, 1, 1, tzinfo=timezone.utc)
ATTEMPTS = []


def refused(*_args, **_kwargs):
    ATTEMPTS.append(1)
    raise RuntimeError("external_transport_refused")


def isolate(root):
    require(root in {"/previous", "/application", "/rollback"} and Path.cwd() == Path(root), "source_root_required")
    nonce = os.environ["ILDR_GROUPS_CI_NONCE"]
    database = "ildr_groups_forward_" + nonce
    require(os.environ["DATABASE_URL"] == f"postgresql+psycopg2://postgres@127.0.0.1:55449/{database}", "owned_database_required")
    require(query(database, "SELECT current_setting('server_version_num')::int,host(inet_server_addr()),inet_server_port(),shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()") == [(170006, "127.0.0.1", 55449, f"ildr-social-groups-ci:{nonce}:forward")], "owned_database_required")
    sys.path.insert(0, root)
    logging.disable(logging.CRITICAL)
    import requests, httpx, resend, smtplib, urllib.request
    requests.sessions.Session.request = refused
    httpx.HTTPTransport.handle_request = refused
    httpx.AsyncHTTPTransport.handle_async_request = refused
    resend.Emails.send = refused
    smtplib.SMTP = refused
    smtplib.SMTP_SSL = refused
    urllib.request.urlopen = refused
    from config import settings
    require(not settings.lead_delivery_enabled and not settings.customer_messages_enabled and not settings.ghl_enabled,
            "workers_disabled_required")
    from database import SessionLocal, engine
    require(str(engine.url) == os.environ["DATABASE_URL"] and SessionLocal.kw["bind"] is engine, "actual_engine_required")


def seed_old():
    from sqlalchemy import null
    from config import settings
    from database import SessionLocal
    from models.user import User
    from models.listing import Listing
    from models.lead import Lead
    from models.inquiry import Inquiry
    from models.booking import Booking
    from models.site_settings import SiteSettings
    from models.trade_contact import TradeContact, TradeContactSource
    from models.professional_profile import ProfessionalProfile, ProfessionalProfileRevision
    from models.website_settings import WebsiteSettings, WebsiteSettingsRevision
    from models.listing_floor_plan import ListingFloorPlan
    from models.lead_delivery import LeadDeliveryWorkerState
    from schemas.trade_contacts import TradeContactEntry, TradeContactImport
    from schemas.professionals import ProfessionalDraft
    from schemas.website_settings import WebsiteContentSettings
    from services.trade_contacts import import_entries
    from services.lead_delivery import enqueue_lead_delivery
    from utils.security import hash_password
    from PIL import Image

    with SessionLocal() as db:
        require(db.query(User).count() == 0 and db.query(TradeContact).count() == 0, "empty_seed_required")
        for role, identity in IDS.items():
            db.add(User(id=identity, role=role, status="active", email=f"groups-retained-{role}@example.com",
                        display_name="Synthetic retained " + role, email_verified=True,
                        password_hash=hash_password(secrets.token_urlsafe(24)), created_at=HISTORY))
        db.flush()
        db.add(Listing(id=LISTING, title="Synthetic retained villa", type="villa", transaction="rent",
            price=3000, price_per_day=100, currency="USD", location="Cabarete", status="active",
            submitted_by=IDS["owner"], owner_id=IDS["owner"], assigned_realtor_id=IDS["realtor"], images=[], bedrooms=3, bathrooms=3))
        db.flush()
        start = date.today() - timedelta(days=60)
        for identity, kind in ((UUID(int=301), "property_inquiry"), (UUID(int=302), "booking")):
            db.add(Lead(id=identity, type=kind, name="Synthetic historical " + kind,
                email="groups-retained-buyer@example.com", message="Synthetic retained original note",
                property_id=LISTING, from_user_id=IDS["buyer"], assigned_realtor_id=IDS["realtor"], status="assigned", created_at=HISTORY))
        db.flush()
        db.add(Inquiry(id=UUID(int=401), listing_id=LISTING, name="Synthetic retained inquiry",
            email="groups-retained-buyer@example.com", message="Original inquiry note", from_user_id=IDS["buyer"], created_at=HISTORY))
        db.add(Booking(id=UUID(int=501), listing_id=LISTING, buyer_id=IDS["buyer"], lead_id=UUID(int=302),
            check_in=str(start), check_out=str(start + timedelta(days=3)), guests=2, total_price=300,
            notes="Original booking note", status="confirmed", created_at=HISTORY))
        configuration = db.get(SiteSettings, 1)
        require(db.query(SiteSettings).count() == 1 and configuration is not None and configuration.data == {},
                "source_created_settings_required")
        configuration.data = {"notify_email": ",".join(TEAM), "groups_retained_sentinel": "preserve"}
        entries = [TradeContactEntry(name=f"Synthetic retained business {index}", category="Synthetic property services",
            province="Puerto Plata", locality="Cabarete", phone=f"+1809555{index:04d}", source_authority="Synthetic source",
            source_url=f"https://example.com/synthetic-retained/{index}", verified_on=date(2025, 1, 1),
            verification_status="owner_source" if index < 82 else "public_source",
            source_details={"original_note": "Retain complete synthetic source note", "index": str(index)}) for index in range(85)]
        outcome = import_entries(db, TradeContactImport(dry_run=False, entries=entries, preserve_source_details=True))
        require(outcome.inserted == 85, "trade_seed_required")
        second = entries[-1].model_copy(update={"source_authority": "Independent synthetic source",
            "source_url": "https://example.org/independent-synthetic-source"})
        import_entries(db, TradeContactImport(dry_run=False, entries=[second], preserve_source_details=True))
        require(db.query(TradeContact).count() == 85 and db.query(TradeContactSource).count() == 86, "trade_source_seed_required")
        for index in range(46):
            draft = ProfessionalDraft(business_name=f"Synthetic private business {index}").model_dump(mode="json")
            profile = ProfessionalProfile(id=UUID(int=3000 + index), user_id=None, version=1, draft_data=draft,
                published_data=null(), review_status="draft", listing_status="hidden", published_at=None,
                created_at=HISTORY, updated_at=HISTORY)
            db.add(profile)
            db.flush()
            db.add(ProfessionalProfileRevision(id=UUID(int=4000 + index), profile_id=profile.id, version=1,
                action="create_managed", snapshot={"draft_data": draft, "listing_status": "hidden"},
                actor_id=IDS["admin"], created_at=HISTORY))
        links = [{"label": {"en": en, "es": es}, "route": route, "enabled": True} for en, es, route in (
            ("Home", "Inicio", "landing"), ("Buying", "Comprar", "buying"), ("Selling", "Vender", "selling"),
            ("Renting", "Alquilar", "rentals"), ("Team", "Equipo", "team"), ("Properties", "Propiedades", "search"),
            ("Blog", "Blog", "blog"), ("Contact", "Contacto", "contact"))]
        cms = WebsiteContentSettings(navigation=links).model_dump(mode="json")
        db.add(WebsiteSettings(id=1, version=7, data=cms, updated_by=IDS["admin"], updated_at=HISTORY))
        for version in range(8):
            db.add(WebsiteSettingsRevision(version=version, snapshot=cms, actor_id=IDS["admin"], created_at=HISTORY))
        canary = Lead(id=UUID(int=303), type="buyer_interest", name="Synthetic retained canary",
            email="groups-retained-buyer@example.com", message="Retained test record",
            from_user_id=IDS["buyer"], tracking={"contact_consent": True, "locale": "en", "intent": "buy"}, created_at=HISTORY)
        db.add(canary)
        db.flush()
        with patch.object(settings, "lead_delivery_enabled", True), patch.object(settings, "customer_messages_enabled", True), \
                patch.object(settings, "ghl_enabled", True), patch.object(settings, "ghl_location_id", "synthetic-location"):
            jobs = enqueue_lead_delivery(db, canary, recipients=TEAM, now=HISTORY)
        require(len(jobs) == 9, "retained_job_shape_required")
        for index, row in enumerate(jobs):
            row.status = "accepted"
            row.attempt_count = 1
            row.first_attempt_at = row.last_attempt_at = HISTORY + timedelta(seconds=1)
            row.accepted_at = row.updated_at = HISTORY + timedelta(seconds=2)
            row.provider_id = f"synthetic-accepted-{index}"
            row.error_code = None
        db.add(LeadDeliveryWorkerState(id=1, last_poll_at=HISTORY + timedelta(minutes=1),
            last_completed_at=HISTORY + timedelta(minutes=1), last_batch_count=9))
        for index, stage in enumerate(("draft", "published")):
            buffer = BytesIO()
            Image.new("RGB", (3, 2), (index * 80, 30, 60)).save(buffer, format="PNG")
            payload = buffer.getvalue()
            db.add(ListingFloorPlan(id=UUID(int=5000 + index), listing_id=LISTING, stage=stage,
                content_type="image/png", byte_count=len(payload), sha256=sha256(payload).hexdigest(), payload=payload,
                created_at=HISTORY, staged_by_id=IDS["owner"], approved_at=HISTORY if index else None,
                approved_by_id=IDS["admin"] if index else None))
        db.commit()
    return {"users": 4, "leads": 3, "owner_contacts": 82, "contacts": 85, "sources": 86,
            "hidden_profiles": 46, "accepted_jobs": 9, "worker_rows": 1, "cms_version": 7, "floorplan_rows": 2}


def seed_groups():
    from database import SessionLocal
    from models.user import User
    from schemas.social_groups import GroupCreate, ManageMember, MembershipAction, GroupPostCreate, GroupCommentCreate, CreateInviteLink
    from services import social_groups as service
    from PIL import Image
    stored = {}

    def store(key, data):
        require(key.startswith("groups/") and key.endswith(".webp") and key not in stored, "synthetic_storage_scope_required")
        stored[key] = data

    with SessionLocal() as db, patch.object(service.storage, "store_image", store):
        owner, buyer = (db.get(User, IDS[role]) for role in ("owner", "buyer"))
        created = service.create_group(db, owner, GroupCreate(submission_id=uuid4(), name="Synthetic retained private group", owner_alias="Owner alias"))
        identity = UUID(created["target_id"])
        service.manage_member(db, owner, identity, buyer.user_code, ManageMember(submission_id=uuid4(), action="invite", expected_membership_version=0))
        service.own_membership_action(db, buyer, identity, MembershipAction(submission_id=uuid4(), action="accept_invite", expected_membership_version=1, member_alias="Member alias"))
        buffer = BytesIO()
        Image.new("RGB", (3, 2), (40, 80, 120)).save(buffer, format="PNG")
        media = service.upload_media(db, owner, identity, "post", uuid4(), buffer.getvalue())
        post = service.create_post(db, owner, identity, GroupPostCreate(submission_id=uuid4(), body="Synthetic group progress", media_ids=[UUID(media["target_id"])]))
        service.create_comment(db, buyer, UUID(post["target_id"]), GroupCommentCreate(submission_id=uuid4(), body="Synthetic photo comment", media_id=UUID(media["target_id"])))
        service.create_link(db, owner, identity, CreateInviteLink(submission_id=uuid4(), token=secrets.token_urlsafe(32)))
        require(len(stored) == 1 and service.post_for(db, buyer, UUID(post["target_id"])).group_id == identity, "actual_group_lifecycle_required")
    return {"groups": 1, "memberships": 1, "posts": 1, "comments": 1, "media": 1, "links": 1, "actions": 7, "synthetic_storage_writes": 1}


def verify_old():
    from fastapi.testclient import TestClient
    from main import app
    from database import SessionLocal
    from models.user import User
    from utils.jwt import create_access_token
    with SessionLocal() as db:
        tokens = {role: create_access_token(db.get(User, identity)) for role, identity in IDS.items()}
    client = TestClient(app)  # No context/lifespan: never start the worker.
    count = 0
    for path, role, expected in (("/health", None, 200), ("/auth/me", "admin", 200),
        ("/auth/me", "owner", 200), ("/auth/me", "buyer", 200), ("/auth/me", "realtor", 200),
        ("/admin/trade-contacts?limit=1", "admin", 200), ("/admin/trade-contacts/map", "admin", 200),
        ("/admin/trade-contacts", "owner", 403), ("/property-network/groups", "owner", 404)):
        headers = {"Authorization": "Bearer " + tokens[role]} if role else {}
        response = client.get(path, headers=headers)
        require(response.status_code == expected, "old_actual_endpoint_required")
        require(len(response.content) <= 1024 * 1024, "bounded_old_response_required")
        count += 1
    client.close()
    return {"actual_old_reads": count, "new_group_route_absent": True}


if __name__ == "__main__":
    mode = sys.argv[1]
    require(mode in {"seed_old", "seed_groups", "verify_old"}, "exact_mode_required")
    root = {"seed_old": "/previous", "seed_groups": "/application", "verify_old": "/rollback"}[mode]
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        isolate(root)
        result = globals()[mode]()
        require(not ATTEMPTS, "no_external_attempts_required")
    print("GROUPS_RECORDS_RESULT " + json.dumps({"mode": mode, "external_provider_attempts": 0, **result}, sort_keys=True))
