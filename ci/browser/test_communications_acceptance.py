"""Exercise the accepted communications routes and worker in hosted loopback CI.

Run with ``python -I -B`` inside the owned Postgres network namespace, with the
accepted browser fixture helper mounted read-only at /tools and a fresh marked
database. The application runs in-process with no server or lifespan workers.
The recorded result describes simulated provider acceptance only.
"""

import hashlib
import importlib.util
import json
import logging
import os
import re
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4


HELPER = Path("/tools/serve_browser_fixture.py")
HELPER_SHA256 = "7c8954c3648d63e247587d22c55661d0a6b63e432483d7d8c71ef3768472325b"
DATABASE_NAME = "ildr_platform_test_20261025"
DATABASE_URL = f"postgresql+psycopg2://postgres@127.0.0.1:55449/{DATABASE_NAME}"
CUSTOMER_ORIGIN = "https://ilovedrrealty.com"
SENDER = "rehearsal-sender@example.com"
OWNER_RECIPIENTS = tuple(f"rehearsal-owner-{number}@example.com" for number in range(1, 5))
OWNER_STEPS = {f"email_{number}": address for number, address in enumerate(OWNER_RECIPIENTS, 1)}
FIXTURE_NOTE = "Rehearsal internal lead note"
EXPECTED_PRIMARY_SENDS = 25
EXPECTED_OWNER_ONLY_SENDS = 12
EXPECTED_SENDS = EXPECTED_PRIMARY_SENDS + EXPECTED_OWNER_ONLY_SENDS
stage = "checkout"


