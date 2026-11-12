"""Serve the exact app with a synthetic awarded request on disposable CI Postgres."""
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import traceback
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from unittest.mock import patch
from urllib import request as urllib_request
from uuid import UUID, uuid4


APP_COMMIT = "330079d7dc32eeb918e7f45ff5b354cd8af76e95"
APP_TREE = "a3e1d2fe4e25d30657aed4cf353a17a596eafd12"
MIGRATION_HEAD = "b7e2c4d6f901"
HOST = "127.0.0.1"
DATABASE_PORT = 55449
API_PORT = 8000
DASHBOARD_ORIGIN = "http://127.0.0.1:4175"
LANDING_ORIGIN = "http://127.0.0.1:8917"
FIXTURE_FILE = Path("/browser-fixtures/fixtures.json")
CLOCK_PATH = "/__service-visit-rehearsal/advance-clock"
CLOCK_START = datetime(2026, 11, 12, 13, tzinfo=timezone.utc)
CLOCK_AFTER = datetime(2026, 11, 12, 18, tzinfo=timezone.utc)
CONFIRMED_START = datetime(2026, 11, 12, 16, tzinfo=timezone.utc)
COMPLETED_ON = date(2026, 11, 12)
ROLES = {"owner": "owner", "winner": "buyer", "loser": "buyer", "outsider": "buyer", "admin": "admin"}
REQUEST_TITLE = "Browser rehearsal awarded pool service"
PROJECT_TITLE = "Browser rehearsal villa service project"
COMPLETION = "Synthetic approved completion for property service history."
REVIEW = "Synthetic private review feedback for the awarded provider only."
ACTIONS = (
    ("owner", "propose_visit"), ("winner", "accept_visit"), ("winner", "propose_visit"),
    ("owner", "decline_visit"), ("winner", "propose_visit"), ("owner", "accept_visit"),
    ("winner", "cancel_visit"), ("owner", "propose_visit"), ("winner", "accept_visit"),
    ("winner", "request_completion"), ("owner", "request_changes"), ("winner", "request_completion"),
    ("owner", "approve_completion"),
)
stage = "checkout"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def advance(value):
    global stage
    stage = value


def verified_checkout():
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_REPOSITORY") == "ilovedrrealty/realty-api", "Hosted CI only")
    require({item.name for item in Path("/sys/class/net").iterdir()} == {"lo"}, "Loopback-only namespace required")
    application = Path("/application")
    require(application.resolve(strict=True) == application and application.is_dir()
            and os.statvfs(application).f_flag & os.ST_RDONLY, "Canonical read-only checkout required")

    def git(*args):
        return subprocess.check_output(
            ["git", "--no-optional-locks", "-c", f"safe.directory={application}", "-C", str(application), *args],
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"},
            stderr=subprocess.DEVNULL, timeout=10,
        ).decode("ascii").strip()

    dotenv = application / ".env"
    require(git("rev-parse", "HEAD", "HEAD^{tree}").splitlines() == [APP_COMMIT, APP_TREE]
            and not git("status", "--porcelain=v1", "--untracked-files=all")
            and not dotenv.exists() and not dotenv.is_symlink(), "Exact clean application required")
    return application


def claim_disposable_database():
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    require(re.fullmatch(r"[a-f0-9]{32}", nonce), "Owned browser database nonce required")
    database_name = f"ildr_browser_ci_{nonce}"

    import psycopg2

    with closing(psycopg2.connect(host=HOST, port=DATABASE_PORT, dbname=database_name, user="postgres",
                                 password="", connect_timeout=4, options="-cstatement_timeout=5000")) as connection:
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
            "Fresh marked loopback database required")
    return f"postgresql+psycopg2://postgres@{HOST}:{DATABASE_PORT}/{database_name}"


