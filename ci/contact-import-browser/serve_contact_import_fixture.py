"""Serve real Admin contact imports against owned synthetic files and rows."""

from collections import Counter
from hashlib import sha256
from io import BytesIO
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

base.APP_COMMIT = "8ed67c8d54ee64017c7e44095f35d954272ef668"
base.APP_TREE = "0f6a7a0b21f57558aa5c67f216e7eea39b5b42b0"
base.MIGRATION_HEAD = "b19d8e4c720a"
base.API_PORT = 8918
PREFIX = "/admin/trade-contacts"
DIRECTORY = Path("/browser-fixtures")
FACTS = {
    "csvName": "Synthetic CSV pool specialist",
    "xlsxName": "Synthetic XLSX property supplier",
    "category": "Synthetic pool service",
    "province": "Puerto Plata",
    "locality": "Cabarete",
    "sourceAuthority": "Synthetic owner-reviewed source",
    "sourceUrl": "https://example.com/contact-import-source",
    "sourceDate": "2026-11-18",
    "contactSheet": "Contactos",
    "hiddenSentinel": "SYNTHETIC HIDDEN CONTACT SENTINEL",
    "searchTerm": "Synthetic",
}
EXPECTED_SAVES = Counter(csv=2, xlsx=1)


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


def source_fields(status):
    return {"category": FACTS["category"], "province": FACTS["province"], "locality": FACTS["locality"],
            "source_authority": FACTS["sourceAuthority"], "source_url": FACTS["sourceUrl"],
            "verified_on": FACTS["sourceDate"], "verification_status": status}


def seed_database():
    import bootstrap_db
    from alembic import command
    from alembic.config import Config
    from database import SessionLocal
    from models.professional_profile import ProfessionalProfile
    from models.trade_contact import TradeContact, TradeContactSource
    from models.user import User
    from schemas.professionals import ProfessionalDraft
    from schemas.trade_contacts import TradeContactEntry, TradeContactImport
    from services.trade_contacts import import_entries
    from sqlalchemy import null, text
    from utils.security import hash_password

    base.advance("bootstrap_database")
    bootstrap_db.main()
    command.upgrade(Config("alembic.ini"), "head")
    accounts = {role: {"email": f"browser-contact-{role}@example.com", "password": secrets.token_urlsafe(24)}
                for role in base.ROLES}
    identities = {}
    protected = [TradeContactEntry(
        name=f"Protected owner contact {index:03d}", category="Protected owner lender",
        province="Santo Domingo", locality="Protected locality",
        source_authority="Synthetic protected owner source", source_url=f"https://example.com/protected/{index}",
        verified_on=FACTS["sourceDate"], verification_status="owner_source",
        source_details={"private_note": "Retain this synthetic owner context", "source_description": "Protected source note"},
    ) for index in range(1, 83)]
    with SessionLocal() as db:
        base.require(db.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
                     == [base.MIGRATION_HEAD]
                     and all(db.query(model).count() == 0 for model in (User, TradeContact, TradeContactSource, ProfessionalProfile)),
                     "Fresh synthetic contact database required")
        for role, account in accounts.items():
            user = User(id=uuid4(), email=account["email"], password_hash=hash_password(account["password"]),
                        role=role, status="active", email_verified=True, display_name=f"Synthetic Contact {role.title()}")
            db.add(user)
            identities[role] = user.id
        for index in range(1, 47):
            draft = ProfessionalDraft(business_name=f"Managed private fixture {index:03d}").model_dump(mode="json")
            db.add(ProfessionalProfile(id=uuid4(), user_id=None, version=1, draft_data=draft,
                                       published_data=null(), review_status="draft", listing_status="hidden"))
        db.commit()
        result = import_entries(db, TradeContactImport(dry_run=False, entries=protected))
        base.require(result.model_dump() == {"inserted": 82, "updated": 0, "unchanged": 0},
                     "Exactly protected owner contacts required")
    return accounts, identities, protected[0].model_dump(mode="json")