class RehearsalFailure(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise RehearsalFailure(reason)


@dataclass(frozen=True)
class Event:
    kind: str
    identity: UUID
    name: str
    email: str | None
    owner_label: str
    customer_copy: tuple[str, str, str, str] | None
    public_listing: UUID | None = None

    @property
    def key(self):
        prefix = "website-signup" if self.kind == "signup" else "website-lead"
        return f"{prefix}/{self.identity}"


def confirm_disposable_database():
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    require(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL") == DATABASE_URL
            and re.fullmatch(r"[a-f0-9]{32}", nonce), "owned_database_configuration_required")
    import psycopg2

    with closing(psycopg2.connect(host="127.0.0.1", port=55449, dbname=DATABASE_NAME, user="postgres",
                                 password="", connect_timeout=4)) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT current_database(), host(inet_server_addr()), inet_server_port(),
                       current_setting('server_version_num')::integer,
                       shobj_description(oid, 'pg_database'),
                       NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                                   WHERE n.nspname = 'public')
                FROM pg_database WHERE datname = current_database()
            """)
            identity = cursor.fetchone()
    require(identity == (DATABASE_NAME, "127.0.0.1", 55449, 170011, f"ildr-communications-ci:{nonce}", True),
            "fresh_marked_communications_database_required")


def verified_isolation():
    require({interface.name for interface in Path("/sys/class/net").iterdir()} == {"lo"},
            "loopback_namespace_required")
    require(HELPER.is_file() and not HELPER.is_symlink()
            and hashlib.sha256(HELPER.read_bytes()).hexdigest() == HELPER_SHA256,
            "accepted_fixture_helper_required")
    spec = importlib.util.spec_from_file_location("accepted_browser_fixture", HELPER)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    application = fixture.verified_checkout()
    confirm_disposable_database()
    fixture.configure_application(application, DATABASE_URL)
    os.environ.update(
        RESEND_API_KEY="synthetic-placeholder-only",
        EMAIL_FROM=SENDER,
        LEAD_DELIVERY_ENABLED="true",
        CUSTOMER_MESSAGES_ENABLED="true",
        PROPERTY_INQUIRY_REQUIRE_CONSENT="true",
        GHL_WEBHOOK_SECRET="",
        LOGO_URL="",
    )
    return fixture


def lead_payload(kind, email, intent, locale, listing=None):
    property_path = f"/listing/{listing}/" if kind == "property_inquiry" else "/get-started/"
    return {
        "type": kind,
        "name": "Rehearsal Website Visitor",
        "email": email,
        "phone": "+12025550123",
        "message": FIXTURE_NOTE,
        "submission_id": str(uuid4()),
        "property_id": str(listing) if listing else None,
        "tracking": {
            "form": "property_inquiry" if kind == "property_inquiry" else "get_started",
            "page_path": property_path,
            "intent": intent,
            "locale": locale,
            "contact_consent": True,
            "measurement_consent": "denied",
            "internal_test": False,
        },
    }


def customer_payload_matches(body, event):
    locale, subject, next_step, path = event.customer_copy
    target = f"{CUSTOMER_ORIGIN}{path}"
    text_body, html_body = body.get("text", ""), body.get("html", "")
    thanks = "¡Gracias" if locale == "es" else "Thank you"
    greeting = f"Hola {event.name}," if locale == "es" else f"Hi {event.name},"
    return (body.get("subject") == subject and text_body.startswith(greeting)
            and thanks in text_body and next_step in text_body and next_step in html_body
            and target in text_body and f'href="{target}"' in html_body and f'lang="{locale}"' in html_body
            and FIXTURE_NOTE not in text_body and FIXTURE_NOTE not in html_body
            and not any(owner in text_body or owner in html_body for owner in OWNER_RECIPIENTS))


def owner_payload_matches(body, event, landing_origin):
    html_body = body.get("html", "")
    if event.kind == "signup":
        return (body.get("subject") == "New verified signup — I Love DR Realty"
                and event.name in html_body and event.email in html_body
                and 'href="https://app.ilovedrrealty.com/users?workspace=admin"' in html_body)
    return (body.get("subject") == f"New lead: {event.name}" and event.owner_label in html_body
            and FIXTURE_NOTE in html_body and f'href="{landing_origin}/dashboard"' in html_body
            and (event.public_listing is None or f'/listing/{event.public_listing}"' in html_body))


def register_and_verify(client, db_factory, job_type, user_type, pending_type, lead_type, verification):
    name, address = "Rehearsal New Customer", "rehearsal-signup@example.com"
    response = client.post("/auth/register", json={"display_name": name, "email": address,
                                                   "password": "RehearsalSyntheticPassword-4175"})
    require(response.status_code == 201 and len(verification) == 1, "registration_route_failed")
    code_address, code = verification.pop()
    require(code_address == address and isinstance(code, str) and bool(code), "verification_stub_failed")
    with db_factory() as db:
        require(db.query(pending_type).filter_by(email=address).count() == 1
                and db.query(user_type).filter_by(email=address).count() == 0
                and db.query(job_type).count() == 0, "premature_signup_delivery")
    verify = {"email": address, "code": code, "locale": "es"}
    response = client.post("/auth/verify", json=verify)
    require(response.status_code == 200 and "ildr_token" in response.cookies,
            "verified_signup_route_failed")
    require(client.post("/auth/verify", json=verify).status_code == 400, "duplicate_signup_not_refused")
    client.cookies.clear()
    with db_factory() as db:
        user = db.query(user_type).filter_by(email=address).one()
        require(user.status == "active" and user.email_verified is True and user.user_code is not None
                and db.query(pending_type).filter_by(email=address).count() == 0
                and db.query(lead_type).count() == 0 and db.query(job_type).count() == 5,
                "verified_signup_transaction_incomplete")
        return Event("signup", user.id, name, address, "New verified signup", (
            "es", "Te damos la bienvenida a I Love DR Realty", "Comienza explorando propiedades", "/search/",
        ))


def create_lead(client, body):
    response = client.post("/leads", json=body)
    require(response.status_code == 201 and response.headers.get("cache-control") == "no-store",
            "lead_route_failed")
    receipt = response.json()
    require(receipt["status"] == "new" and receipt["measurement"]["eligible"] is False,
            "unexpected_lead_receipt")
    return UUID(receipt["id"])


def submit_leads(client, public_listing, private_listing):
    scenarios = (
        ("buyer_interest", "buy", "en", "Buyer Interest", None, (
            "en", "Thank you — let’s find your property", "preferred area, budget and purchase timeline", "/search/",
        )),
        ("seller_interest", "sell", "es", "Seller Interest", None, (
            "es", "Gracias — planifiquemos la venta de tu propiedad", "la ubicación, las características de tu propiedad",
            "/selling/",
        )),
        ("rental_interest", "rent", "en", "Rental Interest", None, (
            "en", "Thank you — let’s find your rental", "preferred dates, area, number of guests and budget", "/rentals/",
        )),
        ("property_inquiry", "general", "es", "Property Inquiry", public_listing, (
            "es", "Gracias por tu consulta sobre la propiedad", "La solicitud no reserva la propiedad ni confirma una visita",
            f"/listing/{public_listing}/",
        )),
    )
    events, repeatable = [], []
    for kind, intent, locale, label, listing, copy in scenarios:
        address = f"rehearsal-{intent}@example.com"
        body = lead_payload(kind, address, intent, locale, listing)
        identity = create_lead(client, body)
        require(create_lead(client, body) == identity, "lead_idempotency_failed")
        repeatable.append((body, identity))
        events.append(Event(kind, identity, body["name"], address, label, copy, listing))

    no_consent = lead_payload("property_inquiry", "rehearsal-no-consent@example.com", "general", "en", public_listing)
    no_consent["tracking"]["contact_consent"] = False
    private = lead_payload("property_inquiry", "rehearsal-private@example.com", "general", "en", private_listing)
    require(client.post("/leads", json=no_consent).status_code == 422, "missing_consent_not_refused")
    require(client.post("/leads", json=private).status_code == 404, "private_listing_inquiry_not_refused")

    internal = lead_payload("seller_interest", "rehearsal-internal@example.com", "sell", "en")
    internal["tracking"]["internal_test"] = True
    legacy = lead_payload("buyer_interest", "rehearsal-legacy@example.com", "buy", "en")
    del legacy["tracking"]
    phone_only = lead_payload("property_inquiry", None, "general", "en", public_listing)
    for body, label, listing in ((internal, "Seller Interest", None), (legacy, "Buyer Interest", None),
                                 (phone_only, "Property Inquiry", public_listing)):
        events.append(Event(body["type"], create_lead(client, body), body["name"], body["email"], label, None, listing))
    return events, repeatable


def expected_deliveries(events):
    expected = {}
    for event in events:
        steps = dict(OWNER_STEPS)
        if event.customer_copy is not None:
            steps["customer_email"] = event.email
        for step, recipient in steps.items():
            expected[f"{event.key}/{step}/v1"] = (event, step, recipient)
    require(len(expected) == EXPECTED_SENDS, "event_matrix_size_changed")
    return expected


def process_and_read_back(service, db_factory, transport, job_type, expected, attempts):
    with db_factory() as db:
        pending = db.query(job_type).filter_by(provider="resend").all()
        require(len(pending) == EXPECTED_SENDS and all(job.status == "pending" and job.attempt_count == 0
                                                       and job.provider_id is None for job in pending),
                "outbox_pending_matrix_incomplete")
        crm = db.query(job_type).filter_by(provider="ghl").all()
        require(len(crm) == 28 and all(job.status == "skipped" and job.attempt_count == 0 for job in crm),
                "crm_not_isolated")
    for count in (10, 10, 10, 7, 0):
        require(service.run_batch(db_factory, client=transport) == {"enabled": True, "processed": count},
                "worker_batch_result_changed")
    require(len(attempts) == EXPECTED_SENDS and {key for key, _body in attempts} == set(expected),
            "duplicate_or_missing_provider_attempt")
    with db_factory() as db:
        accepted = db.query(job_type).filter_by(provider="resend").all()
        require(len(accepted) == EXPECTED_SENDS and all(job.status == "accepted" and job.attempt_count == 1
                and job.accepted_at is not None and job.provider_id is not None
                and job.lease_token is None and not job.safe_to_retry and not job.outcome_uncertain for job in accepted),
                "outbox_acceptance_readback_failed")
        require({job.provider_id for job in accepted} == {f"simulated-email-{n}" for n in range(1, EXPECTED_SENDS + 1)},
                "provider_acknowledgements_not_preserved")


def run():
    global stage
    logging.disable(logging.CRITICAL)
    fixture = verified_isolation()

    import httpx
    import requests
    from alembic import command
    from alembic.config import Config
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from urllib import request as urllib_request

    import bootstrap_db
    from config import settings
    from database import SessionLocal, engine
    from models.lead import Lead
    from models.lead_delivery import LeadDeliveryJob
    from models.listing import Listing
    from models.pending_user import PendingUser
    from models.site_settings import SiteSettings
    from models.user import User
    from routes import auth, leads
    from services import lead_delivery
    from utils.limiter import limiter

    stage = "migrate_and_seed"
    require(settings.resend_api_key == "synthetic-placeholder-only" and settings.email_from == SENDER
            and settings.landing_url == fixture.LANDING_ORIGIN and not settings.ghl_enabled
            and not settings.ghl_api_key and not settings.supabase_service_key
            and settings.lead_delivery_enabled and settings.customer_messages_enabled
            and settings.property_inquiry_require_consent, "synthetic_configuration_required")
    bootstrap_db.main()
    command.upgrade(Config("alembic.ini"), "head")
    public_listing, private_listing, historical_id = uuid4(), uuid4(), uuid4()
    with SessionLocal.begin() as db:
        require(db.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [fixture.MIGRATION_HEAD]
                and db.query(User).count() == db.query(Lead).count() == db.query(LeadDeliveryJob).count() == 0,
                "fresh_migrated_database_required")
        owner = User(email="rehearsal-listing-owner@example.com", display_name="Rehearsal Listing Owner",
                     role="owner", email_verified=True, status="active")
        db.add(owner)
        db.flush()
        site_settings = db.get(SiteSettings, 1)
        require(site_settings is not None and site_settings.data == {}, "unexpected_seeded_recipient_configuration")
        site_settings.data = {"notify_email": ", ".join(OWNER_RECIPIENTS)}
        for listing_id, status in ((public_listing, "active"), (private_listing, "draft")):
            db.add(Listing(id=listing_id, title=f"Rehearsal {status} villa", type="villa", transaction="sale",
                           price=1000, location="Rehearsal location", status=status,
                           submitted_by=owner.id, owner_id=owner.id))

    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(auth.router)
    app.include_router(leads.router)
    verification, network_attempts, attempts = [], [], []

    def blocked_network(*_args, **_kwargs):
        network_attempts.append(True)
        raise RehearsalFailure("unexpected_network_transport")

    def provider(request):
        body = json.loads(request.content)
        key = request.headers.get("Idempotency-Key")
        attempts.append((key, body))
        if request.method != "POST" or str(request.url) != "https://api.resend.com/emails":
            return httpx.Response(400)
        return httpx.Response(200, json={"id": f"simulated-email-{len(attempts)}"})

    def capture_code(address, code):
        verification.append((address, code))

    # Every API request here has the same in-process client identity. Disabling
    # its limiter only in this disposable process makes duplicates deterministic.
    with (
        patch.object(limiter, "enabled", False),
        patch.object(httpx.HTTPTransport, "handle_request", blocked_network),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", blocked_network),
        patch.object(requests.Session, "send", blocked_network),
        patch.object(urllib_request, "urlopen", blocked_network),
        patch.object(auth, "send_verification_email", capture_code),
        patch.object(auth, "_notify_verified_signup") as direct_signup,
        patch.object(leads, "send_lead_notification") as direct_lead,
        patch.object(leads.ghl, "create_contact") as direct_crm,
        TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False, follow_redirects=False) as client,
        httpx.Client(transport=httpx.MockTransport(provider), trust_env=False, follow_redirects=False) as transport,
    ):
        stage = "verified_signup"
        signup = register_and_verify(client, SessionLocal, LeadDeliveryJob, User, PendingUser, Lead, verification)
        stage = "lead_requests"
        with SessionLocal.begin() as db:
            db.add(Lead(id=historical_id, type="buyer_interest", name="Rehearsal historical lead",
                        email="rehearsal-history@example.com", created_at=datetime.now(timezone.utc) - timedelta(days=3)))
        new_leads, repeatable = submit_leads(client, public_listing, private_listing)
        expected = expected_deliveries([signup, *new_leads])
        require(not attempts and not network_attempts and not verification, "provider_called_before_worker")
        stage = "worker_dispatch"
        process_and_read_back(lead_delivery, SessionLocal, transport, LeadDeliveryJob, expected, attempts)

        stage = "provider_payloads"
        for key, body in attempts:
            event, step, recipient = expected[key]
            require(body.get("from") == SENDER and body.get("to") == [recipient]
                    and "cc" not in body and "bcc" not in body, "unexpected_provider_recipients")
            if step == "customer_email":
                require(customer_payload_matches(body, event), "customer_locale_or_next_step_changed")
            else:
                require(owner_payload_matches(body, event, fixture.LANDING_ORIGIN), "owner_event_content_changed")

        stage = "duplicate_and_history_readback"
        for body, identity in repeatable:
            require(create_lead(client, body) == identity, "post_dispatch_duplicate_changed")
        require(lead_delivery.run_batch(SessionLocal, client=transport) == {"enabled": True, "processed": 0}
                and len(attempts) == EXPECTED_SENDS and not network_attempts and not verification
                and not (direct_signup.called or direct_lead.called or direct_crm.called),
                "delivery_replayed_or_fallback_called")
        with SessionLocal() as db:
            require(db.query(Lead).count() == 8 and db.query(LeadDeliveryJob).count() == 65
                    and db.query(LeadDeliveryJob).filter_by(lead_id=historical_id).count() == 0,
                    "unexpected_new_or_historical_jobs")
    engine.dispose()
    return {
        "status": "PASS",
        "application_commit": fixture.APP_COMMIT,
        "application_tree": fixture.APP_TREE,
        "alembic_head": fixture.MIGRATION_HEAD,
        "event_kinds": ["signup", "buyer_interest", "seller_interest", "rental_interest", "property_inquiry"],
        "primary_owner_acceptances": 20,
        "primary_customer_acceptances": 5,
        "owner_only_guard_case_acceptances": 12,
        "accepted_mock_provider_requests": EXPECTED_SENDS,
        "crm_rows_skipped": 28,
        "customer_locales": ["en", "es"],
        "external_transport_attempts": 0,
        "real_inbox_delivery_verified": False,
    }


if __name__ == "__main__":
    try:
        result = run()
    except Exception as error:
        result = {"status": "FAIL", "stage": stage, "exception_type": type(error).__name__}
        if isinstance(error, RehearsalFailure):
            result["reason"] = str(error)
        print(json.dumps(result, sort_keys=True), flush=True)
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True), flush=True)