def configure_application(application, database_url):
    os.environ.update({
        "DATABASE_URL": database_url, "JWT_SECRET": secrets.token_urlsafe(48), "ENVIRONMENT": "development",
        "COOKIE_DOMAIN": "", "ALLOWED_ORIGINS": f"{DASHBOARD_ORIGIN},{LANDING_ORIGIN}",
        "LANDING_URL": LANDING_ORIGIN, "SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": "",
        "RESEND_API_KEY": "", "NOTIFY_EMAIL": "", "GHL_API_KEY": "", "GHL_LOCATION_ID": "",
        "GHL_WEBHOOK_SECRET": "", "GHL_ENABLED": "false", "LEAD_DELIVERY_ENABLED": "false",
        "CUSTOMER_MESSAGES_ENABLED": "false", "PAYPAL_CLIENT_ID": "", "PAYPAL_CLIENT_SECRET": "",
        "PAYPAL_WEBHOOK_ID": "",
    })
    os.chdir(application)
    sys.path.insert(0, str(application))


def migrate_and_seed():
    import bootstrap_db
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from database import SessionLocal
    from models.listing import Listing
    from models.property_network import NetworkProjectUpdate
    from models.service_visit import NetworkServiceWork, NetworkServiceWorkAction
    from models.user import User
    from schemas.service_workflow import AwardWrite, PreferenceWrite, ProposalTerms, QuoteWrite, RecipientAction, RequestCreate
    from services import service_workflow as service
    from utils.security import hash_password

    advance("bootstrap_database")
    bootstrap_db.main()
    advance("apply_alembic")
    command.upgrade(Config("alembic.ini"), "head")

    accounts = {name: {"email": f"browser-{name}@example.com", "password": secrets.token_urlsafe(24)} for name in ROLES}
    with SessionLocal() as db:
        require(db.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [MIGRATION_HEAD]
                and db.query(User).count() == 0, "Fresh reviewed application schema required")
        advance("seed_accounts")
        users = {}
        for name, role in ROLES.items():
            user = User(id=uuid4(), email=accounts[name]["email"], password_hash=hash_password(accounts[name]["password"]),
                        role=role, status="active", display_name=f"Browser {name.title()}", email_verified=True)
            users[name] = user
            db.add(user)
        db.flush()
        user_ids = {name: user.id for name, user in users.items()}
        codes = {name: user.user_code for name, user in users.items()}
        require(all(type(code) is int and code > 0 for code in codes.values()), "Synthetic member codes required")
        listing_id = uuid4()
        owner_id = user_ids["owner"]
        db.add(Listing(id=listing_id, title="Browser rehearsal villa", type="villa", transaction="rent", price=1000,
                       currency="USD", location="Synthetic service area", status="active", images=[],
                       submitted_by=owner_id, owner_id=owner_id))
        db.commit()

        advance("seed_service_award")
        providers = ("winner", "loser")
        for name in providers:
            service.save_preference(db, users[name], PreferenceWrite(submission_id=uuid4(), expected_version=0,
                service_opt_in=True, member_alias=f"Synthetic {name} service provider", category_ids=["pool_service"],
                property_class_ids=["house_villa"]))
        created = service.create_request(db, users["owner"], RequestCreate(submission_id=uuid4(), listing_id=listing_id,
            category_id="pool_service", property_class="house_villa", title=REQUEST_TITLE, brief="Synthetic pool service scope",
            area_label="Synthetic service area", requester_alias="Synthetic property owner",
            recipient_user_codes=sorted(codes[name] for name in providers), shared_brief_acknowledged=True))
        request_id = UUID(created["id"])

        for name, amount in (("winner", "175.00"), ("loser", "290.00")):
            provider = users[name]
            received = service.get_request(db, provider, request_id, view="received")
            recipient_id = UUID(received["recipient"]["id"])
            accepted = service.recipient_action(db, provider, request_id, recipient_id, RecipientAction(
                submission_id=uuid4(), expected_recipient_version=received["recipient"]["version"]), "recipient_accept")
            service.write_proposal(db, provider, request_id, recipient_id, QuoteWrite(submission_id=uuid4(),
                expected_recipient_version=accepted["recipient"]["version"], terms=ProposalTerms(amount=amount, currency="USD",
                pricing_basis="estimate", scope_text="Synthetic proposed pool service", exclusions="Synthetic extras excluded",
                estimated_days=1, valid_until=service.today_dr() + timedelta(days=10))))

        managed = service.get_request(db, users["owner"], request_id, view="managed")
        winner = next(item for item in managed["recipients"] if item["user_code"] == codes["winner"])
        proposal = winner["current_proposal"]
        awarded = service.close_request(db, users["owner"], request_id, AwardWrite(submission_id=uuid4(),
            expected_request_version=managed["version"], recipient_id=UUID(winner["id"]),
            expected_recipient_version=winner["version"], proposal_id=UUID(proposal["id"]),
            expected_proposal_version=proposal["version"], project_title=PROJECT_TITLE,
            project_summary="Synthetic awarded pool service scope", project_scope_acknowledged=True), award=True)
        project_id = UUID(awarded["project"]["id"])
        winner_view = service.get_request(db, users["winner"], request_id, view="received")
        loser_view = service.get_request(db, users["loser"], request_id, view="received")
        require(winner_view["recipient"]["status"] == "awarded" and winner_view["project_id"] == str(project_id)
                and loser_view["recipient"]["status"] == "closed" and loser_view["project_id"] is None,
                "Real awarded service ownership required")
        require(db.query(NetworkServiceWork).count() == db.query(NetworkServiceWorkAction).count()
                == db.query(NetworkProjectUpdate).count() == 0, "Synthetic award must begin without work or timeline rows")

    return {
        "accounts": accounts,
        "serviceVisit": {"requestId": str(request_id), "projectId": str(project_id), "listingId": str(listing_id),
                         "winnerUserCode": codes["winner"], "loserUserCode": codes["loser"], "title": REQUEST_TITLE,
                         "projectTitle": PROJECT_TITLE, "clockStart": CLOCK_START.isoformat(), "clockAfter": CLOCK_AFTER.isoformat()},
    }, user_ids


