"""Serve actual reviewed routes with four synthetic accounts in an owned hosted DB."""

import json
import os
from pathlib import Path
import re
import secrets
import sys
import traceback
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_admin_share_fixture as fixture


APP_COMMIT = "979b5faf37a819161a0501962b2a807836da5b30"
APP_TREE = "d286ca58414fa971f967c0657cba142ca41a5694"
MIGRATION = "c3a914f0b628"
ROLES = ("admin", "owner", "buyer", "realtor")
EMAILS = {f"browser-{role}@example.com" for role in ROLES}
REASONS = {f"Synthetic F09 {action} {language}" for action in ("review", "hold", "stale") for language in ("en", "es")}
UUID_PATTERN = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"


def migrate_and_seed():
    import bootstrap_db
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from database import SessionLocal
    from models.account_deletion import AccountDeletionEvent, AccountDeletionRequest
    from models.lead import Lead
    from models.listing import Listing
    from models.newsletter import NewsletterSubscription
    from models.user import User
    from utils.security import hash_password

    fixture.advance("bootstrap_database")
    bootstrap_db.main()
    fixture.advance("apply_alembic")
    command.upgrade(Config("alembic.ini"), "head")
    accounts = {}
    with SessionLocal.begin() as db:
        fixture.require(
            db.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [MIGRATION],
            "Exact reviewed migration head required",
        )
        for model in (User, Lead, Listing, NewsletterSubscription, AccountDeletionRequest, AccountDeletionEvent):
            fixture.require(db.query(model).count() == 0, "Owned synthetic database must start empty")
        fixture.advance("seed_accounts")
        for role in ROLES:
            account = {"email": f"browser-{role}@example.com", "password": secrets.token_urlsafe(24)}
            user = User(
                id=uuid4(), email=account["email"], password_hash=hash_password(account["password"]),
                role=role, status="active", display_name=f"Browser {role.title()}", email_verified=True,
            )
            db.add(user)
            accounts[role] = account
        db.flush()
    return {"accounts": accounts, "scope": "account-deletion-review-only"}


def confined_api(api):
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    async def serve(scope, receive, send):
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            return await api(scope, receive, send)
        method, path = scope["method"], scope["path"]
        allowed = method == "POST" and path in {"/auth/login", "/auth/logout"}
        needs_body = method == "POST" and (
            path in {"/auth/me/delete-request", "/auth/me/delete-request/cancel"} or
            re.fullmatch(rf"/admin/account-deletion-requests/{UUID_PATTERN}/review", path)
        )
        raw = b""
        if needs_body:
            request = Request(scope, receive)
            raw = await request.body()
            body = None
            if len(raw) <= 4096:
                try:
                    body = json.loads(raw)
                except (ValueError, UnicodeError):
                    pass
            if isinstance(body, dict):
                if path == "/auth/me/delete-request":
                    allowed = (
                        set(body) == {"confirm_email", "confirm_request"} and
                        isinstance(body["confirm_email"], str) and body["confirm_email"] in EMAILS and
                        body["confirm_request"] is True
                    )
                elif path == "/auth/me/delete-request/cancel":
                    allowed = (
                        set(body) == {"request_id", "expected_version"} and
                        isinstance(body["request_id"], str) and re.fullmatch(UUID_PATTERN, body["request_id"]) and
                        type(body["expected_version"]) is int and 1 <= body["expected_version"] <= 20
                    )
                else:
                    allowed = (
                        set(body) == {"expected_version", "status", "reason"} and
                        type(body["expected_version"]) is int and 1 <= body["expected_version"] <= 20 and
                        isinstance(body["status"], str) and body["status"] in {"reviewed", "retention_hold"} and
                        isinstance(body["reason"], str) and body["reason"] in REASONS
                    )
        if not allowed:
            return await JSONResponse(
                {"detail": "Synthetic review mutation refused"}, status_code=405,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
        if not needs_body:
            return await api(scope, receive, send)
        consumed = False

        async def replay():
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": raw, "more_body": False}

        return await api(scope, replay, send)

    return serve


if __name__ == "__main__":
    try:
        fixture.APP_COMMIT = APP_COMMIT
        fixture.APP_TREE = APP_TREE
        fixture.MIGRATION_HEAD = MIGRATION
        fixture.API_PORT = 8918
        fixture.migrate_and_seed = migrate_and_seed
        fixture.confined_api = confined_api
        fixture.run()
    except Exception as error:
        kind = type(error).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", kind):
            kind = "UnhandledError"
        frames = [frame.lineno for frame in traceback.extract_tb(error.__traceback__) if frame.filename == __file__]
        print("F09_BROWSER_API_FAILURE " + json.dumps({
            "stage": fixture.stage, "error_type": kind, "fixture_line": frames[-1] if frames else None,
        }), flush=True)
        raise SystemExit(1) from None