def create_input_files(protected, owned):
    from openpyxl import Workbook
    from schemas.trade_contacts import TradeContactEntry

    csv_values = [FACTS["csvName"], "+18095550123", FACTS["category"], FACTS["province"], FACTS["locality"]]
    csv_data = ("Contact label,Phone,Category,Province,Locality\n" + ",".join(csv_values) + "\n").encode()
    workbook = Workbook()
    workbook.active.title = "Instructions"
    workbook.active.append(["Instructions"])
    workbook.active.append(["Select the Contactos worksheet to review contacts."])
    sheet = workbook.create_sheet(FACTS["contactSheet"])
    sheet.append(["Nombre", "Teléfono", "Correo", "Sitio web", "Código postal"])
    sheet.append([FACTS["xlsxName"], "+12025550124", "excel@example.com", "https://example.com/excel", 123])
    sheet["E2"].number_format = "00000"
    hidden = workbook.create_sheet("Hidden contacts")
    hidden.append([FACTS["hiddenSentinel"]])
    hidden.sheet_state = "hidden"
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    xlsx_data = stream.getvalue()
    paths = {"csv": DIRECTORY / "contacts.csv", "xlsx": DIRECTORY / "contacts.xlsx", "json": DIRECTORY / "protected.json"}
    data = {"csv": csv_data, "xlsx": xlsx_data, "json": (canonical([protected]) + "\n").encode()}
    for kind, path in paths.items():
        write_owned(path, data[kind], owned)
    entries = {
        "protected": [protected],
        "csv": [TradeContactEntry(**source_fields("public_source"), name=FACTS["csvName"], phone=csv_values[1],
                                  source_details={"import_file_sha256": sha256(csv_data).hexdigest(), "import_row": "2"}).model_dump(mode="json")],
        "xlsx": [TradeContactEntry(**source_fields("owner_source"), name=FACTS["xlsxName"], phone="+12025550124",
                                   email="excel@example.com", website="https://example.com/excel", postal_code="00123",
                                   source_details={"import_file_sha256": sha256(xlsx_data).hexdigest(), "import_row": "2",
                                                   "import_sheet": FACTS["contactSheet"]}).model_dump(mode="json")],
    }
    return {key: str(path) for key, path in paths.items()}, data, entries


def snapshot(protected_only=False):
    from database import SessionLocal
    from sqlalchemy import text

    contact_where = "WHERE name NOT IN (:csv_name,:xlsx_name)" if protected_only else ""
    source_where = "WHERE contact_id IN (SELECT id FROM trade_contacts " + contact_where + ")" if protected_only else ""
    parameters = {"csv_name": FACTS["csvName"], "xlsx_name": FACTS["xlsxName"]}
    with SessionLocal() as db:
        return tuple(db.execute(text(f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb)::text "
                                     f"FROM (SELECT * FROM {table} {where}) t"), parameters).scalar_one()
                     for table, where in (("trade_contacts", contact_where), ("trade_contact_sources", source_where),
                                          ("professional_profiles", "")))


def csv_snapshot():
    from database import SessionLocal
    from sqlalchemy import text

    with SessionLocal() as db:
        return db.execute(text("SELECT to_jsonb(c)::text,to_jsonb(s)::text FROM trade_contacts c "
                               "JOIN trade_contact_sources s ON s.contact_id=c.id WHERE c.name=:name "
                               "ORDER BY c.id,s.id"), {"name": FACTS["csvName"]}).all()


def replay_body(raw, receive):
    sent = False

    async def replay():
        nonlocal sent
        if sent:
            return await receive()
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    return replay


