"""Real owner-created property/project workflow with an in-memory private photo sink."""

from collections import Counter
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import sys
from urllib import request as urllib_request
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent / "accepted"))
import serve_service_visit_fixture as base
from private_storage_fixture import install_private_storage_fixture

base.APP_COMMIT = "796f25ff52724db56313916e129df4b252a682fd"
base.APP_TREE = "10d76a3de555b567fde93a74b5f90b8d9ba76978"
base.MIGRATION_HEAD = "b19d8e4c720a"
NETWORK = "/property-network"
ROLES = {"owner": "owner", "professional": "buyer", "outsider": "buyer"}
FACTS = {
    "property": "Synthetic private pool house",
    "location": "Synthetic Sosua",
    "address": "PRIVATE SYNTHETIC ADDRESS",
    "notes": "PRIVATE SYNTHETIC DOSSIER",
    "project": "Synthetic pool maintenance project",
    "summary": "A deliberately shared synthetic work brief.",
    "milestone": "Synthetic pool inspection",
    "update": "Synthetic assigned professional progress update.",
    "comment": "Synthetic owner comment about this exact progress photo.",
    "category": "pool_service",
}
EXPECTED = Counter({name: 1 for name in (
    "registry", "project", "assignment", "milestone", "milestone_complete",
    "photo", "progress", "comment", "revocation",
)})
UUID_TEXT = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def seed_accounts():
    import bootstrap_db
    from alembic import command
    from alembic.config import Config
    from database import SessionLocal
    from models.user import User
    from sqlalchemy import text
    from utils.security import hash_password

    base.advance("bootstrap_database")
    bootstrap_db.main()
    command.upgrade(Config("alembic.ini"), "head")
    accounts, identities, codes = {}, {}, {}
    with SessionLocal() as db:
        base.require(db.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
                     == [base.MIGRATION_HEAD] and db.query(User).count() == 0,
                     "Exact new schema and empty users required")
        for actor, role in ROLES.items():
            account = {"email": f"browser-owner-project-{actor}@example.com",
                       "password": secrets.token_urlsafe(24)}
            user = User(id=uuid4(), email=account["email"], password_hash=hash_password(account["password"]),
                        role=role, status="active", email_verified=True,
                        display_name=f"Synthetic Project {actor.title()}")
            db.add(user)
            db.flush()
            accounts[actor], identities[actor], codes[actor] = account, user.id, user.user_code
        db.commit()
    base.require(len(set(codes.values())) == 3 and all(type(code) is int and code > 0 for code in codes.values()),
                 "Distinct routable synthetic users required")
    return accounts, identities, codes


