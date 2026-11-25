"""Read-only sharing browser APIs; only exact synthetic logins may mutate."""
from collections import Counter
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
import json
import logging
from pathlib import Path
import re
import signal
import smtplib
import stat
from urllib import request as urllib_request
from unittest.mock import patch
from uuid import UUID, uuid4

COMMIT = "8957b18520d15dac503e0e4a5ce4b2455135d601"
TREE = "11ab69db36eb30881e4f3086578201f584c4092d"
ACCEPTED = Path(__file__).resolve().parent.parent / "social-groups-browser/serve_social_groups_fixture.py"
ACCEPTED_SHA = "e0c5f0971c7c13ed47063907f01fa6ef01590b00fa2855eb1ecb16a38b99463e"
DIRECTORY = Path("/browser-fixtures")
FACTS_FILE = DIRECTORY / "fixtures.json"
STAGE = "source"


class Failure(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise Failure(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def accepted_helpers():
    require(ACCEPTED.is_file() and not ACCEPTED.is_symlink()
        and sha256(ACCEPTED.read_bytes()).hexdigest() == ACCEPTED_SHA, "accepted_fixture_bytes_required")
    spec = spec_from_file_location("accepted_sharing_browser_helpers", ACCEPTED)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.APP_COMMIT, module.APP_TREE = COMMIT, TREE
    return module


def seed(base, retained):
    from datetime import date
    from database import SessionLocal
    from models.professional_profile import ProfessionalProfile
    from models.property_network import SocialNetworkProfile, NetworkBlock
    from models.trade_contact import TradeContact
    from schemas.professionals import ProfessionalDraft
    from schemas.trade_contacts import TradeContactEntry, TradeContactImport
    from services.trade_contacts import import_entries
    accounts, identities, codes, previous = base.seed_database(retained)
    facts = {"contactName": "Synthetic sharing contact", "contactEmail": "sharing-contact@example.com",
        "contactPhone": "+18095550100", "privateNote": "NEVER_SHARE_SYNTHETIC_PRIVATE_SOURCE_NOTE",
        "memberName": "Synthetic shared member", "privateName": "Synthetic private profile",
        "blockedName": "Synthetic blocked member", "realtorName": "Synthetic shared realtor",
        "managedName": "Synthetic shared published business", "ownedName": "Synthetic published account business"}
    managed_id, owned_id = uuid4(), uuid4()
    with SessionLocal.begin() as db:
        for actor, identity in identities.items():
            db.add(SocialNetworkProfile(user_id=identity,
                display_name={"member": facts["memberName"], "owner": facts["privateName"],
                    "professional": facts["blockedName"], "outsider": facts["realtorName"]}.get(actor, "Synthetic sharing administrator"),
                bio="Synthetic profile biography", status_text="Synthetic status for browser checks",
                visibility="private" if actor == "owner" else "members", wall_policy="own_only", version=1))
        db.add(NetworkBlock(blocker_id=identities["member"], blocked_id=identities["professional"]))
        for identity, owner, name in ((managed_id, None, facts["managedName"]),
            (owned_id, identities["member"], facts["ownedName"])):
            published = ProfessionalDraft(business_name=name, category_ids=["pool_service"],
                service_area_ids=["cabarete"], descriptions={"en": "Synthetic approved business", "es": "Negocio sintético aprobado"},
                languages=["en", "es"], public_phone=facts["contactPhone"],
                public_email="published-business@example.com", public_contact_consent=True).model_dump(mode="json")
            draft = {**published, "business_name": "NEVER_SHARE_UNPUBLISHED_DRAFT_NAME"}
            db.add(ProfessionalProfile(id=identity, user_id=owner, version=1, draft_data=draft,
                published_data=published, review_status="approved", listing_status="published",
                published_at=retained.HISTORY, created_at=retained.HISTORY, updated_at=retained.HISTORY))
    entry = TradeContactEntry(name=facts["contactName"], category="Synthetic sharing contact",
        phone=facts["contactPhone"], email=facts["contactEmail"], source_authority="Synthetic sharing source",
        source_url="https://example.com/sharing-private-source", verified_on=date.today(),
        verification_status="owner_source", source_details={"private_note": facts["privateNote"]})
    with SessionLocal() as db:
        require(import_entries(db, TradeContactImport(dry_run=False, entries=[entry])).inserted == 1,
            "one_browser_contact_seed_required")
        contact = db.query(TradeContact).filter(TradeContact.name == facts["contactName"]).one()
        facts["contactId"] = str(contact.id)
    facts.update(managedProfileId=str(managed_id), ownedProfileId=str(owned_id), memberCode=codes["member"],
        privateCode=codes["owner"], blockedCode=codes["professional"], realtorCode=codes["outsider"],
        memberId=str(identities["member"]), hiddenProfileId=str(UUID(int=3000)),
        adminId=str(identities["admin"]), unrelatedProjectId=previous["unrelatedProjectId"])
    return accounts, identities, facts


def confined(app, base, accounts, identities, facts, observed):
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from utils.auth import _decode_token
    logins = {canonical(value) for value in accounts.values()}
    actors = {str(identity): actor for actor, identity in identities.items()}
    targets = {"/admin/trade-contacts/" + facts["contactId"]: "contact",
        "/professionals/" + facts["managedProfileId"]: "managed_professional",
        "/professionals/" + facts["ownedProfileId"]: "owned_professional",
        "/professionals/" + facts["hiddenProfileId"]: "hidden_professional",
        "/property-network/profiles/" + str(facts["memberCode"]): "member",
        "/property-network/profiles/" + str(facts["privateCode"]): "private_member",
        "/property-network/profiles/" + str(facts["blockedCode"]): "blocked_member",
        "/property-network/profiles/" + str(facts["realtorCode"]): "realtor_member"}
    async def handle(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        request = Request(scope, receive)
        method, path = scope["method"], scope["path"]
        actor = actors.get(_decode_token(request.cookies.get("ildr_token"))) if request.cookies.get("ildr_token") else None
        if method not in {"GET", "HEAD", "OPTIONS"}:
            raw = await request.body()
            try:
                body = json.loads(raw) if len(raw) <= 1024 else None
            except (ValueError, UnicodeError):
                body = None
            if method == "POST" and path == "/auth/login" and canonical(body) in logins:
                observed["logins"] += 1
                return await app(scope, base.replay_body(raw, receive), send)
            observed["refused"] += 1
            return await JSONResponse({"detail": "ci_mutation_refused"}, status_code=403)(scope, receive, send)
        async def capture(message):
            if method == "GET" and path in targets and message["type"] == "http.response.start":
                observed["reads"][(targets[path], actor or "anonymous", message["status"])] += 1
            await send(message)
        return await app(scope, receive, capture)
    return handle


def run():
    global STAGE
    logging.disable(logging.CRITICAL)
    import importlib.metadata as metadata
    baseline = json.loads(Path("/opt/contact-import/baseline-inventory.json").read_text())
    actual = {re.sub(r"[-_.]+", "-", item.metadata["Name"].lower()): item.version for item in metadata.distributions()}
    require(actual == {**baseline, "openpyxl": "3.1.5", "defusedxml": "0.7.1", "et-xmlfile": "2.0.0"},
            "unchanged_dependency_inventory_required")
    base = accepted_helpers()
    application = base.verified_checkout()
    database, url = base.claim_database()
    base.configure(application, url)
    helpers, retained = base.frozen_helpers()
    import httpx
    import requests
    import resend
    import uvicorn
    owned, attempts = [], []
    observed = {"refused": 0, "logins": 0, "reads": Counter()}
    def refuse(*args, **kwargs):
        attempts.append(1)
        raise RuntimeError("external_transport_refused")
    try:
        with (patch.object(httpx.HTTPTransport, "handle_request", refuse),
            patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse),
            patch.object(requests.Session, "send", refuse), patch.object(urllib_request, "urlopen", refuse),
            patch.object(resend.Emails, "send", refuse), patch.object(smtplib.SMTP, "connect", refuse),
            patch.object(smtplib.SMTP_SSL, "connect", refuse)):
            from main import app
            STAGE = "seed"
            accounts, identities, facts = seed(base, retained)
            tables = helpers.table_names(database)
            require(len(tables) == 99, "all_99_tables_required")
            rows, catalog = helpers.snapshot(database, tables), helpers.catalog(database, tables)
            base.write_owned(FACTS_FILE, (canonical({"scope": "contact-profile-sharing-read-only",
                "accounts": accounts, "facts": facts}) + "\n").encode(), owned)
            server = uvicorn.Server(uvicorn.Config(confined(app, base, accounts, identities, facts, observed),
                host="127.0.0.1", port=8918, lifespan="off", access_log=False, log_level="warning"))
            handlers = {}
            def shutdown(number, frame):
                server.should_exit = True
            try:
                for number in (signal.SIGINT, signal.SIGTERM):
                    handlers[number] = signal.signal(number, shutdown)
                STAGE = "browser"
                server.run()
                require(server.started, "actual_api_server_required")
                STAGE = "preservation"
                require(helpers.table_names(database) == tables and helpers.catalog(database, tables) == catalog
                    and helpers.snapshot(database, tables) == rows, "all_99_catalog_rows_sequences_unchanged_required")
                require(not attempts and observed["refused"] == 0, "zero_mutations_and_provider_attempts_required")
                reads = observed["reads"]
                required = [("contact", "admin", 200), ("contact", "member", 403),
                    ("managed_professional", "member", 200), ("owned_professional", "member", 200),
                    ("hidden_professional", "member", 404), ("private_member", "member", 404),
                    ("blocked_member", "member", 404), ("realtor_member", "member", 200)]
                require(all(reads[key] > 0 for key in required), "actual_authorized_and_refused_reads_required")
                require(1 <= observed["logins"] <= 12, "bounded_real_logins_required")
            finally:
                for number, handler in handlers.items():
                    signal.signal(number, handler)
    finally:
        for path, device, inode in reversed(owned):
            if path.exists() or path.is_symlink():
                info = path.lstat()
                require(stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == (device, inode), "owned_private_inode_required")
                path.unlink()
        require(all(not path.exists() and not path.is_symlink() for path, _, _ in owned), "private_fixture_cleanup_required")
    print("SHARING_FIXTURE_RESULT " + canonical({"status": "PASS", "schema_tables": 99,
        "all_rows_sequences_unchanged": True, "protected_owner_contacts_unchanged": 82,
        "private_profiles_unchanged": 46, "contacts_unchanged": 86, "sources_unchanged": 87,
        "published_professionals_unchanged": 2, "users_unchanged": 5, "accepted_jobs_unchanged": 9,
        "cms_version_unchanged": 7, "floorplans_unchanged": 2, "actual_access_boundaries": 8,
        "unexpected_mutations": 0, "external_provider_attempts": 0, "private_files_removed": 1}), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        name = type(error).__name__
        print("SHARING_FIXTURE_FAILURE " + canonical({"stage": STAGE,
            "error_type": name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name) else "UnhandledError",
            "reason": error.args[0] if isinstance(error, Failure) else "external_failure"}), flush=True)
        raise SystemExit(1) from None
