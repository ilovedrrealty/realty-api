"""Start the reviewed application on a disposable CI-only database for browser checks."""

import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import logging
import traceback
from contextlib import closing
from pathlib import Path
from uuid import uuid4


APP_COMMIT = "08c78f58cf4052a267480a8c27a406c7f8afaf2c"
APP_TREE = "ad70125fd25bb57e8a830ad3ae3dd25e3f259ed4"
MIGRATION_HEAD = "f4b27d6e913c"
HOST = "127.0.0.1"
DATABASE_PORT = 55449
API_PORT = 8000
DASHBOARD_ORIGIN = "http://127.0.0.1:4175"
LANDING_ORIGIN = "http://127.0.0.1:8917"
ROLES = ("admin", "owner", "realtor", "buyer")
FIXTURE_FILE = Path("/browser-fixtures/fixtures.json")
stage = "checkout"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def advance(value):
    global stage
    stage = value


def verified_checkout():
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_REPOSITORY") == "ilovedrrealty/realty-api"
            and {interface.name for interface in Path("/sys/class/net").iterdir()} == {"lo"},
            "Hosted CI with loopback-only network required")
    application = Path(os.environ.get("ILDR_BROWSER_APPLICATION_ROOT", "/application"))
    require(application.is_absolute() and application.resolve(strict=True) == application
            and os.statvfs(application).f_flag & os.ST_RDONLY,
            "Absolute application checkout required")

    def git(*args):
        return subprocess.check_output(
            ["git", "--no-optional-locks", "-c", f"safe.directory={application}", "-C", str(application), *args],
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"},
            stderr=subprocess.DEVNULL, timeout=10,
        ).decode("ascii").strip()

    require(git("rev-parse", "HEAD", "HEAD^{tree}").splitlines() == [APP_COMMIT, APP_TREE]
            and not git("status", "--porcelain=v1", "--untracked-files=all")
            and not (application / ".env").exists() and not (application / ".env").is_symlink(), "Exact clean browser application checkout required")
    return application


def claim_disposable_database():
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    require(re.fullmatch(r"[a-f0-9]{32}", nonce), "Owned browser database nonce required")
    database_name = f"ildr_browser_ci_{nonce}"

    import psycopg2

    with closing(psycopg2.connect(host=HOST, port=DATABASE_PORT, dbname=database_name,
                                 user="postgres", password="", connect_timeout=4)) as connection:
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
    require(identity == (database_name, HOST, DATABASE_PORT, 170006, f"ildr-admin-share-ci:{nonce}", True),
            "Fresh, marked PostgreSQL 17.6 loopback database required")
    return f"postgresql+psycopg2://postgres@{HOST}:{DATABASE_PORT}/{database_name}"


def configure_application(application, database_url):
    os.environ.update({
        "DATABASE_URL": database_url,
        "JWT_SECRET": secrets.token_urlsafe(48),
        "ENVIRONMENT": "development",
        "COOKIE_DOMAIN": "",
        "ALLOWED_ORIGINS": f"{DASHBOARD_ORIGIN},{LANDING_ORIGIN}",
        "LANDING_URL": LANDING_ORIGIN,
        "SUPABASE_URL": "",
        "SUPABASE_SERVICE_KEY": "",
        "RESEND_API_KEY": "",
        "NOTIFY_EMAIL": "",
        "GHL_API_KEY": "",
        "GHL_LOCATION_ID": "",
        "GHL_ENABLED": "false",
        "LEAD_DELIVERY_ENABLED": "false",
        "CUSTOMER_MESSAGES_ENABLED": "false",
        "PROPERTY_INQUIRY_REQUIRE_CONSENT": "false",
        "PAYPAL_CLIENT_ID": "",
        "PAYPAL_CLIENT_SECRET": "",
        "PAYPAL_WEBHOOK_ID": "",
    })
    os.chdir(application)
    sys.path[:0] = [str(application), str(Path(__file__).resolve().parent)]


