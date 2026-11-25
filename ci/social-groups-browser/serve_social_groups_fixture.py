"""Real group API on one owned synthetic database; private bytes stay in memory."""
from collections import Counter
from contextlib import closing
from hashlib import sha256
from importlib.util import spec_from_file_location, module_from_spec
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
import subprocess
import sys
from urllib import request as urllib_request
from unittest.mock import patch
from uuid import UUID, uuid4

APP_COMMIT = "4e23cc8b643a4b07f4e14042e530e6271dad9c8f"
APP_TREE = "8093ea7aa9fd8f87a842e2527c715a8c482ac1fc"
HEAD = "c4e2a7319b60"
HOST, PG_PORT, API_PORT = "127.0.0.1", 55449, 8918
ORIGIN, LANDING = "http://127.0.0.1:4175", "http://127.0.0.1:8917"
DIRECTORY = Path("/browser-fixtures")
FACTS_FILE = DIRECTORY / "fixtures.json"
ROLES = {"admin": "admin", "owner": "owner", "member": "buyer", "professional": "buyer", "outsider": "realtor"}
NATIVE = Path(__file__).resolve().parent.parent / "social-groups-native"
FROZEN_HELPERS = {"catalog.py": "5869dcbd30dc659a649fc6c2bbbe2de1337904ef11e4b91d6ad1992ef7ea9c2f",
                  "records.py": "bffdf8900f286079807ba43e8db5c09160caaf139b1e934449b90e577c1cb5cf"}
GROUP_TABLES = tuple(sorted(("network_groups", "network_group_memberships", "network_group_invite_links",
    "network_group_posts", "network_group_comments", "network_group_media", "network_group_actions")))
FACTS = {"groupName": "Synthetic neighborhood group", "description": "Synthetic community browser check",
    "editedDescription": "Synthetic community updated", "areaLabel": "Cabarete",
    "aliases": {"owner": "Owner alias", "member": "Member alias", "professional": "Professional alias", "outsider": "Outside alias"},
    "privatePost": "Private progress photo", "networkPost": "Network progress photo", "moderatedPost": "Moderated group update",
    "comment": "Synthetic photo comment", "reply": "Synthetic moderated reply", "reason": "Synthetic access review"}
stage = "checkout"


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def advance(value):
    global stage
    stage = value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def verified_checkout():
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("GITHUB_REPOSITORY") == "ilovedrrealty/realty-api"
        and {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}, "hosted_loopback_required")
    application = Path(os.environ.get("ILDR_BROWSER_APPLICATION_ROOT", "/application"))
    require(application.is_absolute() and application.resolve(strict=True) == application
        and os.statvfs(application).f_flag & os.ST_RDONLY, "readonly_application_required")
    def git(*args):
        return subprocess.check_output(["git", "--no-optional-locks", "-c", f"safe.directory={application}", "-C", str(application), *args],
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"},
            stderr=subprocess.DEVNULL, timeout=15).decode().strip()
    require(git("rev-parse", "HEAD", "HEAD^{tree}").splitlines() == [APP_COMMIT, APP_TREE]
        and not git("status", "--porcelain=v1", "--untracked-files=all") and not (application / ".env").exists()
        and not (application / ".env").is_symlink(), "exact_clean_application_required")
    return application


def claim_database():
    import psycopg2
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    require(re.fullmatch(r"[a-f0-9]{32}", nonce), "owned_nonce_required")
    name = "ildr_browser_ci_" + nonce
    with closing(psycopg2.connect(host=HOST, port=PG_PORT, dbname=name, user="postgres", password="", connect_timeout=4)) as db, db.cursor() as cursor:
        cursor.execute("""SELECT current_database(),host(inet_server_addr()),inet_server_port(),
            current_setting('server_version_num')::int,shobj_description(oid,'pg_database'),
            NOT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public')
            FROM pg_database WHERE datname=current_database()""")
        require(cursor.fetchone() == (name, HOST, PG_PORT, 170006, f"ildr-admin-share-ci:{nonce}", True), "fresh_marked_postgres_required")
    return name, f"postgresql+psycopg2://postgres@{HOST}:{PG_PORT}/{name}"