class VisitClock:
    def __init__(self):
        self._instant = CLOCK_START
        self._lock = Lock()

    def now(self):
        with self._lock:
            return self._instant

    def advance_once(self):
        with self._lock:
            if self._instant != CLOCK_START:
                return False
            self._instant = CLOCK_AFTER
            return True


def confined_api(app, request_id, owner_id, clock):
    from fastapi import Depends, HTTPException, Request
    from fastapi.responses import JSONResponse
    from sqlalchemy.orm import Session

    from database import get_db
    from models.service_visit import NetworkServiceWork
    from routes.property_network import NO_STORE
    from utils.auth import get_current_user
    from utils.request_security import require_json_mutation

    def advance_clock(request: Request, body: dict, user=Depends(get_current_user), db: Session = Depends(get_db)):
        require_json_mutation(request)
        if request.client is None or request.client.host != HOST or user.id != owner_id:
            raise HTTPException(404, "Synthetic clock unavailable", headers=NO_STORE)
        if body != {} or request.headers.get("origin") != DASHBOARD_ORIGIN:
            raise HTTPException(422, "Invalid synthetic clock request", headers=NO_STORE)
        work = db.get(NetworkServiceWork, request_id)
        if (work is None or work.version != 9 or work.status != "confirmed" or work.confirmed_starts_at != CONFIRMED_START
                or work.confirmed_duration_minutes != 60 or not clock.advance_once()):
            raise HTTPException(409, "Confirmed synthetic visit required before advancing", headers=NO_STORE)
        return JSONResponse({"advanced": True}, headers=NO_STORE)

    # Mounted in the disposable process after importing the exact app. No product route or DB time edit.
    app.add_api_route(CLOCK_PATH, advance_clock, methods=["POST"], include_in_schema=False)
    mutations = {("POST", "/auth/login"), ("POST", "/auth/logout"), ("POST", CLOCK_PATH),
                 ("POST", f"/property-network/service-requests/{request_id}/work")}

    async def serve(scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            if (scope["method"], scope["path"]) not in mutations:
                refused = JSONResponse({"detail": "Mutation unavailable in browser rehearsal"}, status_code=405, headers=NO_STORE)
                return await refused(scope, receive, send)
        return await app(scope, receive, send)

    return serve


def verify_service_history(fixture, user_ids, clock, transport_attempts):
    from database import SessionLocal
    from models.property_network import NetworkProjectUpdate
    from models.service_visit import NetworkServiceWork, NetworkServiceWorkAction

    visit = fixture["serviceVisit"]
    with SessionLocal() as db:
        work = db.query(NetworkServiceWork).one()
        require(work.request_id == UUID(visit["requestId"]) and work.status == "completed" and work.version == len(ACTIONS)
                and work.confirmed_starts_at == CONFIRMED_START and work.confirmed_duration_minutes == 60
                and work.proposed_starts_at is None and work.completion_note == COMPLETION
                and work.completion_review_note == REVIEW and work.completed_on == COMPLETED_ON,
                "Exact completed service work required")
        actions = db.query(NetworkServiceWorkAction).order_by(NetworkServiceWorkAction.created_at,
                                                             NetworkServiceWorkAction.submission_id).all()
        require(all(action.request_id == work.request_id for action in actions)
                and [(action.actor_id, action.operation) for action in actions] == [(user_ids[actor], op) for actor, op in ACTIONS],
                "Exact service work actor and action history required")
        entry = db.query(NetworkProjectUpdate).one()
        require(entry.id == work.completion_update_id and entry.project_id == UUID(visit["projectId"])
                and entry.author_id == user_ids["owner"] and entry.kind == "progress" and entry.body == COMPLETION
                and entry.occurred_on == COMPLETED_ON and entry.correction_of is None and entry.media_ids == []
                and entry.submission_id not in {action.submission_id for action in actions},
                "Exact single private history entry required")
    require(clock.now() == CLOCK_AFTER and not transport_attempts, "One synthetic clock advance and no provider transport required")
    return {"status": "PASS", "work_status": "completed", "version": len(ACTIONS), "actions": len(actions),
            "progress_entries": 1, "history_author_owner": True, "clock_advanced": True, "external_transport_attempts": 0}


def run():
    application = verified_checkout()
    advance("claim_database")
    database_url = claim_disposable_database()
    advance("configure_application")
    require(FIXTURE_FILE.parent.resolve(strict=True) == FIXTURE_FILE.parent
            and not FIXTURE_FILE.exists() and not FIXTURE_FILE.is_symlink(), "Empty private fixture directory required")
    configure_application(application, database_url)

    advance("load_application")
    import httpx
    import requests
    import uvicorn

    transport_attempts = []

    def refuse_transport(*_args, **_kwargs):
        transport_attempts.append(True)
        raise RuntimeError("Fixture provider transport is disabled")

    with (
        patch.object(httpx.HTTPTransport, "handle_request", refuse_transport),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse_transport),
        patch.object(requests.Session, "send", refuse_transport),
        patch.object(urllib_request, "urlopen", refuse_transport),
    ):
        from main import app
        from services import property_network

        fixture, user_ids = migrate_and_seed()
        clock = VisitClock()
        api = confined_api(app, UUID(fixture["serviceVisit"]["requestId"]), user_ids["owner"], clock)
        advance("write_fixture")
        fd = os.open(FIXTURE_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        owned = os.fstat(fd)
        previous_handlers = {}
        try:
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                json.dump(fixture, stream, ensure_ascii=True)
                stream.write("\n")
            advance("serve_application")
            server = uvicorn.Server(uvicorn.Config(api, host=HOST, port=API_PORT, lifespan="off", access_log=False, log_level="warning"))

            def finish_shutdown(_number, _frame):
                server.should_exit = True

            for number in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[number] = signal.signal(number, finish_shutdown)
            with patch.object(property_network, "now", clock.now):
                server.run()
                require(server.started, "Synthetic browser API failed to start")
                advance("verify_service_history")
                report = verify_service_history(fixture, user_ids, clock, transport_attempts)
        finally:
            try:
                if FIXTURE_FILE.exists() or FIXTURE_FILE.is_symlink():
                    current = FIXTURE_FILE.lstat()
                    if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                        FIXTURE_FILE.unlink()
            finally:
                for number, handler in previous_handlers.items():
                    signal.signal(number, handler)

    require(not FIXTURE_FILE.exists() and not FIXTURE_FILE.is_symlink(), "Synthetic login file cleanup failed")
    print("SERVICE_VISITS_BROWSER_FIXTURE_RESULT " + json.dumps(report, sort_keys=True, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        error_type = type(error).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", error_type):
            error_type = "UnhandledError"
        frames = [frame.lineno for frame in traceback.extract_tb(error.__traceback__) if frame.filename == __file__]
        print("BROWSER_API_FIXTURE_FAILURE " + json.dumps({"stage": stage, "error_type": error_type,
                                                           "fixture_line": frames[-1] if frames else None}), flush=True)
        raise SystemExit(1) from None