def migrate_and_seed():
    import bootstrap_db
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from database import SessionLocal
    from models.inquiry import Inquiry
    from models.lead import Lead
    from models.listing import Listing
    from models.user import User
    from utils.security import hash_password

    advance("bootstrap_database")
    bootstrap_db.main()
    advance("apply_alembic")
    command.upgrade(Config("alembic.ini"), "head")

    accounts = {role: {"email": f"browser-{role}@example.com", "password": secrets.token_urlsafe(24)}
                for role in ROLES}
    with SessionLocal.begin() as db:
        require(db.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [MIGRATION_HEAD],
                "Exact candidate Alembic head required")
        require(db.query(User).count() == db.query(Lead).count() == db.query(Inquiry).count() == 0,
                "Synthetic database must start empty")
        advance("seed_accounts")
        users = {}
        for role, account in accounts.items():
            user = User(id=uuid4(), email=account["email"], password_hash=hash_password(account["password"]),
                        role=role, status="active", display_name=f"Browser {role.title()}", email_verified=True)
            users[role] = user
            db.add(user)
        db.flush()
        advance("seed_lead")
        listing = Listing(id=uuid4(), title="Browser rehearsal villa", type="villa", transaction="rent",
                          price=1000, currency="USD", location="Cabarete", status="active", images=[],
                          submitted_by=users["owner"].id, owner_id=users["owner"].id,
                          assigned_realtor_id=users["realtor"].id)
        foreign = Listing(id=uuid4(), title="Synthetic other owner property", type="villa", transaction="sale",
                          price=1000, currency="USD", location="Cabarete", status="active", images=[],
                          submitted_by=users["admin"].id)
        db.add_all((listing, foreign))
        db.flush()
        lead = Lead(id=uuid4(), type="property_inquiry", name="Browser Share Contact",
                    email="browser-share+ci@example.com", phone="(809) 555-0119",
                    message="Synthetic private lead note sentinel", property_id=listing.id,
                    assigned_realtor_id=users["realtor"].id, from_user_id=users["buyer"].id, status="new")
        foreign_lead = Lead(id=uuid4(), type="property_inquiry", name="Synthetic other owner contact",
                            email="browser-foreign@example.com", phone="(809) 555-0142",
                            message="Synthetic other owner inquiry note", property_id=foreign.id, status="new")
        legacy = Inquiry(id=uuid4(), listing_id=listing.id, name="Browser Legacy Contact",
                         email="browser-legacy@example.com", phone="(809) 555-0191",
                         message="Synthetic legacy inquiry history note", from_user_id=users["buyer"].id)
        db.add_all((lead, foreign_lead, legacy))
        return {"accounts": accounts, "shareLeadId": str(lead.id), "foreignListingId": str(foreign.id),
                "foreignLeadId": str(foreign_lead.id), "legacyInquiryId": str(legacy.id)}


def confined_api(api):
    from starlette.responses import JSONResponse

    mutations = {("POST", "/auth/login"), ("POST", "/auth/logout")}

    async def serve(scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            if (scope["method"], scope["path"]) not in mutations:
                response = JSONResponse({"detail": "Mutation unavailable in browser rehearsal"}, status_code=405,
                                        headers={"Cache-Control": "no-store"})
                return await response(scope, receive, send)
        return await api(scope, receive, send)

    return serve


def run():
    logging.disable(logging.CRITICAL)
    application = verified_checkout()
    advance("claim_database")
    database_url = claim_disposable_database()
    advance("configure_application")
    require(FIXTURE_FILE.parent.resolve(strict=True) == FIXTURE_FILE.parent and not FIXTURE_FILE.exists(),
            "Empty private fixture directory required")
    configure_application(application, database_url)

    advance("load_application")
    import uvicorn
    from main import app

    fixtures = migrate_and_seed()
    advance("write_fixture")
    api = confined_api(app)
    fd = os.open(FIXTURE_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    owned = os.fstat(fd)
    previous_handlers = {}
    try:
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            json.dump(fixtures, stream, ensure_ascii=True)
            stream.write("\n")
        # A test runner checks /health after the fixture file appears. Startup workers stay off.
        advance("serve_application")
        server = uvicorn.Server(uvicorn.Config(api, host=HOST, port=API_PORT, lifespan="off",
                                              access_log=False, log_level="warning"))

        def finish_shutdown(_number, _frame):
            server.should_exit = True

        # Uvicorn restores and replays captured signals when serving ends. A
        # temporary handler lets the fixture's own finally block finish first.
        for number in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[number] = signal.signal(number, finish_shutdown)
        server.run()
        require(server.started, "The synthetic browser API failed to start")
    finally:
        try:
            if FIXTURE_FILE.exists():
                current = FIXTURE_FILE.lstat()
                if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                    FIXTURE_FILE.unlink()
        finally:
            for number, handler in previous_handlers.items():
                signal.signal(number, handler)
    require(not FIXTURE_FILE.exists() and not FIXTURE_FILE.is_symlink(), "Synthetic login file cleanup required")


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        # Emit only fixed stage and type information. Exceptions can contain account data.
        error_type = type(error).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", error_type):
            error_type = "UnhandledError"
        frames = [frame.lineno for frame in traceback.extract_tb(error.__traceback__) if frame.filename == __file__]
        print("BROWSER_API_FIXTURE_FAILURE " + json.dumps({"stage": stage, "error_type": error_type,
                                                          "fixture_line": frames[-1] if frames else None}), flush=True)
        raise SystemExit(1) from None
