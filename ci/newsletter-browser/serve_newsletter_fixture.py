"""Real newsletter application, disposable PostgreSQL, and two synthetic mail sinks."""

from contextlib import closing
from html.parser import HTMLParser
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from uuid import UUID


APP_COMMIT = "4c4d2a71f406a7d552cc2e00a38f346b180fcf45"
APP_TREE = "c986f764dd3496a6da91175bc3d7f8231d1fe9b3"
MIGRATION = "f92c8a74d31e"
ORIGIN = "http://127.0.0.1:8917"
API_PORT = 8918
DATABASE_PORT = 55449
FIXTURES = Path("/browser-fixtures/mail-links.json")
EMAILS = {lang: f"newsletter-browser-{lang}@example.com" for lang in ("en", "es")}
TOKEN = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.[0-9a-f]{64}")
deliveries = {}
stage = "checkout"


def require(condition):
    if not condition:
        raise RuntimeError("Synthetic newsletter fixture invariant failed")


def advance(value):
    global stage
    stage = value


def claim():
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_REPOSITORY") == "ilovedrrealty/realty-api"
            and {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"})
    app = Path("/application")
    require(app.resolve(strict=True) == app and os.statvfs(app).f_flag & os.ST_RDONLY
            and not (app / ".env").exists() and not (app / ".env").is_symlink())
    git_env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"}

    def git(*args):
        return subprocess.check_output(
            ["git", "--no-optional-locks", "-c", f"safe.directory={app}", "-C", str(app), *args],
            env=git_env, stderr=subprocess.DEVNULL, timeout=10,
        ).decode("ascii").strip()

    require(git("rev-parse", "HEAD", "HEAD^{tree}").splitlines() == [APP_COMMIT, APP_TREE]
            and not git("status", "--porcelain=v1", "--untracked-files=all"))
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    require(re.fullmatch(r"[a-f0-9]{32}", nonce))
    name = f"ildr_browser_ci_{nonce}"
    import psycopg2

    with closing(psycopg2.connect(host="127.0.0.1", port=DATABASE_PORT, dbname=name,
                                 user="postgres", password="", connect_timeout=4)) as db:
        with db.cursor() as cursor:
            cursor.execute("""
                SELECT current_database(), host(inet_server_addr()), inet_server_port(),
                       current_setting('server_version_num')::integer,
                       shobj_description(oid, 'pg_database'),
                       NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                                   WHERE n.nspname = 'public')
                FROM pg_database WHERE datname = current_database()
            """)
            require(cursor.fetchone() == (name, "127.0.0.1", DATABASE_PORT, 170006,
                                           f"ildr-newsletter-browser-ci:{nonce}", True))
    require(FIXTURES.parent.resolve(strict=True) == FIXTURES.parent
            and not FIXTURES.exists() and not FIXTURES.is_symlink())
    os.environ.update({
        "DATABASE_URL": f"postgresql+psycopg2://postgres@127.0.0.1:{DATABASE_PORT}/{name}",
        "JWT_SECRET": secrets.token_urlsafe(48), "ENVIRONMENT": "development", "COOKIE_DOMAIN": "",
        "ALLOWED_ORIGINS": ORIGIN, "LANDING_URL": ORIGIN,
        "RESEND_API_KEY": "synthetic-newsletter-browser-only", "EMAIL_FROM": "newsletter@example.com",
        "SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": "", "NOTIFY_EMAIL": "", "GHL_API_KEY": "",
        "GHL_LOCATION_ID": "", "GHL_ENABLED": "false", "LEAD_DELIVERY_ENABLED": "false",
        "CUSTOMER_MESSAGES_ENABLED": "false", "PROPERTY_INQUIRY_REQUIRE_CONSENT": "false",
        "PAYPAL_CLIENT_ID": "", "PAYPAL_CLIENT_SECRET": "", "PAYPAL_WEBHOOK_ID": "",
    })
    os.chdir(app)
    sys.path.insert(0, str(app))


def save_links():
    # Only the links from the two synthetic mail payloads are retained. Nothing
    # is printed or uploaded, and the container removes this owned file on exit.
    fd, name = tempfile.mkstemp(prefix="mail-", suffix=".tmp", dir=FIXTURES.parent)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            json.dump({"deliveries": deliveries}, stream, ensure_ascii=True)
            stream.write("\n")
        os.replace(name, FIXTURES)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs).get("href"))


