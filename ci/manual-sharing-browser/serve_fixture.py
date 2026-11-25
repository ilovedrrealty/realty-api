"""Actual public sharing reads and exact, restored owned view/freshness ledger."""
from collections import Counter
from contextlib import closing, redirect_stderr, redirect_stdout
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
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

COMMIT = "17943ba1714673c51c43cdbb800106f91612f527"
TREE = "ff940a7b6e983d4c669202746d9676b4b82e9a06"
NATIVE = Path(__file__).resolve().parent.parent / "native-inbox/verify.py"
NATIVE_SHA = "388fcae33bf63234df9739ac7405c1a2d49b4914af2a32d56e9023fdb7ed144e"
API, DASH, LANDING = "http://127.0.0.1:8918", "http://127.0.0.1:4175", "http://127.0.0.1:8917"
FACTS_FILE = Path("/browser-fixtures/fixtures.json")
ROLES = {"admin": "admin", "owner": "owner", "buyer": "buyer", "agent": "realtor"}
LISTINGS = {"both": UUID(int=90001), "rent": UUID(int=90002), "archived": UUID(int=90003)}
CONTROL_ORDER = ["price_raise", "price_restore", "withdraw", "restore"] * 2
STAGE = "source"


class Failure(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Failure(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def native_helpers():
    require(NATIVE.is_file() and not NATIVE.is_symlink() and sha256(NATIVE.read_bytes()).hexdigest() == NATIVE_SHA,
        "exact_native_helper_required")
    spec = spec_from_file_location("accepted_manual_sharing_native_helpers", NATIVE)
    native = module_from_spec(spec)
    spec.loader.exec_module(native)
    native.COMMIT, native.TREE = COMMIT, TREE
    return native, native.helpers()


def claim_database():
    import psycopg2
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    require(re.fullmatch(r"[a-f0-9]{32}", nonce), "owned_nonce_required")
    name = "ildr_browser_ci_" + nonce
    with closing(psycopg2.connect(host="127.0.0.1", port=55449, dbname=name, user="postgres", password="", connect_timeout=4)) as db, db.cursor() as cursor:
        cursor.execute("""SELECT current_database(),host(inet_server_addr()),inet_server_port(),
            current_setting('server_version_num')::int,shobj_description(oid,'pg_database'),
            NOT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public')
            FROM pg_database WHERE datname=current_database()""")
        require(cursor.fetchone() == (name, "127.0.0.1", 55449, 170006,
            "ildr-manual-sharing-browser-ci:" + nonce, True), "fresh_owned_database_required")
    return name, f"postgresql+psycopg2://postgres@127.0.0.1:55449/{name}"


def seed(native, base, catalog, retained, database, url):
    global STAGE
    STAGE = "previous99"
    native.previous_start(base, url)
    legacy = tuple(sorted(catalog.OLD_TABLES + native.GROUPS))
    require(catalog.table_names(database) == legacy and catalog.query(database,
        "SELECT version_num FROM alembic_version") == [(native.OLD_HEAD,)], "actual_previous99_required")
    require(retained.seed_old() == native.EXPECTED_SEED, "accepted_retained_seed_required")
    old_rows, old_schema = catalog.snapshot(database, legacy), catalog.catalog(database, legacy)
    STAGE = "upgrade106"
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config("alembic.ini"), "head")
    native.old_preserved(catalog, database, legacy, old_rows, old_schema)
    require(catalog.table_names(database) == tuple(sorted(legacy + native.INBOX)) and catalog.query(database,
        "SELECT version_num FROM alembic_version") == [(native.HEAD,)], "actual106_upgrade_required")
    native.privacy(catalog, database)
    from database import SessionLocal
    from models.user import User
    from models.listing import Listing
    from models.listing_location_review import ListingLocationReview
    from models.website_settings import WebsiteSettings
    from schemas.website_settings import ResourceItem, RealtorTool, WebsiteContentSettings
    from utils.security import hash_password
    identities = {actor: retained.IDS["realtor" if actor == "agent" else actor] for actor in ROLES}
    accounts = {actor: {"email": "", "password": secrets.token_urlsafe(24)} for actor in ROLES}
    facts = {"roles": ROLES, "ids": {actor: str(value) for actor, value in identities.items()},
        "listings": {name: str(value) for name, value in LISTINGS.items()},
        "photoUrl": "https://images.example.com/manual-sharing-synthetic.png",
        "privateMarker": "NEVER_SHARE_PRIVATE_SOURCE_CONTACT_OR_RESEARCH",
        "resource": {"id": "synthetic-guide", "title": {"en": "Synthetic property guide", "es": "Guía inmobiliaria sintética"}},
        "basePrice": 375000, "changedPrice": 375001, "dailyUSD": 160, "monthlyUSD": 2400,
        "dailyDOP": 9000, "monthlyDOP": 120000}
    STAGE = "seed_public_facts"
    with SessionLocal.begin() as db:
        for actor, identity in identities.items():
            user = db.get(User, identity)
            require(user is not None and user.role == ROLES[actor] and user.email_verified and user.status == "active",
                "existing_actual_roles_required")
            accounts[actor]["email"] = user.email
            user.password_hash = hash_password(accounts[actor]["password"])
        for name, identifier in LISTINGS.items():
            visible = name == "both"
            db.add(Listing(id=identifier, title="Synthetic ocean villa" if visible else facts["privateMarker"],
                type="villa", transaction="both" if visible else "rent", status="archived" if name == "archived" else "active",
                price=375000 if visible else 120000, price_per_day=160 if visible else 9000,
                price_per_month=2400 if visible else 120000, currency="USD" if visible else "DOP",
                location="Cabarete" if visible else facts["privateMarker"], bedrooms=3, bathrooms=3,
                latitude="19.7500000" if visible else None, longitude="-70.4000000" if visible else None,
                owner_id=identities["owner"], submitted_by=identities["owner"], assigned_realtor_id=identities["agent"],
                description=facts["privateMarker"], owner_paypal_email="private-owner@example.com",
                co_listing_notes=facts["privateMarker"], source_ref="synthetic-manual-share-" + name,
                images=[facts["photoUrl"]] if visible else []))
        db.flush()
        for name, identifier in LISTINGS.items():
            visible = name == "both"
            db.add(ListingLocationReview(listing_id=identifier, review_status="verified" if visible else "unreviewed",
                public_precision="exact" if visible else "hidden", latitude="19.7500000" if visible else None,
                longitude="-70.4000000" if visible else None, review_note=facts["privateMarker"],
                source_reference=facts["privateMarker"], reviewed_by=identities["admin"] if visible else None))
        cms = db.get(WebsiteSettings, 1)
        data = dict(cms.data)
        data["resources"] = [ResourceItem(id=facts["resource"]["id"], title=facts["resource"]["title"],
            description={"en": "Synthetic public guide", "es": "Guía pública sintética"},
            category={"en": "Synthetic resources", "es": "Recursos sintéticos"},
            url="https://example.com/public-guide?campaign=synthetic").model_dump(mode="json")]
        data["realtorTools"] = [RealtorTool(id="synthetic-private-tool",
            title={"en": facts["privateMarker"], "es": facts["privateMarker"]},
            description={"en": "Private configuration", "es": "Configuración privada"},
            category={"en": "Synthetic research", "es": "Investigación sintética"},
            url="https://example.com/private-research?owned_context=synthetic", access="realtor").model_dump(mode="json")]
        cms.data = WebsiteContentSettings.model_validate(data).model_dump(mode="json")
    all_tables = tuple(sorted(legacy + native.INBOX))
    require(catalog.query(database, "SELECT count(*) FROM listings") == [(4,)]
        and catalog.query(database, "SELECT count(*) FROM listing_daily_views") == [(0,)]
        and catalog.query(database, "SELECT count(*) FROM listing_view_coverage") == [(0,)], "finite_seed_and_empty_view_baseline_required")
    inbox = catalog.snapshot(database, native.INBOX)
    require(all(json.loads(inbox[name]) == [] for name in native.INBOX), "empty_new7_baseline_required")
    return all_tables, accounts, identities, facts


def confined(app, base, accounts, identities, facts, observed, catalog, database):
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from utils.auth import _decode_token
    from database import SessionLocal
    from models.listing import Listing
    actors = {str(value): actor for actor, value in identities.items()}
    logins = {canonical(value) for value in accounts.values()}
    boundaries = {"public_both": (None, "/listings/" + facts["listings"]["both"], 200),
        "public_rent": (None, "/listings/" + facts["listings"]["rent"], 200),
        "archived": (None, "/listings/" + facts["listings"]["archived"], 404),
        **{"research_" + actor: (None if actor == "anonymous" else actor, "/site/realtor-tools", status)
            for actor, status in (("anonymous", 401), ("buyer", 403), ("owner", 403), ("agent", 200), ("admin", 200))}}

    async def handle(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        request = Request(scope, receive)
        path, method = scope["path"], scope["method"]
        cookie = request.cookies.get("ildr_token")
        actor = actors.get(_decode_token(cookie)) if cookie else None
        if path == "/__ci/control" and method == "POST":
            raw = await request.body()
            require(actor == "admin" and len(raw) <= 128 and request.headers.get("x-ci-control") == facts["controlSecret"],
                "owned_admin_control_required")
            body = json.loads(raw)
            index = len(observed["controls"])
            require(set(body) == {"action"} and index < 8 and body["action"] == CONTROL_ORDER[index], "ordered_finite_control_required")
            action = body["action"]
            with SessionLocal.begin() as db:
                listing = db.get(Listing, LISTINGS["both"])
                before = (listing.status, int(listing.price))
                required = {"price_raise": (("active", 375000), ("active", 375001)),
                    "price_restore": (("active", 375001), ("active", 375000)),
                    "withdraw": (("active", 375000), ("archived", 375000)),
                    "restore": (("archived", 375000), ("active", 375000))}
                require(before == required[action][0], "exact_before_control_state_required")
                listing.status, listing.price = required[action][1]
            observed["controls"].append(action)
            return await JSONResponse({"status": "PASS"}, headers={"Cache-Control": "no-store"})(scope, base.replay_body(raw, receive), send)
        if method in {"GET", "HEAD", "OPTIONS"}:
            label = request.headers.get("x-ci-boundary")
            if label is not None:
                require(method == "GET" and label in boundaries and (actor, path) == boundaries[label][:2], "real_role_boundary_request_required")
            fresh = method == "GET" and path in {"/listings/" + facts["listings"][kind] for kind in ("both", "rent")} and request.headers.get("accept") == "application/json"
            if fresh:
                require(not cookie and not request.headers.get("authorization") and request.headers.get("origin") == LANDING,
                    "fresh_public_read_omits_existing_auth_cookie_required")
            async def capture(message):
                if message["type"] == "http.response.start":
                    if label:
                        observed["boundaries"][(label, message["status"])] += 1
                    if fresh:
                        require(dict(message["headers"]).get(b"cache-control") == b"no-store", "actual_fresh_no_store_required")
                        observed["fresh"][("both" if path.endswith(facts["listings"]["both"]) else "rent", message["status"])] += 1
                await send(message)
            return await app(scope, receive, capture)
        raw = await request.body()
        if method == "POST" and path == "/auth/login" and len(raw) <= 8192:
            require(canonical(json.loads(raw)) in logins, "only_exact_owned_login_required")
            observed["logins"] += 1
            return await app(scope, base.replay_body(raw, receive), send)
        match = re.fullmatch(r"/listings/([0-9a-f-]{36})/view", path)
        if method == "POST" and match and match[1] in {facts["listings"][kind] for kind in ("both", "rent")}:
            kind = "both" if match[1] == facts["listings"]["both"] else "rent"
            require(len(observed["views"]) < 4 and not raw and actor == ("admin" if kind == "both" else None), "finite_actual_page_view_required")
            before = catalog.query(database, "SELECT listing_id::text,view_date::text,view_count FROM listing_daily_views ORDER BY listing_id,view_date")
            previous = catalog.query(database, "SELECT view_count FROM listings WHERE id=%s", (match[1],))[0][0]
            status = []
            async def capture(message):
                if message["type"] == "http.response.start": status.append(message["status"])
                await send(message)
            await app(scope, base.replay_body(raw, receive), capture)
            require(status == [204], "real_view_committed_without_rate_bypass_required")
            after = catalog.query(database, "SELECT listing_id::text,view_date::text,view_count FROM listing_daily_views ORDER BY listing_id,view_date")
            old = {(row[0], row[1]): row[2] for row in before}
            new = {(row[0], row[1]): row[2] for row in after}
            changed = {key for key in set(old) | set(new) if old.get(key, 0) != new.get(key, 0)}
            require(len(changed) == 1 and all(key[0] == match[1] and new[key] == old.get(key, 0) + 1 for key in changed)
                and catalog.query(database, "SELECT view_count FROM listings WHERE id=%s", (match[1],)) == [(previous + 1,)],
                "native_view_counter_and_single_day_delta_required")
            coverage = catalog.query(database, "SELECT id,started_at::text FROM listing_view_coverage ORDER BY id")
            require(len(coverage) == 1 and coverage[0][0] == 1 and (not observed["coverage"] or coverage == observed["coverage"]),
                "single_earliest_view_coverage_required")
            observed["coverage"] = coverage
            observed["views"].append((kind, next(iter(changed))))
            return
        observed["refused"] += 1
        return await JSONResponse({"detail": "owned_manual_sharing_scope_refused"}, status_code=405)(scope, base.replay_body(raw, receive), send)
    return handle


def verify(native, catalog, database, all_tables, baseline, schema, facts, observed, attempts):
    require(not attempts and observed["refused"] == 0 and observed["logins"] == 8 and observed["controls"] == CONTROL_ORDER,
        "exact_transport_login_and_restored_control_ledger_required")
    expected = {"public_both": 200, "public_rent": 200, "archived": 404, "research_anonymous": 401,
        "research_buyer": 403, "research_owner": 403, "research_agent": 200, "research_admin": 200}
    require(observed["boundaries"] == Counter({(name, status): 2 for name, status in expected.items()}), "eight_exact_real_role_boundaries_required")
    require(observed["fresh"] == Counter({("both", 200): 18, ("both", 404): 2, ("rent", 200): 2}), "twenty_two_actual_anonymous_fresh_reads_required")
    require(Counter(kind for kind, _ in observed["views"]) == Counter(both=2, rent=2) and len(observed["views"]) == 4,
        "exact_four_real_page_views_required")
    # Validate the complete native delta before restoring only its known effects.
    current = catalog.snapshot(database, all_tables)
    rows_before, rows_after = json.loads(baseline["listings"]), json.loads(current["listings"])
    for row in rows_after:
        expected_row = next(item for item in rows_before if item["id"] == row["id"])
        increment = 2 if row["id"] in {facts["listings"]["both"], facts["listings"]["rent"]} else 0
        require(row == {**expected_row, "view_count": expected_row["view_count"] + increment}, "only_exact_listing_view_field_changes_required")
    require(all(current[name] == baseline[name] for name in baseline if name not in {"listings", "listing_daily_views", "listing_view_coverage"}),
        "all_other106_rows_sequences_unchanged_before_restore_required")
    from sqlalchemy import text
    from database import engine
    with engine.begin() as connection:
        for kind in ("both", "rent"):
            result = connection.execute(text("UPDATE listings SET view_count=view_count-2 WHERE id=:id AND view_count=2"), {"id": LISTINGS[kind]})
            require(result.rowcount == 1, "restore_only_exact_owned_listing_delta_required")
        result = connection.execute(text("DELETE FROM listing_daily_views WHERE listing_id IN (:both,:rent)"), {"both": LISTINGS["both"], "rent": LISTINGS["rent"]})
        require(result.rowcount == len({key for _, key in observed["views"]}), "restore_only_exact_observed_view_days_required")
        result = connection.execute(text("DELETE FROM listing_view_coverage WHERE id=1 AND started_at::text=:stamp"), {"stamp": observed["coverage"][0][1]})
        require(result.rowcount == 1, "restore_only_exact_observed_coverage_required")
    require(catalog.snapshot(database, all_tables) == baseline and catalog.catalog(database, all_tables) == schema
        and catalog.query(database, "SELECT version_num FROM alembic_version") == [(native.HEAD,)], "all106_full_rows_sequences_catalog_equal_after_restore_required")
    native.privacy(catalog, database)
    return {"status": "PASS", "schema_tables": 106, "legacy_tables": 99, "all106_rows_sequences_catalog_unchanged": True,
        "inbox_writes": 0, "private_rls_acl": True, "protected_owner_contacts_unchanged": 82, "private_profiles_unchanged": 46,
        "contacts_unchanged": 85, "sources_unchanged": 86, "users_unchanged": 4, "listings_unchanged": 4,
        "accepted_jobs_unchanged": 9, "cms_version_unchanged": 7, "floorplans_unchanged": 2,
        "actual_access_boundaries": 8, "actual_logins": 8, "anonymous_fresh_reads": 22, "fresh_read_not_found": 2,
        "actual_view_posts": 4, "view_effects_verified_and_restored": True, "control_transitions": 8,
        "unexpected_mutations": 0, "external_provider_attempts": 0}


def run():
    global STAGE
    logging.disable(logging.CRITICAL)
    import importlib.metadata as metadata
    installed = {re.sub(r"[-_.]+", "-", item.metadata["Name"].lower()): item.version for item in metadata.distributions()}
    accepted = json.loads(Path("/opt/contact-import/baseline-inventory.json").read_text())
    require(installed == {**accepted, "openpyxl": "3.1.5", "defusedxml": "0.7.1", "et-xmlfile": "2.0.0"}, "exact_dependency_inventory_required")
    native, base = native_helpers()
    application = base.verified_checkout()
    database, url = claim_database()
    base.configure(application, url)
    catalog, retained = base.frozen_helpers()
    import httpx, requests, resend, uvicorn
    attempts, owned = [], []
    observed = {"logins": 0, "refused": 0, "controls": [], "views": [], "coverage": [], "fresh": Counter(), "boundaries": Counter()}
    def refuse(*_args, **_kwargs):
        attempts.append(1)
        raise RuntimeError("external_transport_refused")
    try:
        with (patch.object(httpx.HTTPTransport, "handle_request", refuse), patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse),
            patch.object(requests.Session, "send", refuse), patch.object(urllib_request, "urlopen", refuse),
            patch.object(resend.Emails, "send", refuse), patch.object(smtplib.SMTP, "connect", refuse), patch.object(smtplib.SMTP_SSL, "connect", refuse)):
            captured = StringIO()
            with redirect_stdout(captured), redirect_stderr(captured):
                tables, accounts, identities, facts = seed(native, base, catalog, retained, database, url)
            require(len(captured.getvalue().encode()) <= 2 * 1024 * 1024, "bounded_seed_output_required")
            baseline, schema = catalog.snapshot(database, tables), catalog.catalog(database, tables)
            facts["controlSecret"] = secrets.token_urlsafe(32)
            base.write_owned(FACTS_FILE, (canonical({"scope": "manual-sharing-only", "accounts": accounts, "facts": facts}) + "\n").encode(), owned)
            from main import app
            server = uvicorn.Server(uvicorn.Config(confined(app, base, accounts, identities, facts, observed, catalog, database),
                host="127.0.0.1", port=8918, lifespan="off", access_log=False, log_level="warning"))
            handlers = {}
            def shutdown(_number, _frame): server.should_exit = True
            try:
                for number in (signal.SIGTERM, signal.SIGINT): handlers[number] = signal.signal(number, shutdown)
                STAGE = "browser"
                server.run()
                require(server.started, "actual_server_required")
                STAGE = "native_ledger"
                report = verify(native, catalog, database, tables, baseline, schema, facts, observed, attempts)
            finally:
                for number, handler in handlers.items(): signal.signal(number, handler)
    finally:
        for path, device, inode in reversed(owned):
            if path.exists() or path.is_symlink():
                info = path.lstat()
                require(stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == (device, inode), "owned_fixture_inode_required")
                path.unlink()
        require(all(not path.exists() and not path.is_symlink() for path, _, _ in owned), "private_fixture_cleanup_required")
    print("MANUAL_SHARE_FIXTURE_RESULT " + canonical({**report, "private_files_removed": 1}), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        name = type(error).__name__
        print("MANUAL_SHARE_FIXTURE_FAILURE " + canonical({"stage": STAGE,
            "error_type": name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name) else "UnhandledError",
            "reason": str(error) if isinstance(error, Failure) and re.fullmatch(r"[a-z_0-9]{1,90}", str(error)) else "unexpected_failure"}), flush=True)
        raise SystemExit(1)
