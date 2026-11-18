"""Real read-only directory maps with exact synthetic-row preservation."""
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import smtplib
import stat
import sys
from urllib import request as urllib_request
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "platform-browser" / "accepted-core"))
import serve_admin_share_fixture as base

base.APP_COMMIT = "4d21cb3ae2b14475828ff8fbe710b22c34c3a53b"
base.APP_TREE = "1e3f72978515a58733188caa235cb6ee61190a26"
base.MIGRATION_HEAD = "b19d8e4c720a"
base.API_PORT = 8918
DIRECTORY = Path("/browser-fixtures")
PREFIX = "/admin/trade-contacts"
FACTS = {
    "searchTerm": "Synthetic area", "category": "Synthetic property service",
    "longSubtype": "Synthetic specialty ".ljust(150, "x"), "serviceArea": "North coast",
    "sosuaKey": "DO:sosua", "cabareteKey": "DO:cabarete", "provinceKey": "DO:puerto-plata",
    "professionalSearch": "Synthetic area professional",
    "professionalNames": ["Synthetic area professional overlap", "Synthetic area professional single", "Synthetic area professional nationwide"],
    "csvName": "Synthetic map preview contact", "sourceDate": "2026-11-18",
    "tradeNames": {key: "Synthetic area " + label for key, label in (
        ("sosua", "Sosua specialist"), ("cabarete", "Cabarete specialist"), ("fallback", "province fallback"),
        ("conflict", "conflicting record"), ("unknown", "unknown area"), ("nationwide", "nationwide service"))},
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def write_owned(path, data, owned):
    base.require(path.parent == DIRECTORY and DIRECTORY.resolve(strict=True) == DIRECTORY
                 and not path.exists() and not path.is_symlink(), "Fresh private fixture path required")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    identity = os.fstat(fd)
    owned.append((path, identity.st_dev, identity.st_ino))
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
    base.require(stat.S_IMODE(path.stat().st_mode) == 0o600, "Owner-only fixture mode required")


def seed_database():
    import bootstrap_db
    from alembic import command
    from alembic.config import Config
    from database import SessionLocal
    from models.professional_profile import ProfessionalProfile
    from models.trade_contact import TradeContact, TradeContactSource
    from models.user import User
    from routes.professionals import _moderate
    from schemas.professionals import ProfessionalDraft, ReviewBody
    from schemas.trade_contacts import TradeContactEntry, TradeContactImport
    from services.trade_contacts import import_entries
    from sqlalchemy import null, text
    from utils.security import hash_password

    base.advance("bootstrap_database")
    bootstrap_db.main()
    command.upgrade(Config("alembic.ini"), "head")
    accounts = {role: {"email": f"browser-map-{role}@example.com", "password": secrets.token_urlsafe(24)} for role in base.ROLES}
    identities, profile_ids = {}, []
    protected = [TradeContactEntry(
        name=f"Protected owner contact {index:03d}", category="Protected owner lender",
        province="Protected unknown region", locality="Protected unknown town",
        source_authority="Synthetic protected owner source", source_url=f"https://example.com/protected/{index}",
        verified_on=FACTS["sourceDate"], verification_status="owner_source",
        source_details={"private_note": "Retain the synthetic owner context", "source_description": "Protected source note"},
    ) for index in range(1, 83)]
    common = {"category": FACTS["category"], "source_authority": "Synthetic map source",
              "source_url": "https://example.com/map-source", "verified_on": FACTS["sourceDate"],
              "verification_status": "public_source", "phone": "+18095550123"}
    specifications = [
        ("sosua", "Puerto Plata", "Sosúa", FACTS["longSubtype"], FACTS["serviceArea"]),
        ("cabarete", "Puerto Plata", "Cabarete", "Residential", FACTS["serviceArea"]),
        ("fallback", "Puerto Plata", "Uncatalogued quarter", "General", "Puerto Plata"),
        ("conflict", "Santiago", "Sosúa", "Structural", "Mixed area"),
        ("unknown", None, None, "General", None),
        ("nationwide", None, None, "Nationwide", "Nationwide"),
    ]
    synthetic = [TradeContactEntry(**common, name=FACTS["tradeNames"][key], province=province, locality=locality,
                                   subtype=subtype, service_area=service) for key, province, locality, subtype, service in specifications]
    with SessionLocal() as db:
        base.require(db.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [base.MIGRATION_HEAD]
                     and all(db.query(model).count() == 0 for model in (User, TradeContact, TradeContactSource, ProfessionalProfile)),
                     "Fresh owned directory-map database required")
        users = {}
        for role, account in accounts.items():
            user = User(id=uuid4(), email=account["email"], password_hash=hash_password(account["password"]),
                        role=role, status="active", email_verified=True, display_name=f"Synthetic Map {role.title()}")
            db.add(user)
            users[role] = user
            identities[role] = user.id
        for index in range(1, 47):
            draft = ProfessionalDraft(business_name=f"Private managed fixture {index:03d}",
                                      descriptions={"en": "Private draft sentinel", "es": "Borrador privado"}).model_dump(mode="json")
            db.add(ProfessionalProfile(id=uuid4(), user_id=None, version=1, draft_data=draft, published_data=null(),
                                       review_status="draft", listing_status="hidden"))
        for name, areas in zip(FACTS["professionalNames"], (["sosua", "cabarete"], ["sosua"], ["nationwide"])):
            identity = uuid4()
            draft = ProfessionalDraft(business_name=name, category_ids=["pool_service"], service_area_ids=areas,
                languages=["en", "es"], descriptions={"en": "Synthetic public business service", "es": "Servicio público sintético"},
                public_phone="+18095550190", public_contact_consent=True).model_dump(mode="json")
            db.add(ProfessionalProfile(id=identity, user_id=None, version=1, draft_data=draft, published_data=null(),
                                       review_status="pending", listing_status="hidden", submitted_at=datetime.now(timezone.utc)))
            profile_ids.append(identity)
        db.commit()
        result = import_entries(db, TradeContactImport(dry_run=False, entries=protected + synthetic))
        base.require(result.model_dump() == {"inserted": 88, "updated": 0, "unchanged": 0}, "Exact synthetic contact baseline required")
        for identity in profile_ids:
            _moderate(identity, ReviewBody(expected_version=1, note="Synthetic fixture approval"), "approve", users["admin"], db)
    return accounts, identities, {**FACTS, "profileId": str(profile_ids[0])}


def snapshot():
    from database import SessionLocal
    from sqlalchemy import text
    with SessionLocal() as db:
        return tuple(db.execute(text(f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb)::text FROM {table} t")).scalar_one()
                     for table in ("users", "trade_contacts", "trade_contact_sources", "professional_profiles", "professional_profile_revisions"))


def csv_fixture():
    columns = ["name", "category", "source_authority", "source_url", "verified_on", "verification_status", "phone"]
    values = [FACTS["csvName"], "Preview-only service", "Synthetic preview source", "https://example.com/preview-source",
              FACTS["sourceDate"], "public_source", "+18095550123"]
    return (",".join(columns) + "\n" + ",".join(values) + "\n").encode(), columns, values


def replay_body(raw, receive):
    sent = False
    async def replay():
        nonlocal sent
        if sent:
            return await receive()
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}
    return replay


def confined(app, accounts, identities, csv_data, columns, values, reads, previews, denials, refused):
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from utils.auth import _decode_token
    actors = {str(identity): role for role, identity in identities.items()}
    login_bodies = {canonical(account) for account in accounts.values()}
    digest = sha256(csv_data).hexdigest()

    async def handle(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        request = Request(scope, receive)
        token = request.cookies.get("ildr_token")
        actor = actors.get(_decode_token(token)) if token else None
        path, method = scope["path"], scope["method"]

        async def capture(message):
            if message["type"] == "http.response.start":
                status = message["status"]
                if status == 200 and method == "GET" and path in {PREFIX + "/map", "/professionals/map"}:
                    reads["admin" if path.startswith(PREFIX) else "member"] += 1
                if path.startswith(PREFIX) and status == 403 and actor in {"owner", "realtor", "buyer"}:
                    denials.add(actor)
                if status == 200 and method == "POST" and path in {PREFIX + "/preview-file", PREFIX + "/preview-mapping"}:
                    previews[path.rsplit("/", 1)[-1]] += 1
            await send(message)

        if method in {"GET", "HEAD", "OPTIONS"}:
            return await app(scope, receive, capture)

        async def reject():
            refused.append(True)
            return await JSONResponse({"detail": "Read-only synthetic directory mutation refused"}, 405,
                                      headers={"Cache-Control": "no-store"})(scope, receive, send)

        raw = await request.body()
        if method != "POST" or len(raw) > 2 * 1024 * 1024:
            return await reject()
        if path == "/auth/logout" and raw in (b"", b"{}"):
            return await app(scope, replay_body(raw, receive), capture)
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if path == PREFIX + "/preview-file" and content_type == "multipart/form-data":
            form = await Request(scope, replay_body(raw, receive)).form()
            try:
                upload = form.get("file")
                if (upload is None or not hasattr(upload, "read") or upload.filename != "map-preview.csv"
                        or set(form) - {"file", "sheet", "delimiter"} or form.get("sheet") not in (None, "")
                        or form.get("delimiter", "auto") not in {"auto", "comma"} or await upload.read() != csv_data):
                    return await reject()
            finally:
                await form.close()
            return await app(scope, replay_body(raw, receive), capture)
        try:
            body = json.loads(raw) if content_type == "application/json" else None
        except (ValueError, UnicodeError):
            body = None
        if path == "/auth/login" and canonical(body) in login_bodies:
            return await app(scope, replay_body(raw, receive), capture)
        if path == PREFIX + "/preview-mapping" and isinstance(body, dict):
            table, defaults = body.get("table"), body.get("defaults")
            if (not isinstance(table, dict) or table.get("file_sha256") != digest or table.get("columns") != columns
                    or table.get("rows") != [{"row_number": 2, "values": values}]
                    or not isinstance(defaults, dict) or set(defaults) - {"verification_status"}
                    or defaults.get("verification_status", "owner_source") not in {"owner_source", "public_source"}):
                return await reject()
            # Safe owned input still reaches the real API for anonymous and
            # non-Admin actors. This wrapper never manufactures auth denials.
            return await app(scope, replay_body(raw, receive), capture)
        return await reject()
    return handle


def verify_database(before, reads, previews, denials, refused, attempts):
    from database import SessionLocal
    from models.lead import Lead
    from models.lead_delivery import LeadDeliveryJob, LeadDeliveryWorkerState
    from models.professional_profile import ProfessionalProfile
    from models.property_network import NetworkProject
    from models.trade_contact import TradeContact, TradeContactSource
    from models.user import User
    from sqlalchemy import text
    base.require(snapshot() == before and not refused and not attempts, "Every seeded directory and account row must remain unchanged")
    base.require(reads["admin"] > 0 and reads["member"] > 0 and previews["preview-file"] > 0
                 and previews["preview-mapping"] > 0 and denials == {"owner", "realtor", "buyer"},
                 "Actual map reads preview cancel and real role denials required")
    with SessionLocal() as db:
        base.require(db.query(TradeContact).count() == db.query(TradeContactSource).count() == 88
                     and db.query(ProfessionalProfile).count() == 49
                     and db.query(User).count() == db.query(User).filter_by(status="active", email_verified=True).count() == 4
                     and all(db.query(model).count() == 0 for model in (Lead, LeadDeliveryJob, LeadDeliveryWorkerState, NetworkProject)),
                     "Exact baseline counts and no incidental application activity required")
        base.require(db.execute(text("SELECT count(*) FROM professional_profiles WHERE user_id IS NULL AND published_data IS NULL "
                                     "AND review_status='draft' AND listing_status='hidden' AND version=1")).scalar_one() == 46,
                     "Exactly46 managed drafts remain private")
    return {"status": "PASS", "contacts": 88, "sources": 88, "protected_contacts_unchanged": 82,
            "protected_sources_unchanged": 82, "private_profiles_unchanged": 46, "published_profiles_unchanged": 3,
            "users": 4, "final_imports": 0, "successful_ui_mutations": 0, "actual_nonadmin_denial_roles": 3,
            "leads": 0, "jobs": 0, "worker_rows": 0, "projects": 0, "unexpected_mutations": 0,
            "external_provider_attempts": 0}


def run():
    logging.disable(logging.CRITICAL)
    application = base.verified_checkout()
    url = base.claim_disposable_database()
    base.configure_application(application, url)
    os.environ.update(TRANSACTION_DOCUMENT_BUCKET="", GHL_WEBHOOK_SECRET="")
    import httpx
    import requests
    import resend
    import uvicorn
    owned, attempts, refused = [], [], []
    reads, previews, denials = Counter(), Counter(), set()

    def refuse_transport(*_args, **_kwargs):
        attempts.append(True)
        raise RuntimeError("External transport unavailable")

    try:
        with (patch.object(httpx.HTTPTransport, "handle_request", refuse_transport),
              patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse_transport),
              patch.object(requests.Session, "send", refuse_transport), patch.object(urllib_request, "urlopen", refuse_transport),
              patch.object(resend.Emails, "send", refuse_transport), patch.object(smtplib.SMTP, "connect", refuse_transport)):
            from main import app
            accounts, identities, facts = seed_database()
            before = snapshot()
            csv_data, columns, values = csv_fixture()
            csv_path = DIRECTORY / "map-preview.csv"
            write_owned(csv_path, csv_data, owned)
            fixtures = {"scope": "directory-map-only", "accounts": accounts, "files": {"csv": str(csv_path)}, "facts": facts}
            write_owned(base.FIXTURE_FILE, (canonical(fixtures) + "\n").encode(), owned)
            server = uvicorn.Server(uvicorn.Config(confined(app, accounts, identities, csv_data, columns, values, reads,
                previews, denials, refused), host=base.HOST, port=base.API_PORT, lifespan="off", access_log=False, log_level="warning"))
            handlers = {}
            def shutdown(_number, _frame):
                server.should_exit = True
            try:
                for number in (signal.SIGINT, signal.SIGTERM):
                    handlers[number] = signal.signal(number, shutdown)
                base.advance("directory_map_browser")
                server.run()
                base.require(server.started, "Actual directory map server required")
                base.advance("verify_directory_map")
                report = verify_database(before, reads, previews, denials, refused, attempts)
            finally:
                for number, handler in handlers.items():
                    signal.signal(number, handler)
    finally:
        for path, device, inode in reversed(owned):
            if path.exists() or path.is_symlink():
                current = path.lstat()
                base.require(stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (device, inode),
                             "Owned fixture inode required for cleanup")
                path.unlink()
        base.require(all(not path.exists() and not path.is_symlink() for path, _, _ in owned), "Private fixture cleanup required")
    print("DIRECTORY_MAP_FIXTURE_RESULT " + canonical({**report, "private_files_removed": 2}), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        error_type = type(error).__name__
        print("DIRECTORY_MAP_FIXTURE_FAILURE " + canonical({"stage": base.stage,
              "error_type": error_type if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", error_type) else "UnhandledError"}), flush=True)
        raise SystemExit(1) from None