def configure(application, url):
    os.environ.update(DATABASE_URL=url, JWT_SECRET=secrets.token_urlsafe(48), ENVIRONMENT="development", COOKIE_DOMAIN="",
        ALLOWED_ORIGINS=f"{ORIGIN},{LANDING}", LANDING_URL=LANDING, SUPABASE_URL="", SUPABASE_SERVICE_KEY="",
        RESEND_API_KEY="", EMAIL_FROM="rehearsal@example.com", NOTIFY_EMAIL="", GHL_API_KEY="", GHL_LOCATION_ID="",
        GHL_ENABLED="false", GHL_WEBHOOK_SECRET="", LEAD_DELIVERY_ENABLED="false", CUSTOMER_MESSAGES_ENABLED="false",
        PROPERTY_INQUIRY_REQUIRE_CONSENT="false", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="", PAYPAL_WEBHOOK_ID="",
        TRANSACTION_DOCUMENT_BUCKET="")
    os.chdir(application)
    sys.path.insert(0, str(application))


def frozen_helpers():
    require(NATIVE.is_dir() and os.statvfs(NATIVE).f_flag & os.ST_RDONLY, "readonly_native_helpers_required")
    loaded = {}
    for filename, module_name in (("catalog.py", "catalog"), ("records.py", "groups_retained_records")):
        path = NATIVE / filename
        require(path.is_file() and not path.is_symlink() and sha256(path.read_bytes()).hexdigest() == FROZEN_HELPERS[filename],
                "exact_accepted_native_helper_required")
        spec = spec_from_file_location(module_name, path)
        module = module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        require(Path(module.__file__).resolve() == path.resolve(), "exact_helper_module_required")
        loaded[module_name] = module
    return loaded["catalog"], loaded["groups_retained_records"]


def seed_database(retained):
    import bootstrap_db
    from alembic import command
    from alembic.config import Config
    from database import SessionLocal
    from models.user import User
    from models.property_network import NetworkProperty, NetworkProject
    from utils.security import hash_password
    advance("bootstrap_database")
    bootstrap_db.main()
    command.upgrade(Config("alembic.ini"), "head")
    # This exact accepted function is unchanged. The fifth supported user and
    # known test passwords are added only before the preservation baseline.
    result = retained.seed_old()
    require(result["users"] == 4 and result["accepted_jobs"] == 9, "accepted_retained_seed_required")
    identities = {"admin": retained.IDS["admin"], "owner": retained.IDS["owner"],
        "member": retained.IDS["buyer"], "outsider": retained.IDS["realtor"], "professional": uuid4()}
    accounts = {actor: {"email": f"groups-browser-{actor}@example.com", "password": secrets.token_urlsafe(24)} for actor in ROLES}
    codes = {}
    with SessionLocal.begin() as db:
        for actor, role in ROLES.items():
            user = db.get(User, identities[actor])
            if actor == "professional":
                user = User(id=identities[actor], role=role, status="active", email_verified=True,
                            display_name="Synthetic group professional persona")
                db.add(user)
            require(user.role == role and user.status == "active" and user.email_verified, "supported_active_roles_required")
            if actor == "professional":
                user.email = accounts[actor]["email"]
            else:
                accounts[actor]["email"] = user.email
            user.password_hash = hash_password(accounts[actor]["password"])
        db.flush()
        for actor, identity in identities.items():
            codes[actor] = db.get(User, identity).user_code
        property = NetworkProperty(id=uuid4(), custodian_id=identities["owner"], current_custodian_id=identities["owner"],
            submission_id=uuid4(), title="Unrelated private browser property", location="Synthetic area", address="", notes="",
            property_class="house_villa", relationship="owner", status="active", version=1,
            creation_sha256="a" * 64, authority_acknowledged=True)
        db.add(property)
        db.flush()
        project = NetworkProject(id=uuid4(), property_id=property.id, owner_id=identities["owner"], created_by_id=identities["owner"],
            submission_id=uuid4(), creation_sha256="b" * 64, title="Unrelated private browser project", summary="",
            property_class="house_villa", service_category_ids=["pool_service"], status="open", version=1)
        db.add(project)
        extra = {"unrelatedPropertyId": str(property.id), "unrelatedProjectId": str(project.id)}
    return accounts, identities, codes, {**FACTS, **extra}