def fake_mail(url, **kwargs):
    import httpx

    require(url == "https://api.resend.com/emails" and set(kwargs) == {"headers", "json", "timeout"})
    headers, payload = kwargs["headers"], kwargs["json"]
    require(set(headers) == {"Authorization", "Idempotency-Key"}
            and headers["Authorization"] == "Bearer synthetic-newsletter-browser-only")
    key = headers["Idempotency-Key"]
    require(key.startswith("ildr-newsletter-confirm-") and UUID(key[24:]))
    require(isinstance(payload, dict) and set(payload) == {"from", "to", "subject", "html"}
            and payload["from"] == "newsletter@example.com")
    lang = next((locale for locale, email in EMAILS.items() if payload["to"] == [email]), None)
    require(lang in EMAILS and lang not in deliveries and len(deliveries) < 2)
    expected = {
        "en": ("Confirm your iLoveDR Realty newsletter signup", "Confirm subscription", "Cancel or unsubscribe"),
        "es": ("Confirma tu suscripción al boletín de iLoveDR Realty", "Confirmar suscripción", "Cancelar o darme de baja"),
    }[lang]
    require(payload["subject"] == expected[0] and all(label in payload["html"] for label in expected[1:]))
    parser = Links()
    parser.feed(payload["html"])
    require(len(parser.links) == 2)
    found = {}
    for link in parser.links:
        target = urlsplit(link)
        action, separator, token = target.fragment.partition("=")
        require(target.scheme == "http" and target.netloc == "127.0.0.1:8917"
                and target.path == "/newsletter/" and not target.query
                and separator == "=" and action in {"confirm", "unsubscribe"}
                and TOKEN.fullmatch(token) and action not in found)
        found[action] = link
    require(set(found) == {"confirm", "unsubscribe"})
    deliveries[lang] = found
    save_links()
    return httpx.Response(201, json={"id": f"synthetic-newsletter-{lang}"})


def migrate():
    import bootstrap_db
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from database import SessionLocal
    from models.newsletter import NewsletterSubscription
    from models.user import User
    from models.lead import Lead
    from models.inquiry import Inquiry

    advance("bootstrap_database")
    bootstrap_db.main()
    advance("apply_alembic")
    command.upgrade(Config("alembic.ini"), "head")
    with SessionLocal() as db:
        require(db.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [MIGRATION]
                and db.query(NewsletterSubscription).count() == db.query(User).count()
                == db.query(Lead).count() == db.query(Inquiry).count() == 0)


def state():
    from database import SessionLocal
    from models.newsletter import NewsletterSubscription
    from models.user import User
    from models.lead import Lead
    from models.inquiry import Inquiry

    with SessionLocal() as db:
        rows = db.query(NewsletterSubscription).all()
        require(len(rows) <= 2 and all(row.email in EMAILS.values() for row in rows)
                and db.query(User).count() == db.query(Lead).count() == db.query(Inquiry).count() == 0)
        return {"mail_count": len(deliveries), "rows": {
            row.language: {"status": row.status, "consent": row.consent_at is not None,
                           "confirmed": row.subscribed_at is not None, "left": row.unsubscribed_at is not None,
                           "provider_accepted": row.provider_accepted_at is not None}
            for row in rows}, "customer_accounts": 0, "leads": 0, "inquiries": 0}


def confined(api):
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    async def serve(scope, receive, send):
        if scope["type"] != "http":
            return await api(scope, receive, send)
        method, path = scope["method"], scope["path"]
        if method == "GET" and path == "/__newsletter_fixture__/state":
            return await JSONResponse(state(), headers={"Cache-Control": "no-store"})(scope, receive, send)
        if method in {"GET", "HEAD", "OPTIONS"}:
            return await api(scope, receive, send)
        request = Request(scope, receive)
        raw = await request.body()
        allowed = False
        if method == "POST" and path in {"/newsletter/subscribe", "/newsletter/confirm", "/newsletter/unsubscribe"} and len(raw) <= 1024:
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                body = None
            if isinstance(body, dict) and path.endswith("/subscribe"):
                lang = body.get("language")
                allowed = (lang in EMAILS and body.get("email") == EMAILS[lang]
                           and set(body) <= {"email", "language", "consent"})
            elif isinstance(body, dict) and set(body) == {"token"}:
                allowed = body["token"] in {urlsplit(url).fragment.partition("=")[2]
                                           for links in deliveries.values() for url in links.values()}
        if not allowed:
            return await JSONResponse({"detail": "Synthetic mutation refused"}, 405,
                                      headers={"Cache-Control": "no-store"})(scope, receive, send)
        consumed = False

        async def replay():
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": raw, "more_body": False}

        return await api(scope, replay, send)

    return serve


def run():
    logging.disable(logging.CRITICAL)
    claim()
    advance("load_application")
    import uvicorn
    from main import app
    from services import newsletter

    migrate()
    newsletter.httpx.post = fake_mail
    advance("write_fixture")
    save_links()
    previous = {}
    try:
        server = uvicorn.Server(uvicorn.Config(confined(app), host="127.0.0.1", port=API_PORT,
                                              lifespan="off", access_log=False, log_level="warning"))

        def shutdown(_number, _frame):
            server.should_exit = True

        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, shutdown)
        advance("serve_application")
        server.run()
        require(server.started)
    finally:
        if FIXTURES.exists():
            info = FIXTURES.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 4096)
            FIXTURES.unlink()
        for number, handler in previous.items():
            signal.signal(number, handler)
    require(not FIXTURES.exists() and not FIXTURES.is_symlink())


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        kind = type(error).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", kind):
            kind = "UnhandledError"
        print("NEWSLETTER_BROWSER_API_FAILURE " + json.dumps({"stage": stage, "error_type": kind}), flush=True)
        raise SystemExit(1) from None