def confined(app, accounts, identities, input_data, entries, saves, first_csv, denials, refused):
    from schemas.trade_contacts import TradeContactEntry
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from utils.auth import _decode_token

    actors = {str(identity): role for role, identity in identities.items()}
    login_bodies = {canonical(account) for account in accounts.values()}
    default_values = {"category": FACTS["category"], "province": FACTS["province"], "locality": FACTS["locality"],
                      "source_authority": FACTS["sourceAuthority"], "source_url": FACTS["sourceUrl"],
                      "verified_on": FACTS["sourceDate"]}
    file_hashes = {sha256(input_data[kind]).hexdigest() for kind in ("csv", "xlsx")}

    async def handle(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        request = Request(scope, receive)
        token = request.cookies.get("ildr_token")
        actor = actors.get(_decode_token(token)) if token else None
        path, method = scope["path"], scope["method"]
        operation = None

        async def capture(message):
            if message["type"] == "http.response.start":
                if path.startswith(PREFIX) and actor in {"owner", "realtor", "buyer"} and message["status"] == 403:
                    denials.add(actor)
                if operation in EXPECTED_SAVES and actor == "admin" and 200 <= message["status"] < 300:
                    saves[operation] += 1
            await send(message)

        if method in {"GET", "HEAD", "OPTIONS"}:
            return await app(scope, receive, capture)

        async def reject():
            refused.append(True)
            return await JSONResponse({"detail": "Synthetic contact mutation refused"}, 405,
                                      headers={"Cache-Control": "no-store"})(scope, receive, send)

        raw = await request.body()
        if method != "POST" or len(raw) > 2 * 1024 * 1024:
            return await reject()
        if path == "/auth/logout" and actor in accounts and raw in (b"", b"{}"):
            return await app(scope, replay_body(raw, receive), capture)
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if path == PREFIX + "/preview-file" and content_type == "multipart/form-data":
            form = await Request(scope, replay_body(raw, receive)).form()
            try:
                upload = form.get("file")
                if (upload is None or not hasattr(upload, "read") or set(form) - {"file", "sheet", "delimiter"}
                        or form.get("sheet") not in (None, "", "Instructions", FACTS["contactSheet"])
                        or form.get("delimiter", "auto") not in {"auto", "comma"}):
                    return await reject()
                data = await upload.read()
                kind = {"contacts.csv": "csv", "contacts.xlsx": "xlsx"}.get(upload.filename)
                if kind is None or data != input_data[kind]:
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
        if not isinstance(body, dict):
            return await reject()
        if path == PREFIX + "/preview-mapping":
            table, defaults = body.get("table"), body.get("defaults")
            if (not isinstance(table, dict) or table.get("file_sha256") not in file_hashes
                    or not isinstance(defaults, dict) or set(defaults) - (set(default_values) | {"verification_status"})
                    or any(value != default_values.get(key) for key, value in defaults.items() if key != "verification_status")
                    or defaults.get("verification_status") not in {"public_source", "owner_source"}):
                return await reject()
            rows = table.get("rows")
            expected_values = ([FACTS["csvName"], "+18095550123", FACTS["category"], FACTS["province"], FACTS["locality"]],
                               [FACTS["xlsxName"], "+12025550124", "excel@example.com", "https://example.com/excel", "00123"])
            if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
                    or rows[0].get("row_number") != 2 or rows[0].get("values") not in expected_values):
                return await reject()
            return await app(scope, replay_body(raw, receive), capture)
        if path == PREFIX + "/import" and type(body.get("dry_run")) is bool:
            try:
                normalized = [TradeContactEntry.model_validate(entry).model_dump(mode="json") for entry in body.get("entries", [])]
            except Exception:
                return await reject()
            operation = next((kind for kind, value in entries.items() if normalized == value), None)
            if operation is None or set(body) - {"dry_run", "entries", "preserve_source_details"}:
                return await reject()
            if not body["dry_run"] and (operation == "protected" or body.get("preserve_source_details") is not True
                                         or (actor == "admin" and saves[operation] >= EXPECTED_SAVES[operation])):
                return await reject()
            if body["dry_run"]:
                operation = None
            await app(scope, replay_body(raw, receive), capture)
            if operation == "csv" and actor == "admin" and saves["csv"] == 1:
                first_csv["rows"] = csv_snapshot()
            return
        return await reject()

    return handle