def confined(app, accounts, identities, codes, observed, refused):
    from database import SessionLocal
    from models.property_network import NetworkMedia, NetworkMilestone, NetworkProject, NetworkProjectUpdate
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from utils.auth import _decode_token

    actors = {str(identity): actor for actor, identity in identities.items()}
    login_bodies = {canonical(account) for account in accounts.values()}
    active = False

    async def handle(scope, receive, send):
        nonlocal active
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            return await app(scope, receive, send)
        request = Request(scope, receive)
        raw = await request.body()

        async def reject():
            refused.append(True)
            return await JSONResponse({"detail": "Synthetic owner workflow mutation refused"}, 405,
                                      headers={"Cache-Control": "no-store"})(scope, receive, send)

        if len(raw) > 5 * 1024 * 1024 + 65536:
            return await reject()
        try:
            body = json.loads(raw) if request.headers.get("content-type", "").split(";")[0] == "application/json" else None
        except (ValueError, UnicodeError):
            body = None
        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": raw, "more_body": False}

        path, method = scope["path"], scope["method"]
        if path == "/auth/login":
            if method != "POST" or canonical(body) not in login_bodies:
                return await reject()
            return await app(scope, replay, send)
        if active:
            return await reject()
        cookie = request.cookies.get("ildr_token")
        actor = actors.get(_decode_token(cookie)) if cookie else None
        submission = body.get("submission_id") if isinstance(body, dict) else None
        unique = isinstance(submission, str) and UUID_TEXT.fullmatch(submission)
        with SessionLocal() as db:
            projects = db.query(NetworkProject).all()
            milestones = db.query(NetworkMilestone).all()
            updates = db.query(NetworkProjectUpdate).filter_by(kind="progress").all()
            media = db.query(NetworkMedia).all()
            base.require(len(projects) <= 1 and len(milestones) <= 1 and len(updates) <= 1 and len(media) <= 1,
                         "Bounded synthetic workspace required")
            project = projects[0] if projects else None
            project_id = str(project.id) if project else None
            milestone_id = str(milestones[0].id) if milestones else None
            update_id = str(updates[0].id) if updates else None
            photo_id = str(media[0].id) if media else None
        action = None
        if actor == "owner" and method == "POST" and path == NETWORK + "/registry" and isinstance(body, dict):
            if (set(body) == {"title", "location", "address", "property_class", "relationship", "notes",
                              "authority_acknowledged", "submission_id"} and unique
                    and all(body.get(key) == FACTS[key] for key in ("location", "address", "notes"))
                    and body["title"] == FACTS["property"] and body["property_class"] == "house_villa"
                    and body["relationship"] == "owner" and body["authority_acknowledged"] is True):
                action = "registry"
        elif actor == "owner" and method == "POST" and path == NETWORK + "/projects" and isinstance(body, dict):
            if (set(body) == {"property_id", "submission_id", "title", "summary", "property_class", "service_category_ids"}
                    and unique and UUID_TEXT.fullmatch(str(body.get("property_id", "")))
                    and body["title"] == FACTS["project"] and body["summary"] == FACTS["summary"]
                    and body["property_class"] == "house_villa" and body["service_category_ids"] == [FACTS["category"]]):
                action = "project"
        elif actor == "owner" and project_id and method == "POST" and path == f"{NETWORK}/projects/{project_id}/members":
            if (isinstance(body, dict) and set(body) == {"expected_version", "user_code", "role"}
                    and type(body["expected_version"]) is int and body["expected_version"] >= 1
                    and body["user_code"] == codes["professional"] and body["role"] == "professional"):
                action = "assignment"
        elif actor == "owner" and project_id and method == "POST" and path == f"{NETWORK}/projects/{project_id}/milestones":
            if isinstance(body, dict) and set(body) == {"title", "due_date"} and body["title"] == FACTS["milestone"]:
                action = "milestone"
        elif actor == "owner" and milestone_id and method == "PUT" and path == f"{NETWORK}/milestones/{milestone_id}":
            if (isinstance(body, dict) and set(body) == {"expected_version", "title", "due_date", "status"}
                    and body["title"] == FACTS["milestone"] and body["status"] == "completed"
                    and type(body["expected_version"]) is int and body["expected_version"] >= 1):
                action = "milestone_complete"
        elif actor == "professional" and project_id and method == "POST" and path == NETWORK + "/media":
            if body is None and request.headers.get("content-type", "").startswith("multipart/form-data;"):
                action = "photo"
        elif actor == "professional" and project_id and method == "POST" and path == f"{NETWORK}/projects/{project_id}/updates":
            if (isinstance(body, dict) and set(body) <= {"submission_id", "body", "occurred_on", "media_ids", "correction_of"}
                    and unique and body.get("body") == FACTS["update"] and body.get("media_ids") == [photo_id]
                    and body.get("correction_of") is None and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(body.get("occurred_on", "")))):
                action = "progress"
        elif actor == "owner" and update_id and method == "POST" and path == f"{NETWORK}/updates/{update_id}/comments":
            if (isinstance(body, dict) and set(body) == {"submission_id", "body", "media_id"} and unique
                    and body["body"] == FACTS["comment"] and body["media_id"] == photo_id):
                action = "comment"
        elif actor == "owner" and project_id and method == "POST" and path == f"{NETWORK}/projects/{project_id}/members/{codes['professional']}/revoke":
            if (isinstance(body, dict) and set(body) == {"expected_version"}
                    and type(body["expected_version"]) is int and body["expected_version"] >= 1):
                action = "revocation"
        if action is None or observed[action] >= EXPECTED[action]:
            return await reject()
        active = True
        status = None

        async def capture(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await app(scope, replay, capture)
            if status is not None and 200 <= status < 300:
                observed[action] += 1
        finally:
            active = False

    return handle


def verify_database(identities, observed, refused, attempts, storage):
    from database import SessionLocal
    from models.lead import Lead
    from models.lead_delivery import LeadDeliveryJob, LeadDeliveryWorkerState
    from models.listing import Listing
    from models.property_network import (
        NetworkProperty, NetworkProject, NetworkProjectMember, NetworkMilestone,
        NetworkProjectUpdate, NetworkProjectComment, NetworkMedia, SocialNetworkProfile,
    )
    from models.user import User

    with SessionLocal() as db:
        properties, projects = db.query(NetworkProperty).all(), db.query(NetworkProject).all()
        members, milestones = db.query(NetworkProjectMember).all(), db.query(NetworkMilestone).all()
        updates = db.query(NetworkProjectUpdate).filter_by(kind="progress").all()
        comments, media = db.query(NetworkProjectComment).all(), db.query(NetworkMedia).all()
        base.require(len(properties) == len(projects) == len(members) == len(milestones)
                     == len(updates) == len(comments) == len(media) == 1, "Exact single-workspace records required")
        prop, project, member, milestone, update, comment, photo = (
            properties[0], projects[0], members[0], milestones[0], updates[0], comments[0], media[0])
        base.require(prop.current_custodian_id == prop.custodian_id == project.owner_id == identities["owner"]
                     and prop.authority_acknowledged and prop.title == FACTS["property"]
                     and prop.address == FACTS["address"] and prop.notes == FACTS["notes"]
                     and project.property_id == prop.id and project.listing_id is None
                     and project.title == FACTS["project"] and project.service_category_ids == [FACTS["category"]],
                     "Actual property/project linkage required")
        base.require(member.project_id == project.id and member.user_id == identities["professional"]
                     and member.role == "professional" and not member.active
                     and milestone.project_id == project.id and milestone.status == "completed"
                     and update.project_id == project.id and update.author_id == identities["professional"]
                     and update.body == FACTS["update"] and update.media_ids == [str(photo.id)]
                     and photo.project_id == project.id and photo.update_id == update.id
                     and photo.uploader_id == identities["professional"]
                     and comment.update_id == update.id and comment.media_id == photo.id
                     and comment.author_id == identities["owner"] and comment.body == FACTS["comment"],
                     "Assigned work, private photo, comment and revocation linkage required")
        base.require(db.query(User).count() == 3 and db.query(User).filter_by(status="active").count() == 3
                     and db.query(Lead).count() == db.query(Listing).count()
                     == db.query(LeadDeliveryJob).count() == db.query(LeadDeliveryWorkerState).count()
                     == db.query(SocialNetworkProfile).count() == 0, "No incidental accounts, publication or communications")
    state = storage.snapshot()
    base.require(observed == EXPECTED and not refused and not attempts
                 and state["uploads"] == state["object_count"] == 1 and state["downloads"] >= 1,
                 "Exact isolated synthetic actions and storage required")
    return {"status": "PASS", "properties": 1, "projects": 1, "professional_assignments": 1,
            "private_progress_photos": 1, "photo_comments": 1, "completed_milestones": 1,
            "revocations": 1, "successful_ui_mutations": 9, "live_provider_requests": 0,
            "incidental_leads_or_jobs": 0, "all_accounts_active": True}


def run():
    application = base.verified_checkout()
    url = base.claim_disposable_database()
    base.configure_application(application, url)
    import httpx
    import requests
    import uvicorn

    observed, refused, attempts = Counter(), [], []

    def refuse_transport(*_args, **_kwargs):
        attempts.append(True)
        raise RuntimeError("External transport unavailable")

    with (patch.object(httpx.HTTPTransport, "handle_request", refuse_transport),
          patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse_transport),
          patch.object(requests.Session, "send", refuse_transport),
          patch.object(urllib_request, "urlopen", refuse_transport),
          install_private_storage_fixture() as storage):
        from main import app
        accounts, identities, codes = seed_accounts()
        path = base.FIXTURE_FILE
        base.require(not path.exists() and not path.is_symlink(), "Fresh private fixture file required")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        owned = os.fstat(fd)
        handlers = {}
        try:
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                stream.write(canonical({"accounts": accounts, "codes": codes, "facts": FACTS}) + "\n")
            server = uvicorn.Server(uvicorn.Config(confined(app, accounts, identities, codes, observed, refused),
                host=base.HOST, port=base.API_PORT, lifespan="off", access_log=False, log_level="warning"))

            def shutdown(_number, _frame):
                server.should_exit = True

            for number in (signal.SIGINT, signal.SIGTERM):
                handlers[number] = signal.signal(number, shutdown)
            base.advance("owner_project_browser")
            server.run()
            base.require(server.started, "Actual synthetic server required")
            base.advance("verify_owner_project")
            report = verify_database(identities, observed, refused, attempts, storage)
        finally:
            if path.exists() or path.is_symlink():
                current = path.lstat()
                if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                    path.unlink()
            for number, handler in handlers.items():
                signal.signal(number, handler)
    base.require(not path.exists() and not path.is_symlink(), "Private fixture file cleanup required")
    print("OWNER_PROJECT_FIXTURE_RESULT " + canonical({**report, "login_file_absent": True}), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        print("OWNER_PROJECT_FIXTURE_FAILURE " + canonical({
            "stage": base.stage, "error_type": type(error).__name__,
        }), flush=True)
        raise SystemExit(1) from None
