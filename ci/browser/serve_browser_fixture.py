"""Start the reviewed application on a disposable CI-only database for browser checks."""

import io
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from uuid import UUID, uuid4


APP_COMMIT = "3da654cefb806ba90a8e66078721611f2d2d57d7"
APP_TREE = "ec5f60b27a4aabfb8b1dd6de5b55da40879f0c8c"
MIGRATION_HEAD = "a4b6c0d2e834"
HOST = "127.0.0.1"
DATABASE_PORT = 55449
API_PORT = 8000
DASHBOARD_ORIGIN = "http://127.0.0.1:4175"
LANDING_ORIGIN = "http://127.0.0.1:8917"
ROLES = {
    "admin": "admin",
    "realtor": "realtor",
    "owner": "owner",
    "member": "buyer",
    "outsider": "buyer",
}
PROJECT_TITLE = "Browser rehearsal project"
UPDATE_TEXT = "Browser rehearsal progress photo"
FIXTURE_FILE = Path("/browser-fixtures/fixtures.json")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verified_checkout():
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_REPOSITORY") == "ilovedrrealty/realty-api", "CI runner required")
    application = Path(os.environ.get("ILDR_BROWSER_APPLICATION_ROOT", "/application"))
    require(application.is_absolute() and application.resolve(strict=True) == application,
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
            and not (application / ".env").exists(), "Exact clean browser application checkout required")
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
    require(identity == (database_name, HOST, DATABASE_PORT, 170011, f"ildr-browser-ci:{nonce}", True),
            "Fresh, marked loopback database required")
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
        "PAYPAL_CLIENT_ID": "",
        "PAYPAL_CLIENT_SECRET": "",
        "PAYPAL_WEBHOOK_ID": "",
    })
    os.chdir(application)
    sys.path[:0] = [str(application), str(Path(__file__).resolve().parent)]


def migrate_and_seed():
    from alembic import command
    from alembic.config import Config
    from PIL import Image
    from sqlalchemy import text

    from database import SessionLocal
    from models.listing import Listing
    from models.user import User
    from schemas.property_network import MemberWrite, ProgressWrite, ProjectCreate
    from services import property_network
    from services.property_management import today_dr
    from utils.security import hash_password

    # Apply the same Alembic entry point as production, within the checked disposable DB only.
    command.upgrade(Config("alembic.ini"), "head")

    accounts = {name: {"email": f"browser-{name}@example.com", "password": secrets.token_urlsafe(24)}
                for name in ROLES}
    with SessionLocal() as db:
        require(db.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [MIGRATION_HEAD],
                "Reviewed Alembic head required")
        require(db.query(User).count() == 0, "Unexpected fixture database users")
        users = {}
        for name, role in ROLES.items():
            account = accounts[name]
            user = User(id=uuid4(), email=account["email"], password_hash=hash_password(account["password"]),
                        role=role, status="active", display_name=f"Browser {name.title()}", email_verified=True)
            users[name] = user
            db.add(user)
        db.flush()
        owner, member = users["owner"], users["member"]
        listing = Listing(id=uuid4(), title="Browser rehearsal villa", type="villa", transaction="rent",
                          price=1000, currency="USD", location="Cabarete", status="active", images=[],
                          submitted_by=owner.id, owner_id=owner.id, assigned_realtor_id=users["realtor"].id)
        db.add(listing)
        db.commit()

        project = property_network.create_project(db, owner, ProjectCreate(
            listing_id=listing.id, submission_id=uuid4(), title=PROJECT_TITLE, property_class="house_villa"))
        project_id = UUID(project["id"])
        member_code = member.user_code
        require(isinstance(member_code, int) and member_code > 0, "Synthetic member code missing")
        property_network.assign(db, owner, project_id, MemberWrite(
            expected_version=project["version"], user_code=member_code, role="viewer"))

        png = io.BytesIO()
        Image.new("RGB", (36, 24), "blue").save(png, format="PNG")
        attached = property_network.upload_media(db, owner, project_id, png.getvalue())
        unattached = property_network.upload_media(db, owner, project_id, png.getvalue())
        property_network.create_update(db, owner, project_id, ProgressWrite(
            submission_id=uuid4(), body=UPDATE_TEXT, occurred_on=today_dr(), media_ids=[UUID(attached["id"])]))
        return {
            "accounts": accounts,
            "project": {"id": project["id"], "title": PROJECT_TITLE, "mediaId": attached["id"],
                        "mediaSha256": attached["sha256"], "unattachedMediaId": unattached["id"],
                        "memberUserCode": member_code, "updateText": UPDATE_TEXT},
        }


def confined_api(api, project):
    from starlette.responses import JSONResponse

    mutations = {
        ("POST", "/auth/login"),
        ("POST", "/auth/logout"),
        ("POST", "/property-network/media"),
        ("POST", f"/property-network/projects/{project['id']}/members/{project['memberUserCode']}/revoke"),
    }

    async def serve(scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            if (scope["method"], scope["path"]) not in mutations:
                response = JSONResponse({"detail": "Mutation unavailable in browser rehearsal"}, status_code=405,
                                        headers={"Cache-Control": "no-store"})
                return await response(scope, receive, send)
        return await api(scope, receive, send)

    return serve


def run():
    application = verified_checkout()
    database_url = claim_disposable_database()
    require(FIXTURE_FILE.parent.resolve(strict=True) == FIXTURE_FILE.parent and not FIXTURE_FILE.exists(),
            "Empty private fixture directory required")
    configure_application(application, database_url)

    import uvicorn
    from main import app
    from private_storage_fixture import install_private_storage_fixture

    with install_private_storage_fixture() as storage:
        fixtures = migrate_and_seed()
        seed_storage = storage.snapshot()
        require(seed_storage["object_count"] == seed_storage["uploads"] == 2 and seed_storage["downloads"] == 0,
                "Expected two synthetic uploads and zero downloads before the browser starts")
        api = confined_api(app, fixtures["project"])
        fd = os.open(FIXTURE_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        owned = os.fstat(fd)
        try:
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                json.dump(fixtures, stream, ensure_ascii=True)
                stream.write("\n")
            # A test runner checks /health after the fixture file appears. Startup workers stay off.
            server = uvicorn.Server(uvicorn.Config(api, host=HOST, port=API_PORT, lifespan="off",
                                                  access_log=False, log_level="warning"))
            server.run()
            require(server.started, "The synthetic browser API failed to start")
        finally:
            if FIXTURE_FILE.exists():
                current = FIXTURE_FILE.lstat()
                if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                    FIXTURE_FILE.unlink()


if __name__ == "__main__":
    try:
        run()
    except Exception:
        # Application/library exceptions may include synthetic account or fixture identifiers.
        raise SystemExit("Browser rehearsal fixture failed") from None