def write_owned(path, data, owned):
    require(path.parent == DIRECTORY and DIRECTORY.resolve(strict=True) == DIRECTORY and not path.exists() and not path.is_symlink(),
            "fresh_private_fixture_path_required")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    info = os.fstat(descriptor)
    owned.append((path, info.st_dev, info.st_ino))
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
    require(stat.S_IMODE(path.stat().st_mode) == 0o600, "owner_only_fixture_mode_required")


def replay_body(raw, receive):
    sent = False
    async def replay():
        nonlocal sent
        if sent:
            return await receive()
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}
    return replay


def confined(app, accounts, identities, codes, photo, observations):
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from utils.auth import _decode_token
    actors = {str(identity): actor for actor, identity in identities.items()}
    logins = {canonical(body) for body in accounts.values()}
    codes = {str(value) for value in codes.values()}
    created = {kind: set() for kind in ("group", "post", "comment", "media", "link")}
    tokens = set()
    uuid_pattern = r"[0-9a-f-]{36}"
    allowed_strings = {FACTS["groupName"], FACTS["description"], FACTS["editedDescription"], FACTS["areaLabel"],
        FACTS["privatePost"], FACTS["networkPost"], FACTS["moderatedPost"], FACTS["comment"], FACTS["reply"], FACTS["reason"],
        *FACTS["aliases"].values(), ""}

    def route(path, method):
        if path == "/property-network/groups" and method == "POST":
            return "group_create", "group", set()
        for pattern, verbs, operation, kind, parents in (
            (rf"/property-network/groups/({uuid_pattern})", {"PUT"}, "group_edit", "group", ("group",)),
            (rf"/property-network/groups/({uuid_pattern})/actions", {"POST"}, "archive", "group", ("group",)),
            (rf"/property-network/groups/({uuid_pattern})/membership/actions", {"POST"}, "own_membership", "membership", ("group",)),
            (rf"/property-network/groups/({uuid_pattern})/members/([0-9]+)/actions", {"POST"}, "manage_membership", "membership", ("group", "code")),
            (rf"/property-network/groups/({uuid_pattern})/invite-links", {"POST"}, "link_create", "link", ("group",)),
            (rf"/property-network/groups/({uuid_pattern})/invite-links/({uuid_pattern})/revoke", {"POST"}, "link_revoke", "link", ("group", "link")),
            (rf"/property-network/groups/({uuid_pattern})/posts", {"POST"}, "post_create", "post", ("group",)),
            (rf"/property-network/group-posts/({uuid_pattern})/actions", {"POST"}, "post_moderate", "post", ("post",)),
            (rf"/property-network/group-posts/({uuid_pattern})/comments", {"POST"}, "comment_create", "comment", ("post",)),
            (rf"/property-network/group-comments/({uuid_pattern})/actions", {"POST"}, "comment_hide", "comment", ("comment",)),
            (rf"/property-network/groups/({uuid_pattern})/media", {"POST"}, "media_upload", "media", ("group",)),
        ):
            match = re.fullmatch(pattern, path)
            if match and method in verbs:
                if all(value in (codes if parent == "code" else created[parent]) for parent, value in zip(parents, match.groups())):
                    return operation, kind, set(match.groups())
        if path in {"/property-network/group-invites/resolve", "/property-network/group-invites/accept"} and method == "POST":
            return ("link_resolve", "" , set()) if path.endswith("resolve") else ("link_accept", "membership", set())
        return None

    async def handle(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        request = Request(scope, receive)
        path, method = scope["path"], scope["method"]
        cookie = request.cookies.get("ildr_token")
        actor = actors.get(_decode_token(cookie)) if cookie else None

        async def read_capture(message):
            if message["type"] == "http.response.start" and (path.startswith("/property-network/") or path == "/auth/me"):
                section = "auth-me" if path == "/auth/me" else path.split("/")[2]
                observations["reads"][(actor or "anonymous", section, message["status"])] += 1
            await send(message)
        if method in {"GET", "HEAD", "OPTIONS"}:
            return await app(scope, receive, read_capture)

        async def reject(reason):
            observations["refused"].append(True)
            endpoint_kind = "media" if re.fullmatch(rf"/property-network/groups/({uuid_pattern})/media", path) else (
                "invite" if path in {"/property-network/group-invites/resolve", "/property-network/group-invites/accept"} else (
                    "group" if path.startswith("/property-network/") else "auth" if path in {"/auth/login", "/auth/logout"} else "other"))
            method_kind = method if method in {"POST", "PUT", "PATCH", "DELETE"} else "other"
            print("SOCIAL_GROUPS_FIXTURE_REFUSAL " + json.dumps({"stage": reason, "method": method_kind, "kind": endpoint_kind}, sort_keys=True), flush=True)
            return await JSONResponse({"detail": "Synthetic group scope refused"}, 405,
                headers={"Cache-Control": "no-store"})(scope, receive, send)

        raw = await request.body()
        if len(raw) > 6 * 1024 * 1024:
            return await reject("body_size")
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if path == "/auth/logout" and method == "POST" and raw in (b"", b"{}"):
            return await app(scope, replay_body(raw, receive), send)
        try:
            body = json.loads(raw) if content_type == "application/json" else None
        except (ValueError, UnicodeError):
            body = None
        if path == "/auth/login" and method == "POST" and canonical(body) in logins:
            return await app(scope, replay_body(raw, receive), send)
        action = route(path, method)
        if action is None:
            return await reject("path_or_method")
        operation, kind, _parents = action
        if operation == "media_upload":
            if content_type != "multipart/form-data":
                return await reject("content_type")
            form = await Request(scope, replay_body(raw, receive)).form()
            try:
                upload = form.get("file")
                if set(form) != {"submission_id", "kind", "file"} or upload is None or not hasattr(upload, "read"):
                    return await reject("form_keys")
                if upload.filename != "groups-photo.png":
                    return await reject("filename")
                if await upload.read() != photo:
                    return await reject("bytes")
                if form.get("kind") not in {"group_image", "post"}:
                    return await reject("kind")
                submission_hint = str(UUID(str(form["submission_id"])))
            finally:
                await form.close()
        else:
            if not isinstance(body, dict) or len(raw) > 32768:
                return await reject("json_body")
            for field in ("name", "description", "area_label", "owner_alias", "member_alias", "body", "reason"):
                if field in body and body[field] not in allowed_strings:
                    return await reject("field_allowlist")
            replay_key = (str(identities.get(actor)), str(body.get("submission_id")))
            if operation == "group_create" and (body.get("name") != FACTS["groupName"]
                    or created["group"] and replay_key not in observations["receipts"]):
                return await reject("create_scope")
            if any(str(value) not in created["media"] for value in body.get("media_ids", [])):
                return await reject("media_ids")
            for field in ("image_media_id", "media_id"):
                if body.get(field) is not None and str(body[field]) not in created["media"]:
                    return await reject("media_reference")
            if operation in {"link_create", "link_resolve", "link_accept"}:
                token = body.get("token")
                if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
                    return await reject("token_format")
                if operation != "link_create" and token not in tokens:
                    return await reject("token_scope")
            submission_hint = str(UUID(str(body["submission_id"]))) if operation != "link_resolve" else None
        if operation != "link_resolve" and len(observations["receipts"]) >= 64 \
                and (str(identities.get(actor)), submission_hint) not in observations["receipts"]:
            return await reject("receipt_budget")
        captured, size = [], 0
        async def capture(message):
            nonlocal size
            if message["type"] == "http.response.body":
                size += len(message.get("body", b""))
                require(size <= 131072, "bounded_group_response_required")
            captured.append(message)
        # Anonymous and unauthorized actors with safe owned inputs still reach
        # the real API. This fixture never synthesizes auth/role denials.
        await app(scope, replay_body(raw, receive), capture)
        start = next(item for item in captured if item["type"] == "http.response.start")
        if start["status"] in {200, 201}:
            require(actor in identities, "actual_seeded_cookie_actor_required")
            require(dict(start["headers"]).get(b"cache-control") == b"no-store", "real_group_no_store_required")
            if operation != "link_resolve":
                result = json.loads(b"".join(item.get("body", b"") for item in captured if item["type"] == "http.response.body"))
                submission, target = str(UUID(result["submission_id"])), str(UUID(result["target_id"]))
                key = (str(identities[actor]), submission)
                record = {"operation": result["operation"], "target_id": target, "result_versions": result["result_versions"]}
                require(key in observations["receipts"] or len(observations["receipts"]) < 64, "finite_group_receipt_budget_required")
                require(key not in observations["receipts"] or observations["receipts"][key] == record, "stable_replay_receipt_required")
                observations["receipts"][key] = record
                observations["success_requests"] += 1
                if kind in created:
                    created[kind].add(target)
                if operation == "link_create":
                    tokens.add(body["token"])
                if kind == "group":
                    current = result.get("current") or {}
                    observations["group_states"].append((result["operation"], current.get("visibility"), current.get("status")))
        else:
            observations["denials"][(actor or "anonymous", operation, start["status"])] += 1
        for message in captured:
            await send(message)
    return handle


def verify_database(database, helpers, identities, before_rows, before_catalog, observations, attempts, stored):
    from database import SessionLocal
    from models.social_groups import NetworkGroup, NetworkGroupAction
    old_tables = helpers.OLD_TABLES
    all_tables = tuple(sorted(old_tables + GROUP_TABLES))
    require(helpers.table_names(database) == all_tables and helpers.catalog(database, all_tables) == before_catalog,
            "entire_99_catalog_unchanged_required")
    require(helpers.query(database, "SELECT version_num FROM alembic_version") == [(HEAD,)], "unchanged_group_head_required")
    require(helpers.snapshot(database, old_tables) == before_rows, "all_92_retained_rows_sequences_unchanged_required")
    require(not observations["refused"] and not attempts, "no_unexpected_mutations_or_provider_attempts_required")
    with SessionLocal() as db:
        groups = db.query(NetworkGroup).all()
        require(len(groups) == 1 and groups[0].owner_id == identities["owner"] and groups[0].status == "archived"
            and groups[0].visibility == "private", "actual_final_closed_private_group_required")
        rows = db.query(NetworkGroupAction).all()
        actual = {(str(row.actor_id), str(row.submission_id)): {"operation": row.operation, "target_id": str(row.target_id),
            "result_versions": row.result_versions} for row in rows}
        require(actual == observations["receipts"] and 1 <= len(actual) <= 64, "all_actual_receipts_and_versions_match_required")
        operations = {row.operation for row in rows}
        require({"group_create", "group_edit", "invite", "decline_invite", "accept_invite", "request", "approve_member", "join", "leave",
            "link_create", "link_accept", "link_revoke", "media_upload", "post_create", "comment_create", "set_role", "archive", "remove"} <= operations,
            "finite_normal_group_operations_required")
        require(operations & {"post_hide", "comment_hide"}, "actual_moderation_required")
        require(any(op == "group_edit" and visibility == "network" for op, visibility, _status in observations["group_states"]),
                "explicit_network_publication_required")
        archived_at = next(row.created_at for row in rows if row.operation == "archive")
        require({"remove", "link_revoke"} <= {row.operation for row in rows if row.created_at > archived_at}, "closed_access_revocations_required")
    require(1 <= len(stored) <= 3, "only_finite_synthetic_group_bytes_required")
    require(any(actor == "anonymous" and status in {401, 403} for (actor, _path, status) in observations["reads"]),
            "actual_auth_loss_required")
    require(any(section == "group-media" and status in {403, 404} for (_actor, section, status) in observations["reads"]),
            "actual_private_photo_revocation_required")
    group_snapshot = helpers.snapshot(database, GROUP_TABLES)
    counts = {table: helpers.query(database, "SELECT count(*) FROM public." + table)[0][0] for table in GROUP_TABLES}
    return {"status": "PASS", "schema_tables": 99, "retained_tables_unchanged": 92, "owner_contacts_unchanged": 82,
        "contacts_unchanged": 85, "sources_unchanged": 86, "private_profiles_unchanged": 46, "users_unchanged": 5,
        "accepted_jobs_unchanged": 9, "worker_rows_unchanged": 1, "cms_version_unchanged": 7, "floorplan_rows_unchanged": 2,
        "unrelated_projects_unchanged": 1, "unique_action_receipts": len(observations["receipts"]),
        "successful_group_requests": observations["success_requests"], "group_row_counts": counts,
        "group_rows_sha256": sha256(canonical(group_snapshot).encode()).hexdigest(),
        "synthetic_group_objects": len(stored), "unexpected_mutations": 0, "external_provider_attempts": 0}


def run():
    logging.disable(logging.CRITICAL)
    application = verified_checkout()
    database, url = claim_database()
    configure(application, url)
    helpers, retained = frozen_helpers()
    import httpx, requests, resend, uvicorn
    from PIL import Image
    owned, attempts, stored = [], [], {}
    observations = {"reads": Counter(), "denials": Counter(), "refused": [], "receipts": {}, "group_states": [], "success_requests": 0}

    def refuse_transport(*_args, **_kwargs):
        attempts.append(True)
        raise RuntimeError("external_transport_refused")

    def store_image(key, data):
        require(re.fullmatch(r"groups/[a-f0-9-]{36}/[a-f0-9-]{36}\.webp", key) and key not in stored and len(stored) < 3
            and isinstance(data, bytes) and 0 < len(data) <= 5242880, "owned_immutable_group_image_required")
        stored[key] = data

    def read_image(row):
        data = stored.get(row.object_key)
        require(isinstance(data, bytes) and len(data) == row.byte_count and sha256(data).hexdigest() == row.sha256,
                "synthetic_group_image_hash_required")
        return data

    try:
        with (patch.object(httpx.HTTPTransport, "handle_request", refuse_transport),
              patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse_transport),
              patch.object(requests.Session, "send", refuse_transport), patch.object(urllib_request, "urlopen", refuse_transport),
              patch.object(resend.Emails, "send", refuse_transport), patch.object(smtplib.SMTP, "connect", refuse_transport),
              patch.object(smtplib.SMTP_SSL, "connect", refuse_transport)):
            from main import app
            from services import social_groups
            accounts, identities, codes, facts = seed_database(retained)
            all_tables = tuple(sorted(helpers.OLD_TABLES + GROUP_TABLES))
            require(helpers.table_names(database) == all_tables and helpers.query(database, "SELECT version_num FROM alembic_version") == [(HEAD,)],
                    "fresh_99_group_head_required")
            require(all(helpers.query(database, "SELECT count(*) FROM public." + table) == [(0,)] for table in GROUP_TABLES),
                    "no_preseeded_group_activity_required")
            before_rows, before_catalog = helpers.snapshot(database, helpers.OLD_TABLES), helpers.catalog(database, all_tables)
            image = BytesIO()
            Image.new("RGB", (8, 6), (45, 90, 130)).save(image, format="PNG")
            photo = image.getvalue()
            photo_path = DIRECTORY / "groups-photo.png"
            write_owned(photo_path, photo, owned)
            fixtures = {"scope": "social-groups-only", "accounts": accounts, "codes": codes, "files": {"photo": str(photo_path)}, "facts": facts}
            write_owned(FACTS_FILE, (canonical(fixtures) + "\n").encode(), owned)
            server = uvicorn.Server(uvicorn.Config(confined(app, accounts, identities, codes, photo, observations), host=HOST, port=API_PORT,
                lifespan="off", access_log=False, log_level="warning"))
            handlers = {}
            def shutdown(_number, _frame):
                server.should_exit = True
            try:
                with patch.object(social_groups.storage, "store_image", store_image), patch.object(social_groups.storage, "read_image", read_image):
                    for number in (signal.SIGINT, signal.SIGTERM):
                        handlers[number] = signal.signal(number, shutdown)
                    advance("social_groups_browser")
                    server.run()
                    require(server.started, "actual_group_server_required")
                    advance("verify_social_groups")
                    report = verify_database(database, helpers, identities, before_rows, before_catalog, observations, attempts, stored)
            finally:
                for number, handler in handlers.items():
                    signal.signal(number, handler)
    finally:
        for path, device, inode in reversed(owned):
            if path.exists() or path.is_symlink():
                info = path.lstat()
                require(stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == (device, inode), "owned_fixture_inode_required")
                path.unlink()
        require(all(not path.exists() and not path.is_symlink() for path, _, _ in owned), "private_fixture_cleanup_required")
    print("SOCIAL_GROUPS_FIXTURE_RESULT " + canonical({**report, "private_files_removed": 2}), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        name = type(error).__name__
        print("SOCIAL_GROUPS_FIXTURE_FAILURE " + canonical({"stage": stage,
            "error_type": name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name) else "UnhandledError"}), flush=True)
        raise SystemExit(1) from None
