"""Owned catalog browser fixture; only exact synthetic logins may mutate."""
from collections import Counter
from contextlib import closing
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import smtplib
import stat
from urllib import request as urllib_request
from unittest.mock import patch
from uuid import UUID

COMMIT = "8957b18520d15dac503e0e4a5ce4b2455135d601"
TREE = "11ab69db36eb30881e4f3086578201f584c4092d"
ACCEPTED = Path(__file__).resolve().parent.parent / "social-groups-browser/serve_social_groups_fixture.py"
ACCEPTED_SHA = "e0c5f0971c7c13ed47063907f01fa6ef01590b00fa2855eb1ecb16a38b99463e"
DIRECTORY = Path("/browser-fixtures")
FACTS_FILE = DIRECTORY / "fixtures.json"
ARLYN = UUID("30e0a4b8-aab7-4af8-9b0b-ccbde1dd4e64")
STAGE = "source"


class Failure(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Failure(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def accepted_helpers():
    require(ACCEPTED.is_file() and not ACCEPTED.is_symlink()
            and sha256(ACCEPTED.read_bytes()).hexdigest() == ACCEPTED_SHA, "accepted_fixture_bytes_required")
    spec = spec_from_file_location("accepted_catalog_browser_helpers", ACCEPTED)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.APP_COMMIT, module.APP_TREE = COMMIT, TREE
    return module


def claim_database():
    import psycopg2
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    require(re.fullmatch(r"[a-f0-9]{32}", nonce), "owned_nonce_required")
    name = "ildr_browser_ci_" + nonce
    with closing(psycopg2.connect(host="127.0.0.1", port=55449, dbname=name,
                                 user="postgres", password="", connect_timeout=4)) as db, db.cursor() as cursor:
        cursor.execute("""SELECT current_database(),host(inet_server_addr()),inet_server_port(),
            current_setting('server_version_num')::int,shobj_description(oid,'pg_database'),
            NOT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public')
            FROM pg_database WHERE datname=current_database()""")
        require(cursor.fetchone() == (name, "127.0.0.1", 55449, 170006,
                f"ildr-realtor-catalog-ci:{nonce}", True), "fresh_marked_postgres_required")
    return name, f"postgresql+psycopg2://postgres@127.0.0.1:55449/{name}"


def seed(base, retained):
    from datetime import timedelta
    from database import SessionLocal
    from models.listing import Listing
    from models.property_network import SocialNetworkProfile
    from models.user import User
    from utils.security import hash_password
    accounts, identities, codes, previous = base.seed_database(retained)
    accounts["arlyn"] = {"email": "catalog-arlyn@example.com", "password": secrets.token_urlsafe(24)}
    identities["arlyn"] = ARLYN
    facts = {"roles": {**base.ROLES, "arlyn": "realtor"}, "arlynId": str(ARLYN),
             "profileNames": {"outsider": "Synthetic catalog member", "arlyn": "Synthetic catalog Arlyn"},
             "bio": "Synthetic catalog profile biography", "draftBio": "Unsaved synthetic catalog draft",
             "privateMarker": "NEVER_RENDER_SYNTHETIC_PRIVATE_LISTING_DATA", "catalog": [],
             "retainedListingId": str(retained.LISTING), "excluded": {}}
    with SessionLocal.begin() as db:
        require(db.get(User, ARLYN) is None, "fresh_synthetic_arlyn_identity_required")
        db.add(User(id=ARLYN, role="realtor", status="active", email_verified=True,
                    display_name="Synthetic catalog Arlyn", email=accounts["arlyn"]["email"],
                    password_hash=hash_password(accounts["arlyn"]["password"])))
        db.flush()
        codes["arlyn"] = db.get(User, ARLYN).user_code
        for actor in ("outsider", "arlyn"):
            db.add(SocialNetworkProfile(user_id=identities[actor], display_name=facts["profileNames"][actor],
                    bio=facts["bio"], status_text="Synthetic browser status", visibility="members",
                    wall_policy="own_only", version=1))
        for index in range(30):
            identity = UUID(int=60000 + index)
            actor = "outsider" if index % 2 else "arlyn"
            active = index < 27
            status = "active" if index < 28 else "draft" if index == 28 else "archived"
            transaction = "both" if index == 1 else "rent" if index == 27 else "sale"
            currency = "DOP" if index == 2 else "USD"
            price = 9000000 if index == 2 else 100000 if index == 0 else 120000 if index == 1 else 200000 + index * 10000
            location = "Cabarete" if index < 3 else "Sosúa" if index % 2 else "Punta Cana"
            kind = "condo" if index % 2 else "villa"
            title = f"Synthetic catalog property {index + 1:02d}"
            bedrooms = 3 + index if index < 3 else 1 + index % 5
            db.add(Listing(id=identity, title=title, description="Synthetic public property description",
                    type=kind, transaction=transaction, price=price, currency=currency, location=location,
                    bedrooms=bedrooms, bathrooms=2, area_sqft=1500, images=[], status=status,
                    submitted_by=identities[actor], assigned_realtor_id=identities[actor],
                    owner_id=identities["owner" if index % 2 else "arlyn"], owner_paypal_email="never-render@example.invalid",
                    co_listing_notes=facts["privateMarker"], created_at=retained.HISTORY + timedelta(days=index + 1)))
            if active:
                facts["catalog"].append({"id": str(identity), "title": title, "location": location,
                    "type": kind, "transaction": transaction, "price": price, "currency": currency,
                    "bedrooms": bedrooms, "actor": actor, "ownerActor": "owner" if index % 2 else "arlyn"})
            else:
                facts["excluded"]["rent" if index == 27 else status] = str(identity)
        db.flush()
        require(db.query(Listing).count() == 31 and db.query(User).count() == 6,
                "exact_seed_counts_required")
        require(db.query(Listing.owner_id).filter(Listing.id.in_([UUID(item["id"]) for item in facts["catalog"]]))
                .distinct().count() == 2, "two_actual_catalog_owners_required")
    facts["codes"] = {actor: codes[actor] for actor in ("outsider", "arlyn")}
    facts["ids"] = {actor: str(value) for actor, value in identities.items()}
    facts["mine"] = {actor: [str(UUID(int=60000 + i)) for i in range(30)
        if ("outsider" if i % 2 else "arlyn") == actor] for actor in ("outsider", "arlyn")}
    facts["mine"]["outsider"].append(str(retained.LISTING))
    return accounts, identities, facts


def confined(app, base, accounts, identities, facts, observed):
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from utils.auth import _decode_token
    logins = {canonical(value) for value in accounts.values()}
    actors = {str(identity): actor for actor, identity in identities.items()}
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
        label = None
        if method == "GET":
            query = request.query_params
            if path == "/listings" and query.get("purpose") == "sale" and query.get("page_size") == "24":
                if not any(query.get(key) for key in ("region", "type", "beds", "min_price", "max_price")):
                    label = "catalog_page_2" if query.get("page") == "2" else "catalog_page_1"
                if (query.get("region") == "Cabarete" and query.get("type") == "villa"
                        and query.get("beds") == "4" and query.get("min_price") == "140000"
                        and query.get("max_price") == "160000" and query.get("usd_to_dop") == "60"):
                    label = "comparable_price"
            elif path == "/listings/mine" and actor in ("outsider", "arlyn", "member"):
                label = "mine_" + actor
            elif path == "/listings/" + facts["excluded"]["draft"]:
                label = "draft_hidden"
            elif path == "/listings/" + facts["excluded"]["archived"]:
                label = "archived_hidden"
        async def capture(message):
            if label and message["type"] == "http.response.start":
                observed["reads"][(label, message["status"])] += 1
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
    database, url = claim_database()
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
            payload = (canonical({"scope": "realtor-catalog-read-only", "accounts": accounts, "facts": facts}) + "\n").encode()
            require(len(payload) < 32768, "bounded_private_facts_required")
            base.write_owned(FACTS_FILE, payload, owned)
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
                required = [("catalog_page_1", 200), ("catalog_page_2", 200), ("comparable_price", 200),
                    ("mine_outsider", 200), ("mine_arlyn", 200), ("mine_member", 403),
                    ("draft_hidden", 404), ("archived_hidden", 404)]
                require(all(observed["reads"][key] > 0 for key in required), "eight_actual_read_boundaries_required")
                require(observed["logins"] == 10, "ten_real_logins_required")
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
    print("CATALOG_FIXTURE_RESULT " + canonical({"status": "PASS", "schema_tables": 99,
        "all_rows_sequences_unchanged": True, "protected_owner_contacts_unchanged": 82,
        "private_profiles_unchanged": 46, "contacts_unchanged": 85, "sources_unchanged": 86,
        "users_unchanged": 6, "listings_unchanged": 31, "catalog_inventory": 27,
        "accepted_jobs_unchanged": 9, "cms_version_unchanged": 7, "floorplans_unchanged": 2,
        "actual_access_boundaries": 8, "actual_logins": 10, "unexpected_mutations": 0,
        "external_provider_attempts": 0, "private_files_removed": 1}), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        name = type(error).__name__
        print("CATALOG_FIXTURE_FAILURE " + canonical({"stage": STAGE,
            "error_type": name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name) else "UnhandledError",
            "reason": error.args[0] if isinstance(error, Failure) else "external_failure"}), flush=True)
        raise SystemExit(1) from None