def verify_database(before, entries, saves, first_csv, denials, refused, attempts):
    from database import SessionLocal
    from models.lead import Lead
    from models.lead_delivery import LeadDeliveryJob, LeadDeliveryWorkerState
    from models.professional_profile import ProfessionalProfile
    from models.property_network import NetworkProject
    from models.trade_contact import TradeContact, TradeContactSource
    from models.user import User
    from sqlalchemy import text

    base.require(snapshot(protected_only=True) == before and saves == EXPECTED_SAVES
                 and len(first_csv.get("rows", [])) == 1 and csv_snapshot() == first_csv["rows"]
                 and denials == {"owner", "realtor", "buyer"} and not refused and not attempts,
                 "Original private rows exact UI saves and actual role denials required")
    with SessionLocal() as db:
        base.require(db.query(TradeContact).count() == db.query(TradeContactSource).count() == 84
                     and db.query(ProfessionalProfile).count() == 46
                     and db.query(User).count() == db.query(User).filter_by(status="active", email_verified=True).count() == 4
                     and all(db.query(model).count() == 0 for model in (Lead, LeadDeliveryJob, LeadDeliveryWorkerState, NetworkProject)),
                     "Exactly two new contacts and no incidental application data required")
        base.require(db.execute(text("SELECT count(*) FROM professional_profiles WHERE user_id IS NULL "
                                     "AND published_data IS NULL AND review_status='draft' AND listing_status='hidden' "
                                     "AND version=1")).scalar_one() == 46,
                     "Managed profiles remain strictly private")
        for kind in ("csv", "xlsx"):
            contact = db.query(TradeContact).filter_by(name=entries[kind][0]["name"]).one()
            source = db.query(TradeContactSource).filter_by(contact_id=contact.id).one()
            base.require(contact.details == source.evidence == entries[kind][0], "Exact reviewed file fields retained")
    return {"status": "PASS", "protected_contacts_unchanged": 82, "protected_sources_unchanged": 82,
            "private_profiles_unchanged": 46, "contacts": 84, "sources": 84, "new_contacts": 2, "new_sources": 2,
            "successful_final_imports": 3, "csv_replay_unchanged": True, "actual_nonadmin_denial_roles": 3,
            "users": 4, "leads": 0, "jobs": 0, "worker_rows": 0, "projects": 0,
            "unexpected_mutations": 0, "external_provider_attempts": 0}


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
    saves, first_csv, denials = Counter(), {}, set()

    def refuse_transport(*_args, **_kwargs):
        attempts.append(True)
        raise RuntimeError("External transport unavailable")

    try:
        with (patch.object(httpx.HTTPTransport, "handle_request", refuse_transport),
              patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse_transport),
              patch.object(requests.Session, "send", refuse_transport),
              patch.object(urllib_request, "urlopen", refuse_transport),
              patch.object(resend.Emails, "send", refuse_transport), patch.object(smtplib.SMTP, "connect", refuse_transport)):
            from main import app
            accounts, identities, protected = seed_database()
            before = snapshot()
            base.advance("create_private_input_files")
            files, input_data, entries = create_input_files(protected, owned)
            fixtures = {"scope": "contact-import-filter-only", "accounts": accounts, "files": files,
                        "entries": entries, "sourceDate": FACTS["sourceDate"], "facts": FACTS}
            write_owned(base.FIXTURE_FILE, (canonical(fixtures) + "\n").encode(), owned)
            server = uvicorn.Server(uvicorn.Config(confined(app, accounts, identities, input_data, entries, saves, first_csv,
                                                          denials, refused), host=base.HOST, port=base.API_PORT,
                                                   lifespan="off", access_log=False, log_level="warning"))
            handlers = {}

            def shutdown(_number, _frame):
                server.should_exit = True

            try:
                for number in (signal.SIGINT, signal.SIGTERM):
                    handlers[number] = signal.signal(number, shutdown)
                base.advance("contact_import_browser")
                server.run()
                base.require(server.started, "Actual synthetic API server required")
                base.advance("verify_contact_import")
                report = verify_database(before, entries, saves, first_csv, denials, refused, attempts)
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
        base.require(all(not path.exists() and not path.is_symlink() for path, _, _ in owned),
                     "Private input and login file cleanup required")
    print("CONTACT_IMPORT_FIXTURE_RESULT " + canonical({**report, "private_files_removed": 4}), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        error_type = type(error).__name__
        print("CONTACT_IMPORT_FIXTURE_FAILURE " + canonical({"stage": base.stage,
              "error_type": error_type if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", error_type) else "UnhandledError"}), flush=True)
        raise SystemExit(1) from None
