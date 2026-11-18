"""Three real UI service-intake writes against synthetic users and one listing."""

from collections import Counter
from datetime import timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import sys
from urllib import request as urllib_request
from unittest.mock import patch
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent / "accepted"))
import serve_service_visit_fixture as base

base.APP_COMMIT = "01695530df670a94ac28f8b3bf36cc34b5b7a942"
base.APP_TREE = "e88ea0c57f9fac39545322532a5992d5a2927676"
base.MIGRATION_HEAD = "b19d8e4c720a"
NETWORK = "/property-network"
ROLES = {"owner": "owner", "provider": "buyer", "outsider": "buyer"}
EXPECTED = Counter({name: 1 for name in ("request_create", "recipient_accept", "provider_quote")})
UUID_TEXT = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
FACTS = {
    "listingTitle": "Synthetic managed pool villa",
    "providerAlias": "Synthetic opted-in pool member",
    "title": "Synthetic pool service request",
    "brief": "Inspect and service the synthetic pool equipment.",
    "requesterAlias": "Synthetic property owner",
    "area": "Synthetic Cabarete",
    "category": "pool_service",
    "propertyClass": "house_villa",
    "quoteScope": "Inspect pump, filter and pool circulation.",
    "quoteExclusions": "Replacement parts require a separate agreement.",
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def seed_accounts():
    import bootstrap_db
    from alembic import command
    from alembic.config import Config
    from database import SessionLocal
    from models.listing import Listing
    from models.service_workflow import (
        NetworkServiceAction, NetworkServicePreference, NetworkServiceProposal,
        NetworkServiceRecipient, NetworkServiceRequest,
    )
    from models.user import User
    from services.property_network import today_dr
    from sqlalchemy import text
    from utils.security import hash_password

    base.advance("bootstrap_database")
    bootstrap_db.main()
    command.upgrade(Config("alembic.ini"), "head")
    accounts, identities, codes = {}, {}, {}
    facts = {**FACTS, "listingId": str(uuid4()), "validUntil": (today_dr() + timedelta(days=14)).isoformat()}
    with SessionLocal() as db:
        base.require(db.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
                     == [base.MIGRATION_HEAD]
                     and all(db.query(model).count() == 0 for model in (
                         User, Listing, NetworkServicePreference, NetworkServiceRequest,
                         NetworkServiceRecipient, NetworkServiceProposal, NetworkServiceAction)),
                     "Fresh synthetic service intake required")
        for actor, role in ROLES.items():
            account = {"email": f"browser-intake-{actor}@example.com", "password": secrets.token_urlsafe(24)}
            user = User(id=uuid4(), email=account["email"], password_hash=hash_password(account["password"]),
                        role=role, status="active", email_verified=True,
                        display_name=f"Synthetic Intake {actor.title()}")
            db.add(user)
            db.flush()
            accounts[actor], identities[actor], codes[actor] = account, user.id, user.user_code
        db.add(Listing(
            id=UUID(facts["listingId"]), title=facts["listingTitle"], type="villa", transaction="rent",
            price=3000, currency="USD", location=facts["area"], status="active",
            owner_id=identities["owner"], submitted_by=identities["owner"],
            images=[], bedrooms=3, bathrooms=3,
        ))
        db.add(NetworkServicePreference(
            user_id=identities["provider"], service_opt_in=True, member_alias=facts["providerAlias"],
            category_ids=[facts["category"]], property_class_ids=[facts["propertyClass"]], version=1,
        ))
        db.commit()
    base.require(len(set(codes.values())) == 3 and all(type(code) is int and code > 0 for code in codes.values()),
                 "Distinct routable synthetic users required")
    return accounts, identities, codes, facts


def confined(app, accounts, identities, codes, facts, observed, refused):
    from database import SessionLocal
    from models.service_workflow import NetworkServiceRecipient, NetworkServiceRequest
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from utils.auth import _decode_token

    actors = {str(identity): actor for actor, identity in identities.items()}
    login_bodies = {canonical(account) for account in accounts.values()}
    active = False

    async def handle(scope, receive, send):
        nonlocal active
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            return await app(scope, receive, send)
        request = Request(scope, receive)
        raw = await request.body()

        async def reject():
            refused.append(True)
            return await JSONResponse({"detail": "Synthetic service intake mutation refused"}, 405,
                                      headers={"Cache-Control": "no-store"})(scope, receive, send)

        if len(raw) > 24576:
            return await reject()
        try:
            body = json.loads(raw) if request.headers.get("content-type", "").split(";")[0] == "application/json" else None
        except (ValueError, UnicodeError):
            body = None
        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": raw, "more_body": False}

        path, method = scope["path"], scope["method"]
        if path == "/auth/login":
            if method != "POST" or canonical(body) not in login_bodies:
                return await reject()
            return await app(scope, replay, send)
        if active or method != "POST" or not isinstance(body, dict):
            return await reject()
        cookie = request.cookies.get("ildr_token")
        actor = actors.get(_decode_token(cookie)) if cookie else None
        submission = body.get("submission_id")
        if not isinstance(submission, str) or not UUID_TEXT.fullmatch(submission):
            return await reject()
        with SessionLocal() as db:
            requests = db.query(NetworkServiceRequest).all()
            recipients = db.query(NetworkServiceRecipient).all()
            base.require(len(requests) <= 1 and len(recipients) <= 1, "Bounded synthetic service intake required")
            request_id = str(requests[0].id) if requests else None
            recipient_id = str(recipients[0].id) if recipients else None
            recipient_version = recipients[0].version if recipients else None
        action = None
        if actor == "owner" and path == NETWORK + "/service-requests" and request_id is None:
            expected = {
                "submission_id": submission, "property_id": None, "listing_id": facts["listingId"],
                "category_id": facts["category"], "property_class": facts["propertyClass"],
                "title": facts["title"], "brief": facts["brief"], "area_label": facts["area"],
                "requester_alias": facts["requesterAlias"], "desired_start_on": None,
                "recipient_user_codes": [codes["provider"]], "shared_brief_acknowledged": True,
            }
            if canonical(body) == canonical(expected):
                action = "request_create"
        elif actor == "provider" and request_id and recipient_id:
            prefix = f"{NETWORK}/service-requests/{request_id}/recipients/{recipient_id}"
            if path == prefix + "/accept" and body == {
                "submission_id": submission, "expected_recipient_version": recipient_version,
            } and type(body.get("expected_recipient_version")) is int and recipient_version == 1:
                action = "recipient_accept"
            elif path == prefix + "/proposals":
                expected = {
                    "submission_id": submission, "expected_recipient_version": recipient_version,
                    "current_proposal_id": None, "expected_proposal_version": None,
                    "terms": {"amount": "175.00", "currency": "USD", "pricing_basis": "fixed",
                              "scope_text": facts["quoteScope"], "exclusions": facts["quoteExclusions"],
                              "estimated_days": 2, "valid_until": facts["validUntil"]},
                }
                if canonical(body) == canonical(expected) and recipient_version == 2:
                    action = "provider_quote"
        if action is None or observed[action] >= EXPECTED[action]:
            return await reject()
        active = True
        status = None

        async def capture(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await app(scope, replay, capture)
            if status is not None and 200 <= status < 300:
                observed[action] += 1
        finally:
            active = False

    return handle


def verify_database(identities, codes, facts, observed, refused, attempts):
    from database import SessionLocal
    from models.lead import Lead
    from models.lead_delivery import LeadDeliveryJob, LeadDeliveryWorkerState
    from models.listing import Listing
    from models.professional_profile import ProfessionalProfile
    from models.property_network import NetworkMedia, NetworkProject, NetworkProperty, SocialNetworkProfile
    from models.service_workflow import (
        NetworkServiceAction, NetworkServicePreference, NetworkServiceProposal,
        NetworkServiceRecipient, NetworkServiceRequest,
    )
    from models.user import User

    with SessionLocal() as db:
        requests, recipients = db.query(NetworkServiceRequest).all(), db.query(NetworkServiceRecipient).all()
        proposals, actions = db.query(NetworkServiceProposal).all(), db.query(NetworkServiceAction).all()
        base.require(len(requests) == len(recipients) == len(proposals) == 1 and len(actions) == 3,
                     "Exactly one real request recipient and first quote required")
        request, recipient, proposal = requests[0], recipients[0], proposals[0]
        base.require(str(request.listing_id) == facts["listingId"] and request.property_id is None
                     and request.custodian_id == request.requester_id == identities["owner"]
                     and request.title == facts["title"] and request.brief == facts["brief"]
                     and request.category_id == facts["category"] and request.property_class == facts["propertyClass"]
                     and request.area_label == facts["area"] and request.requester_alias == facts["requesterAlias"]
                     and request.recipient_user_codes == [codes["provider"]] and request.desired_start_on is None
                     and request.source_listing_type == "villa" and request.status == "open" and request.version == 1
                     and request.project_id is None and not request.custody_closed,
                     "Actual managed listing and shared request fields required")
        base.require(recipient.request_id == request.id and recipient.provider_id == identities["provider"]
                     and recipient.user_code == codes["provider"] and recipient.member_alias == facts["providerAlias"]
                     and recipient.status == "accepted" and recipient.version == 3
                     and recipient.current_proposal_id == proposal.id
                     and proposal.request_id == request.id and proposal.recipient_id == recipient.id
                     and proposal.author_id == identities["provider"] and proposal.parent_proposal_id is None
                     and proposal.kind == "provider_quote" and proposal.status == "submitted"
                     and proposal.revision_no == proposal.version == 1 and proposal.amount == Decimal("175.00")
                     and proposal.currency == "USD" and proposal.pricing_basis == "fixed"
                     and proposal.scope_text == facts["quoteScope"] and proposal.exclusions == facts["quoteExclusions"]
                     and proposal.estimated_days == 2 and proposal.valid_until.isoformat() == facts["validUntil"],
                     "Accepted recipient and exact retained first quote required")
        indexed = {action.operation: action for action in actions}
        base.require(set(indexed) == set(EXPECTED)
                     and indexed["request_create"].actor_id == identities["owner"]
                     and indexed["request_create"].target_id == request.id
                     and indexed["request_create"].result_versions == {"request_version": 1}
                     and indexed["recipient_accept"].actor_id == indexed["provider_quote"].actor_id == identities["provider"]
                     and indexed["recipient_accept"].recipient_id == indexed["provider_quote"].recipient_id == recipient.id
                     and indexed["recipient_accept"].result_versions == {"recipient_version": 2}
                     and indexed["provider_quote"].proposal_id == proposal.id
                     and indexed["provider_quote"].result_versions == {"recipient_version": 3, "proposal_version": 1}
                     and len({action.submission_id for action in actions}) == 3
                     and all(action.request_id == request.id and re.fullmatch(r"[a-f0-9]{64}", action.creation_sha256)
                             for action in actions), "Three actual immutable action identities required")
        preference = db.query(NetworkServicePreference).one()
        listing = db.query(Listing).one()
        base.require(preference.user_id == identities["provider"] and preference.service_opt_in
                     and preference.member_alias == facts["providerAlias"] and preference.version == 1
                     and preference.category_ids == [facts["category"]]
                     and preference.property_class_ids == [facts["propertyClass"]]
                     and str(listing.id) == facts["listingId"] and listing.title == facts["listingTitle"]
                     and listing.owner_id == identities["owner"] and listing.status == "active",
                     "Original opt in preference and managed listing retained")
        base.require(db.query(User).count() == db.query(User).filter_by(status="active", email_verified=True).count() == 3
                     and all(db.query(model).count() == 0 for model in (
                         Lead, LeadDeliveryJob, LeadDeliveryWorkerState, ProfessionalProfile,
                         NetworkProperty, NetworkProject, NetworkMedia, SocialNetworkProfile)),
                     "No incidental users publication projects or communications")
    base.require(observed == EXPECTED and not refused and not attempts,
                 "Exact isolated intake actions and zero provider calls required")
    return {"status": "PASS", "users": 3, "listings": 1, "opted_in_preferences": 1,
            "requests": 1, "recipients": 1, "accepted_recipients": 1, "first_quotes": 1,
            "retained_actions": 3, "successful_ui_mutations": 3, "no_seeded_requests_or_quotes": True,
            "live_provider_calls": 0, "incidental_leads_or_jobs": 0, "projects_created": 0,
            "all_accounts_active": True}


def run():
    application = base.verified_checkout()
    url = base.claim_disposable_database()
    base.configure_application(application, url)
    import httpx
    import requests
    import resend
    import uvicorn

    observed, refused, attempts = Counter(), [], []

    def refuse_transport(*_args, **_kwargs):
        attempts.append(True)
        raise RuntimeError("External transport unavailable")

    with (patch.object(httpx.HTTPTransport, "handle_request", refuse_transport),
          patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse_transport),
          patch.object(requests.Session, "send", refuse_transport),
          patch.object(urllib_request, "urlopen", refuse_transport),
          patch.object(resend.Emails, "send", refuse_transport)):
        from main import app
        accounts, identities, codes, facts = seed_accounts()
        path = base.FIXTURE_FILE
        base.require(not path.exists() and not path.is_symlink(), "Fresh private fixture file required")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        owned = os.fstat(fd)
        handlers = {}
        try:
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                stream.write(canonical({"accounts": accounts, "codes": codes, "facts": facts}) + "\n")
            server = uvicorn.Server(uvicorn.Config(confined(app, accounts, identities, codes, facts, observed, refused),
                host=base.HOST, port=base.API_PORT, lifespan="off", access_log=False, log_level="warning"))

            def shutdown(_number, _frame):
                server.should_exit = True

            for number in (signal.SIGINT, signal.SIGTERM):
                handlers[number] = signal.signal(number, shutdown)
            base.advance("service_intake_browser")
            server.run()
            base.require(server.started, "Actual synthetic server required")
            base.advance("verify_service_intake")
            report = verify_database(identities, codes, facts, observed, refused, attempts)
        finally:
            if path.exists() or path.is_symlink():
                current = path.lstat()
                if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                    path.unlink()
            for number, handler in handlers.items():
                signal.signal(number, handler)
    base.require(not path.exists() and not path.is_symlink(), "Private fixture file cleanup required")
    print("SERVICE_INTAKE_FIXTURE_RESULT " + canonical({**report, "login_file_absent": True}), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        print("SERVICE_INTAKE_FIXTURE_FAILURE " + canonical({
            "stage": base.stage, "error_type": type(error).__name__,
        }), flush=True)
        raise SystemExit(1) from None
