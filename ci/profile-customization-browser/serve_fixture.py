"""Owned real profile API, private file adapter and exact actor/action ledger."""
from collections import Counter
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO, StringIO
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
from urllib import request as urllib_request
from unittest.mock import patch
from uuid import UUID

COMMIT = "a75f9c26f3bceb14afcb6e1150e1b2a3d50fbada"
TREE = "12bbe32d4a6578bc58741c3f10f55bfccbf07e39"
BASE_COMMIT = "17943ba1714673c51c43cdbb800106f91612f527"
BASE_TREE = "ff940a7b6e983d4c669202746d9676b4b82e9a06"
HEAD = "e8b5a3d1f907"
NATIVE = Path(__file__).resolve().parent.parent / "native-inbox/verify.py"
NATIVE_SHA = "388fcae33bf63234df9739ac7405c1a2d49b4914af2a32d56e9023fdb7ed144e"
FACTS_FILE = Path("/browser-fixtures/fixtures.json")
API, DASH, LANDING = "http://127.0.0.1:8918", "http://127.0.0.1:4175", "http://127.0.0.1:8917"
ROLES = {"admin": "admin", "owner": "owner", "pro": "buyer", "viewer": "realtor", "outsider": "buyer", "blocked": "buyer"}
TARGETS = {"en": "owner", "es": "pro"}
UPLOADS = {"avatar": (32, 24, (17, 51, 85)), "cover": (40, 24, (33, 77, 121)), "replacement": (28, 28, (65, 99, 133))}
BASE_CONTROLS = ("advance_target_version", "author_private", "author_members", "revoke_peer_assignment", "restore_peer_assignment",
                 "target_private", "target_members", "target_avatar_none", "target_avatar_restore",
                 "target_unverified_upload", "target_verified_upload", "target_unverified_save", "target_verified_save")
CONTROL_ORDER = [(locale, action) for locale in ("en", "es") for action in (
    ("advance_target_version", "author_private", "author_members", "peer_members", "revoke_peer_assignment",
     "restore_peer_assignment", "peer_private", *BASE_CONTROLS[5:]) if locale == "en" else BASE_CONTROLS)]
UI_ORDER = ("avatar", "cover", "initial", "replacement", "replace", "stale", "uncertain", "remove", "upload_denied", "save_denied")
STAGE = "source"


class Failure(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Failure(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def native_helpers():
    require(NATIVE.is_file() and not NATIVE.is_symlink() and os.statvfs(NATIVE).f_flag & os.ST_RDONLY
            and sha256(NATIVE.read_bytes()).hexdigest() == NATIVE_SHA, "exact_readonly_native_helper_required")
    spec = spec_from_file_location("profile_browser_native_helpers", NATIVE)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.COMMIT, module.TREE = COMMIT, TREE
    return module, module.helpers()


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
            "ildr-profile-customization-browser-ci:" + nonce, True), "fresh_owned_database_required")
    return name, f"postgresql+psycopg2://postgres@127.0.0.1:55449/{name}"


def baseline_upgrade(base, url):
    reference = Path("/baseline106")
    previous = (base.APP_COMMIT, base.APP_TREE)
    try:
        base.APP_COMMIT, base.APP_TREE = BASE_COMMIT, BASE_TREE
        os.environ["ILDR_BROWSER_APPLICATION_ROOT"] = str(reference)
        require(base.verified_checkout() == reference, "exact_reference179_required")
    finally:
        base.APP_COMMIT, base.APP_TREE = previous
        os.environ.pop("ILDR_BROWSER_APPLICATION_ROOT", None)
    script = "import sys;sys.path.insert(0,'/baseline106');from alembic import command;from alembic.config import Config;command.upgrade(Config('alembic.ini'),'head')"
    result = subprocess.run(["/usr/local/bin/python", "-I", "-B", "-c", script], cwd=reference,
        env=dict(os.environ, DATABASE_URL=url), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    require(result.returncode == 0 and len(result.stdout) <= 2 * 1024 * 1024, "actual_reference106_upgrade_required")


def image_bytes(width, height, color):
    from PIL import Image
    buffer = BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


def seed(native, base, catalog, retained, database, url, storage):
    global STAGE
    STAGE = "previous99"
    native.previous_start(base, url)
    legacy = tuple(sorted(catalog.OLD_TABLES + native.GROUPS))
    require(catalog.table_names(database) == legacy and catalog.query(database,
        "SELECT version_num FROM alembic_version") == [(native.OLD_HEAD,)], "genuine99_previous_required")
    require(retained.seed_old() == native.EXPECTED_SEED, "original_retained_seed_required")
    old_rows, old_schema = catalog.snapshot(database, legacy), catalog.catalog(database, legacy)
    STAGE = "baseline106"
    baseline_upgrade(base, url)
    native.old_preserved(catalog, database, legacy, old_rows, old_schema)
    tables = tuple(sorted(legacy + native.INBOX))
    require(catalog.table_names(database) == tables and catalog.query(database,
        "SELECT version_num FROM alembic_version") == [("d8f4c2a91e67",)], "genuine179_d8_required")
    STAGE = "upgrade_profile"
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config("alembic.ini"), "head")
    require(catalog.table_names(database) == tables and catalog.query(database,
        "SELECT version_num FROM alembic_version") == [(HEAD,)], "actual_e8_106_required")
    native.privacy(catalog, database)
    from database import SessionLocal
    from models.user import User
    from models.property_network import (SocialNetworkProfile as Profile, NetworkProperty as Property,
        NetworkProject as Project, NetworkProjectMember as Member, NetworkMilestone as Milestone,
        NetworkProjectUpdate as Update, NetworkProjectComment as ProjectComment, NetworkMedia as Media,
        NetworkPost as Post, NetworkPostComment as Comment, NetworkBlock as Block)
    from schemas.property_network import ProjectCreate, RegistryCreate
    from services.property_network import creation_digest
    from utils.security import hash_password
    identities = {"admin": retained.IDS["admin"], "owner": retained.IDS["owner"],
                  "pro": retained.IDS["buyer"], "viewer": retained.IDS["realtor"],
                  "outsider": UUID(int=90101), "blocked": UUID(int=90102)}
    accounts = {actor: {"email": "", "password": secrets.token_urlsafe(24)} for actor in ROLES}
    aliases = {actor: "Network alias " + actor for actor in ROLES}
    profiles = {actor: {"display_name": aliases[actor], "bio": "Deliberate network biography " + actor,
        "status_text": "Synthetic network status " + actor, "visibility": "private" if actor in {"owner", "pro"} else "members",
        "wall_policy": "approval_required", "comments_enabled": True, "version": 1,
        "avatar_media_id": None, "cover_media_id": None} for actor in ROLES}
    facts = {"roles": ROLES, "ids": {key: str(value) for key, value in identities.items()}, "targets": TARGETS,
        "aliases": aliases, "profiles": profiles, "accountNames": {}, "codes": {},
        "accountPhotoUrl": API + "/__ci/account-photo",
        "locales": {locale: {"uncertainBio": "Synthetic committed biography " + locale,
            "staleBio": "Synthetic stale attempt " + locale, "deniedBio": "Synthetic denied attempt " + locale,
            "dirtyBio": "Synthetic discarded draft " + locale} for locale in TARGETS},
        "uploads": {locale: {kind: {"name": locale + "-" + kind + ".png", "mime": "image/png", "width": value[0],
            "height": value[1], "rgb": list(value[2])} for kind, value in UPLOADS.items()} for locale in TARGETS},
        "controlOrder": [{"locale": locale, "action": action} for locale, action in CONTROL_ORDER]}
    shared = [UUID(int=92000 + index) for index in range(32)]
    viewer_only, registry = UUID(int=92032), UUID(int=91001)
    original, correction = UUID(int=93000), UUID(int=93999)
    media_ids = {"author": UUID(int=94000), "update": UUID(int=94001)}
    posts = {"owner": UUID(int=95000), "pro": UUID(int=95001)}
    comments = {"owner": UUID(int=95100), "pro": UUID(int=95101)}
    project_comment = UUID(int=95200)
    STAGE = "seed_profile_facts"
    with SessionLocal.begin() as db:
        for actor in ("outsider", "blocked"):
            db.add(User(id=identities[actor], email="synthetic-profile-" + actor + "@example.com", display_name="Account identity " + actor,
                role=ROLES[actor], status="active", email_verified=True, created_at=retained.HISTORY))
        db.flush()
        for actor, identity in identities.items():
            user = db.get(User, identity)
            require(user.role == ROLES[actor] and user.status == "active" and user.email_verified and user.user_code,
                    "six_real_verified_role_identities_required")
            accounts[actor]["email"] = user.email
            user.password_hash = hash_password(accounts[actor]["password"])
            user.avatar_url = facts["accountPhotoUrl"]
            user.google_id = "synthetic-never-copy-google-" + actor
            facts["codes"][actor], facts["accountNames"][actor] = user.user_code, user.display_name
            value = profiles[actor]
            db.add(Profile(user_id=identity, **{key: val for key, val in value.items() if key not in {"avatar_media_id", "cover_media_id"}}))
        db.flush()
        private_body = RegistryCreate(submission_id=UUID(int=98000), title="Synthetic unrelated private registry", location="Synthetic location",
            address="", property_class="house_villa", relationship="owner", notes="Synthetic responsibility only, no title claim", authority_acknowledged=True)
        db.add(Property(id=registry, custodian_id=identities["viewer"], current_custodian_id=identities["viewer"],
            **private_body.model_dump(exclude={"authority_acknowledged"}),
            status="active", version=1, custody_version=1, creation_sha256=creation_digest(private_body), authority_acknowledged=True,
            created_at=retained.HISTORY, updated_at=retained.HISTORY))
        db.flush()
        project_rows = {}
        for index, identity in enumerate([*shared, viewer_only]):
            private = identity == viewer_only
            title = "Synthetic viewer-only project" if private else "Synthetic shared project " + str(index + 1).zfill(2)
            body = ProjectCreate(listing_id=None if private else retained.LISTING, property_id=registry if private else None,
                submission_id=UUID(int=98100 + index), title=title, summary="Private assigned work " + str(index),
                property_class="house_villa", service_category_ids=["pool_service"])
            owner = identities["viewer"] if private else identities["owner"]
            row = Project(id=identity, listing_id=body.listing_id, property_id=body.property_id, owner_id=owner,
                title=body.title, summary=body.summary, property_class=body.property_class, service_category_ids=body.service_category_ids,
                status="in_progress", version=1, created_by_id=owner, submission_id=body.submission_id,
                creation_sha256=creation_digest(body), created_at=retained.HISTORY + timedelta(days=index),
                updated_at=retained.HISTORY + timedelta(days=index))
            db.add(row)
            project_rows[str(identity)] = {"title": row.title, "summary": row.summary, "status": row.status,
                "service_category_ids": row.service_category_ids, "progress": {"total": 2, "completed": 1, "percent": 50.0}}
        db.flush()
        for index, identity in enumerate(shared):
            db.add(Member(id=UUID(int=96000 + index), project_id=identity, user_id=identities["pro"], role="professional", active=True))
            if index < 31:
                db.add(Member(id=UUID(int=96100 + index), project_id=identity, user_id=identities["viewer"], role="viewer", active=True))
        db.add(Member(id=UUID(int=96200), project_id=viewer_only, user_id=identities["pro"], role="professional", active=False))
        for index, identity in enumerate([*shared, viewer_only]):
            for offset, status in enumerate(("completed", "open")):
                db.add(Milestone(id=UUID(int=97000 + index * 2 + offset), project_id=identity,
                    title="Synthetic milestone " + str(offset), status=status, version=1))
        for name, identity in media_ids.items():
            data, width, height = storage.prepare_image(image_bytes(20, 16, (90, 120, 150) if name == "author" else (120, 50, 90)))
            uploader = identities["admin"] if name == "author" else identities["owner"]
            key = f"network/{uploader}/{identity}.webp"
            storage.store_image(key, data)
            db.add(Media(id=identity, uploader_id=uploader, project_id=None if name == "author" else shared[0],
                object_key=key, sha256=sha256(data).hexdigest(), byte_count=len(data), width=width, height=height,
                profile_user_id=uploader if name == "author" else None, profile_kind="avatar" if name == "author" else None,
                created_at=retained.HISTORY))
        db.flush()
        db.get(Profile, identities["admin"]).avatar_media_id = media_ids["author"]
        profiles["admin"]["avatar_media_id"] = str(media_ids["author"])
        history_rows = {}
        for index, identity in enumerate([*shared, viewer_only]):
            uid = UUID(int=93000 + index)
            author = identities["viewer"] if identity == viewer_only else identities["owner"]
            row = Update(id=uid, project_id=identity, author_id=author, submission_id=UUID(int=99000 + index), kind="progress",
                body="Synthetic recorded progress " + str(index + 1).zfill(2), occurred_on=retained.HISTORY.date(),
                media_ids=[str(media_ids["update"])] if identity == shared[0] else [],
                created_at=retained.HISTORY + timedelta(days=index))
            db.add(row)
            history_rows[str(uid)] = {"body": row.body, "project_id": str(identity), "project_title": project_rows[str(identity)]["title"],
                "author_code": facts["codes"]["viewer" if identity == viewer_only else "owner"], "correction_of": None}
        db.flush()
        db.get(Media, media_ids["update"]).update_id = original
        db.add(Update(id=correction, project_id=shared[0], author_id=identities["admin"], submission_id=UUID(int=99999),
            kind="correction", body="Synthetic correction preserves the original", occurred_on=retained.HISTORY.date(),
            correction_of=original, media_ids=[], created_at=retained.HISTORY + timedelta(days=100)))
        history_rows[str(correction)] = {"body": "Synthetic correction preserves the original", "project_id": str(shared[0]),
            "project_title": project_rows[str(shared[0])]["title"], "author_code": facts["codes"]["admin"], "correction_of": str(original)}
        for actor, identity in posts.items():
            db.add(Post(id=identity, profile_user_id=identities[actor], author_id=identities["admin"], submission_id=UUID(int=99500 + (actor == "pro")),
                body="Synthetic independently authorized wall " + actor, media_ids=[], status="visible", created_at=retained.HISTORY))
        db.flush()
        for actor, identity in comments.items():
            db.add(Comment(id=identity, post_id=posts[actor], author_id=identities["admin"], submission_id=UUID(int=99600 + (actor == "pro")),
                body="Synthetic wall photo comment " + actor, hidden=False, created_at=retained.HISTORY))
        db.add(ProjectComment(id=project_comment, update_id=original, author_id=identities["admin"], submission_id=UUID(int=99700),
            body="Synthetic project comment keeps its project actor name", created_at=retained.HISTORY))
        for actor in ("owner", "pro"):
            db.add(Block(blocker_id=identities[actor], blocked_id=identities["blocked"]))
    own_projects = [str(value) for value in reversed(shared)]
    viewer_projects = [str(value) for value in reversed(shared[:31])]
    own_history = [str(correction), *[str(UUID(int=93000 + index)) for index in reversed(range(32))]]
    viewer_history = [str(correction), *[str(UUID(int=93000 + index)) for index in reversed(range(31))]]
    def pages(values):
        return [values[:30], values[30:]]
    facts.update({"media": {key: str(value) for key, value in media_ids.items()}, "sharedProjectID": str(shared[0]),
        "updateID": str(original), "postIDs": {key: str(value) for key, value in posts.items()},
        "commentIDs": {key: str(value) for key, value in comments.items()}, "projectCommentID": str(project_comment),
        "registryID": str(registry), "legacyListingID": str(retained.LISTING),
        "projects": {"byId": project_rows, "shared": [str(value) for value in shared], "peer": str(shared[0]),
            "targetOnly": str(shared[-1]), "viewerOnly": str(viewer_only), "ownPages": pages(own_projects),
            "stakeholderPages": pages(viewer_projects), "revokedPages": pages([value for value in viewer_projects if value != str(shared[0])])},
        "history": {"byId": history_rows, "original": str(original), "correction": str(correction),
            "ownPages": pages(own_history), "stakeholderPages": pages(viewer_history),
            "revokedPages": pages([value for value in viewer_history if value not in {str(original), str(correction)}])}})
    require(catalog.query(database, "SELECT count(*) FROM network_projects") == [(33,)]
        and catalog.query(database, "SELECT count(*) FROM network_project_updates") == [(34,)]
        and catalog.query(database, "SELECT count(*) FROM network_media") == [(2,)]
        and catalog.query(database, "SELECT count(*) FROM users") == [(6,)], "exact_declared_seed_inventory_required")
    return tables, accounts, identities, facts


def request_plan(index):
    require(0 <= index < 20, "only_twenty_ui_mutation_requests_required")
    locale = "en" if index < 10 else "es"
    action = UI_ORDER[index % 10]
    status = 201 if action in {"avatar", "cover", "replacement"} else 409 if action == "stale" else 403 if action.endswith("denied") else 200
    return locale, action, status


def expected_profile_body(facts, observed, locale, action):
    actor = TARGETS[locale]
    initial = facts["profiles"][actor]
    body = {key: initial[key] for key in ("display_name", "bio", "status_text", "wall_policy", "comments_enabled")}
    body.update(visibility="members", expected_version={"initial": 1, "replace": 2, "stale": 3, "uncertain": 4, "remove": 5, "save_denied": 6}[action])
    if action == "initial":
        body.update(avatar_media_id=observed["media"][(locale, "avatar")]["id"], cover_media_id=observed["media"][(locale, "cover")]["id"])
    else:
        body.update(avatar_media_id=None if action in {"remove", "save_denied"} else observed["media"][(locale, "replacement")]["id"], cover_media_id=None)
    if action in {"uncertain", "remove"}:
        body["bio"] = facts["locales"][locale]["uncertainBio"]
    elif action in {"stale", "save_denied"}:
        body["bio"] = facts["locales"][locale]["staleBio" if action == "stale" else "deniedBio"]
    return body


def control(facts, observed, identities, locale, action):
    from database import SessionLocal
    from models.user import User
    from models.property_network import SocialNetworkProfile as Profile, NetworkProjectMember as Member
    index = len(observed["controls"])
    require(index < 28 and CONTROL_ORDER[index] == (locale, action), "exact_ordered_control_required")
    count = len(observed["events"]) - (0 if locale == "en" else 10)
    expected = 5 if action == "advance_target_version" else 8 if action == "target_unverified_upload" else 9 if action in {
        "target_verified_upload", "target_unverified_save"} else 10 if action == "target_verified_save" else 7
    require(count == expected and all(event["complete"] for event in observed["events"]), "exact_control_ui_interleave_required")
    actor = TARGETS[locale]
    with SessionLocal.begin() as db:
        admin = db.get(User, identities["admin"])
        require(admin.role == "admin" and admin.status == "active" and admin.email_verified, "current_actual_admin_control_required")
        target = db.get(Profile, identities[actor])
        if action == "advance_target_version":
            row, field, old, new = target, "version", 3, 4
        elif action.startswith("author_"):
            row, field = db.get(Profile, identities["admin"]), "visibility"
            old, new = ("members", "private") if action == "author_private" else ("private", "members")
        elif action in {"peer_members", "peer_private"}:
            require(locale == "en", "peer_visibility_only_en_required")
            row, field = db.get(Profile, identities["pro"]), "visibility"
            old, new = ("private", "members") if action == "peer_members" else ("members", "private")
            require(row.version == 1, "untouched_es_target_version_required")
        elif action in {"revoke_peer_assignment", "restore_peer_assignment"}:
            row = db.query(Member).filter(Member.project_id == UUID(facts["sharedProjectID"]), Member.user_id == identities["pro"]).one()
            field = "active"
            old, new = (True, False) if action == "revoke_peer_assignment" else (False, True)
            require(row.role == "professional", "exact_peer_membership_role_required")
        elif action in {"target_private", "target_members"}:
            row, field = target, "visibility"
            old, new = ("members", "private") if action == "target_private" else ("private", "members")
            require(row.version == 5, "committed_target_before_final_remove_required")
        elif action in {"target_avatar_none", "target_avatar_restore"}:
            row, field = target, "avatar_media_id"
            chosen = UUID(observed["media"][(locale, "replacement")]["id"])
            old, new = (chosen, None) if action == "target_avatar_none" else (None, chosen)
            require(row.version == 5 and row.cover_media_id is None, "selected_current_replacement_required")
        else:
            require(action in BASE_CONTROLS[-4:], "source_owned_control_action_required")
            row, field = db.get(User, identities[actor]), "email_verified"
            old, new = (True, False) if "unverified" in action else (False, True)
            require(target.version == 6 and target.avatar_media_id is target.cover_media_id is None,
                    "final_profile_before_auth_denials_required")
        before = {column.name: getattr(row, column.name) for column in row.__table__.columns}
        require(before[field] == old and type(before[field]) is type(old), "exact_control_before_value_required")
        setattr(row, field, new)
        db.flush()
        after = {column.name: getattr(row, column.name) for column in row.__table__.columns}
        require(after == {**before, field: new}, "only_declared_control_field_changed_required")
    observed["controls"].append((locale, action))


def boundary(facts, observed, locale, name):
    actor = TARGETS[locale]
    code = facts["codes"][actor]
    cases = {
        "anonymous_profile": (None, f"/property-network/profiles/{code}", 401),
        "anonymous_media": (None, "/property-network/media/" + facts["media"]["author"], 401),
        "outsider_private": ("outsider", f"/property-network/profiles/{code}", 404),
        "blocked_profile": ("blocked", f"/property-network/profiles/{code}", 404),
        "author_private_avatar": ("viewer", "/property-network/media/" + facts["media"]["author"], 404),
        "viewer_private_work": ("viewer", f"/property-network/profiles/{code}/projects", 404),
    }
    if name in {"viewer_old_avatar", "admin_old_avatar"}:
        require((locale, "avatar") in observed["media"], "real_uploaded_avatar_boundary_required")
        cases[name] = ("viewer" if name.startswith("viewer") else "admin",
                       "/property-network/media/" + observed["media"][(locale, "avatar")]["id"], 404)
    require(name in cases, "finite_boundary_name_required")
    return cases[name]


async def validate_upload(scope, raw, receive, base, facts, locale, action):
    from PIL import Image
    from starlette.requests import Request
    request = Request(scope, base.replay_body(raw, receive))
    form = await request.form()
    kind = "avatar" if action == "upload_denied" else action
    specification = facts["uploads"][locale][kind]
    require(set(form) == {"kind", "file"} and len(form.multi_items()) == 2
            and form["kind"] == ("cover" if kind == "cover" else "avatar"), "exact_upload_form_fields_required")
    upload = form["file"]
    require(upload.filename == specification["name"] and upload.content_type == specification["mime"], "exact_upload_name_mime_required")
    payload = await upload.read()
    await upload.close()
    require(0 < len(payload) <= 8192 and payload.startswith(b"\x89PNG\r\n\x1a\n"), "bounded_literal_synthetic_png_required")
    with Image.open(BytesIO(payload)) as image:
        require(image.format == "PNG" and image.size == (specification["width"], specification["height"]), "actual_png_dimensions_required")
        pixels = image.convert("RGBA")
        expected = bytes([*specification["rgb"], 255]) * (specification["width"] * specification["height"])
        require(pixels.tobytes() == expected, "all_synthetic_png_pixels_required")
    return payload


def confined(app, base, accounts, identities, facts, observed, catalog, database):
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from utils.auth import _decode_token
    from utils import private_network_media as storage
    actor_ids = {str(identity): actor for actor, identity in identities.items()}
    login_bodies = {canonical(value): actor for actor, value in accounts.items()}

    async def handle(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        request = Request(scope, receive)
        method, path = scope["method"], scope["path"]
        token = request.cookies.get("ildr_token")
        actor = actor_ids.get(_decode_token(token)) if token else None
        if path == "/__ci/account-photo" and method == "GET":
            observed["account_images"] += 1
            return await Response(image_bytes(8, 8, (180, 90, 30)), media_type="image/png",
                                  headers={"Cache-Control": "no-store"})(scope, receive, send)
        if path == "/__ci/control" and method == "POST":
            raw = await request.body()
            require(actor == "admin" and len(raw) <= 256 and request.headers.get("x-ci-control") == facts["controlSecret"],
                    "owned_signed_in_admin_control_required")
            body = json.loads(raw)
            require(set(body) == {"locale", "action"} and body["locale"] in TARGETS and type(body["action"]) is str,
                    "exact_control_body_required")
            control(facts, observed, identities, body["locale"], body["action"])
            return await JSONResponse({"status": "PASS"}, headers={"Cache-Control": "no-store"})(scope, base.replay_body(raw, receive), send)
        if method in {"GET", "HEAD", "OPTIONS"}:
            label = request.headers.get("x-ci-boundary")
            expected = None
            if label is not None:
                match = re.fullmatch(r"(en|es)_([a-z_]+)", label)
                require(method == "GET" and match, "known_actual_boundary_label_required")
                expected = boundary(facts, observed, match[1], match[2])
                require((actor, path) == expected[:2] and not scope["query_string"] and not request.headers.get("authorization"),
                        "exact_cookie_actor_and_boundary_path_required")
                require(expected[0] is not None or not request.headers.get("cookie"), "genuinely_anonymous_boundary_required")
                if match[2] == "blocked_profile":
                    require(catalog.query(database, "SELECT visibility,avatar_media_id IS NOT NULL FROM social_network_profiles WHERE user_id=%s",
                                          (facts["ids"][TARGETS[match[1]]],)) == [("members", True)],
                            "block_boundary_requires_selected_members_profile_required")
            async def capture(message):
                if message["type"] == "http.response.start" and label is not None:
                    require(message["status"] == expected[2] and dict(message["headers"]).get(b"cache-control") == b"no-store",
                            "real_no_store_authorization_status_required")
                    observed["boundaries"][(label, message["status"])] += 1
                await send(message)
            return await app(scope, receive, capture)
        raw = await request.body()
        if method == "POST" and path == "/auth/login":
            require(len(raw) <= 8192 and canonical(json.loads(raw)) in login_bodies, "only_six_owned_login_bodies_required")
            login_actor = login_bodies[canonical(json.loads(raw))]
            async def capture_login(message):
                if message["type"] == "http.response.start":
                    require(message["status"] == 200, "actual_login_without_limiter_bypass_required")
                    observed["logins"][login_actor] += 1
                await send(message)
            return await app(scope, base.replay_body(raw, receive), capture_login)
        if (method, path) not in {("PUT", "/property-network/me/profile"), ("POST", "/property-network/me/profile/media")}:
            observed["refused"] += 1
            return await JSONResponse({"detail": "owned_profile_scope_refused"}, status_code=405)(scope, base.replay_body(raw, receive), send)
        locale, action, status = request_plan(len(observed["events"]))
        require(actor == TARGETS[locale], "exact_target_actor_ui_write_required")
        multipart = action in {"avatar", "cover", "replacement", "upload_denied"}
        require((method, path) == (("POST", "/property-network/me/profile/media") if multipart else ("PUT", "/property-network/me/profile")),
                "exact_ui_action_endpoint_required")
        payload = None
        if multipart:
            require(len(raw) <= 65536, "bounded_owned_multipart_required")
            payload = await validate_upload(scope, raw, receive, base, facts, locale, action)
            processed = storage.prepare_image(payload)
        else:
            require(len(raw) <= 8192 and json.loads(raw) == expected_profile_body(facts, observed, locale, action),
                    "exact_version_body_selection_and_alias_required")
        before = catalog.snapshot(database, ("network_media", "social_network_profiles", "network_audit"))
        event = {"locale": locale, "action": action, "actor": actor, "status": status, "complete": False}
        observed["events"].append(event)
        started = datetime.now(timezone.utc)
        starts, chunks = [], []
        async def capture_write(message):
            if message["type"] == "http.response.start":
                require(dict(message["headers"]).get(b"cache-control") == b"no-store", "real_write_no_store_required")
                starts.append(message["status"])
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                require(sum(map(len, chunks)) <= 65536, "bounded_actual_write_response_required")
            await send(message)
        await app(scope, base.replay_body(raw, receive), capture_write)
        finished = datetime.now(timezone.utc)
        require(starts == [status], "exact_actual_ui_response_status_required")
        after = catalog.snapshot(database, ("network_media", "social_network_profiles", "network_audit"))
        if status in {403, 409}:
            require(after == before, "denied_or_conflicted_write_has_no_native_delta_required")
        elif multipart:
            response = json.loads(b"".join(chunks))
            require(set(response) == {"id", "url", "content_type", "bytes", "sha256", "width", "height"}
                    and response["content_type"] == "image/webp" and re.fullmatch(r"[0-9a-f-]{36}", response["id"])
                    and response["url"] == "/property-network/media/" + response["id"], "actual_existing_media_contract_required")
            data, width, height = processed
            require((response["bytes"], response["sha256"], response["width"], response["height"])
                    == (len(data), sha256(data).hexdigest(), width, height), "actual_reencoded_bytes_digest_dimensions_required")
            original = {row["id"]: row for row in json.loads(before["network_media"])}
            current = {row["id"]: row for row in json.loads(after["network_media"])}
            require(set(current) - set(original) == {response["id"]} and all(current[key] == value for key, value in original.items()),
                    "exact_one_new_private_media_row_required")
            row = current[response["id"]]
            kind = "cover" if action == "cover" else "avatar"
            require(set(row) == {"id", "uploader_id", "profile_user_id", "profile_kind", "project_id", "update_id", "post_id",
                                 "object_key", "sha256", "byte_count", "width", "height", "created_at"}
                    and started - timedelta(seconds=1) <= datetime.fromisoformat(row["created_at"]) <= finished + timedelta(seconds=1),
                    "exact_media_columns_and_actual_creation_interval_required")
            require(row == {**row, "id": response["id"], "uploader_id": facts["ids"][actor], "profile_user_id": facts["ids"][actor],
                "profile_kind": kind, "project_id": None, "update_id": None, "post_id": None,
                "object_key": f"network/{facts['ids'][actor]}/{response['id']}.webp", "sha256": response["sha256"],
                "byte_count": response["bytes"], "width": width, "height": height}, "new_media_exact_target_and_purpose_required")
            require(all(after[key] == before[key] for key in ("social_network_profiles", "network_audit", "sequences")),
                    "upload_does_not_select_or_audit_profile_required")
            observed["media"][(locale, action)] = {**response, "row": row}
        else:
            response = json.loads(b"".join(chunks))
            body = expected_profile_body(facts, observed, locale, action)
            expected_response = {key: value for key, value in body.items() if key != "expected_version"}
            expected_response.update(user_code=facts["codes"][actor], can_edit=True, version=body["expected_version"] + 1)
            require(response == expected_response, "actual_profile_receipt_matches_exact_attempt_required")
            old = {row["user_id"]: row for row in json.loads(before["social_network_profiles"])}
            new = {row["user_id"]: row for row in json.loads(after["social_network_profiles"])}
            require(set(new) == set(old) and all(new[key] == value for key, value in old.items() if key != facts["ids"][actor]),
                    "only_target_profile_changes_required")
            require(new[facts["ids"][actor]] == {**old[facts["ids"][actor]], **{key: value for key, value in body.items() if key != "expected_version"},
                                                "version": body["expected_version"] + 1}, "exact_target_profile_version_delta_required")
            audits_before = {row["id"]: row for row in json.loads(before["network_audit"])}
            audits_after = {row["id"]: row for row in json.loads(after["network_audit"])}
            added = set(audits_after) - set(audits_before)
            require(len(added) == 1 and all(audits_after[key] == value for key, value in audits_before.items()),
                    "exact_one_append_only_profile_audit_required")
            audit = audits_after[next(iter(added))]
            require(set(audit) == {"id", "actor_id", "target_kind", "target_id", "action", "data", "created_at"}
                    and started - timedelta(seconds=1) <= datetime.fromisoformat(audit["created_at"]) <= finished + timedelta(seconds=1),
                    "actual_audit_creation_interval_and_columns_required")
            require((audit["actor_id"], audit["target_kind"], audit["target_id"], audit["action"], audit["data"])
                    == (facts["ids"][actor], "profile", facts["ids"][actor], "profile_saved",
                        {"version": body["expected_version"] + 1, "visibility": "members"}), "exact_actor_action_version_audit_required")
            require(after["network_media"] == before["network_media"] and after["sequences"] == before["sequences"],
                    "save_does_not_delete_media_or_advance_sequences_required")
            event["audit"] = audit
        event["complete"] = True
    async def guarded(scope, receive, send):
        try:
            return await handle(scope, receive, send)
        except Exception as error:
            if observed["guard_failure"] is None:
                name = type(error).__name__
                cause = getattr(error, "orig", error)
                state = getattr(cause, "pgcode", None)
                reason = str(error) if isinstance(error, Failure) and re.fullmatch(r"[a-z_0-9]{1,100}", str(error)) else "unexpected_failure"
                phase = "control" if scope.get("path") == "/__ci/control" else "read" if scope.get("method") in {"GET", "HEAD", "OPTIONS"} else "mutation"
                observed["guard_failure"] = {"phase": phase,
                    "error_type": name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name) else "UnhandledError",
                    "reason": reason, "sqlstate": state if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state) else "unavailable"}
                print("PROFILE_FIXTURE_GUARD " + canonical(observed["guard_failure"]), flush=True)
            raise
    return guarded


def verify(native, base, catalog, database, tables, baseline, schema, facts, observed, files, attempts, server_start):
    names = ("anonymous_profile", "anonymous_media", "outsider_private", "blocked_profile", "viewer_old_avatar",
             "admin_old_avatar", "author_private_avatar", "viewer_private_work")
    expected_boundaries = Counter({(locale + "_" + name, boundary(facts, observed, locale, name)[2]): 1
                                   for locale in TARGETS for name in names})
    require(observed["boundaries"] == expected_boundaries and observed["logins"] == Counter({actor: 2 for actor in ROLES})
            and observed["controls"] == CONTROL_ORDER and observed["refused"] == 0 and observed["guard_failure"] is None and not attempts,
            "exact_boundary_login_control_and_zero_provider_ledgers_required")
    require(len(observed["events"]) == 20 and all((row["locale"], row["action"], row["status"]) == request_plan(index)
            and row["actor"] == TARGETS[row["locale"]] and row["complete"] for index, row in enumerate(observed["events"])),
            "all_twenty_exact_completed_ui_requests_required")
    require(set(observed["media"]) == {(locale, action) for locale in TARGETS for action in ("avatar", "cover", "replacement")},
            "exact_six_successful_uploads_required")
    current = catalog.snapshot(database, tables)
    allowed = {"social_network_profiles", "network_media", "network_audit"}
    require(all(current[key] == value for key, value in baseline.items() if key not in allowed),
            "all_other102_data_tables_and_all_sequences_unchanged_required")
    old_profiles = {row["user_id"]: row for row in json.loads(baseline["social_network_profiles"])}
    new_profiles = {row["user_id"]: row for row in json.loads(current["social_network_profiles"])}
    require(set(new_profiles) == set(old_profiles) and len(new_profiles) == 6, "same_six_opted_profile_rows_required")
    for actor, identity in facts["ids"].items():
        expected = old_profiles[identity]
        locale = next((key for key, value in TARGETS.items() if value == actor), None)
        if locale:
            expected = {**expected, "version": 6, "visibility": "members", "bio": facts["locales"][locale]["uncertainBio"],
                        "avatar_media_id": None, "cover_media_id": None}
        require(new_profiles[identity] == expected, "exact_final_targets_and_all_transient_profile_controls_restored_required")
    before_media = {row["id"]: row for row in json.loads(baseline["network_media"])}
    after_media = {row["id"]: row for row in json.loads(current["network_media"])}
    expected_media = {value["id"]: value["row"] for value in observed["media"].values()}
    require(len(before_media) == 2 and len(expected_media) == 6 and after_media == {**before_media, **expected_media}
            and not set(before_media) & set(expected_media), "all_eight_private_media_rows_exact_retained_required")
    before_audit = {row["id"]: row for row in json.loads(baseline["network_audit"])}
    after_audit = {row["id"]: row for row in json.loads(current["network_audit"])}
    expected_audit = {row["audit"]["id"]: row["audit"] for row in observed["events"] if "audit" in row}
    require(len(expected_audit) == 8 and after_audit == {**before_audit, **expected_audit}
            and not set(before_audit) & set(expected_audit), "exact_eight_append_only_profile_audits_required")
    require(len(files) == 8 and set(files) == {row["object_key"] for row in after_media.values()},
            "all_private_file_keys_match_retained_media_required")
    for row in after_media.values():
        path, device, inode, digest, size = files[row["object_key"]]
        info = path.lstat()
        data = path.read_bytes()
        require(stat.S_ISREG(info.st_mode) and not path.is_symlink() and (info.st_dev, info.st_ino) == (device, inode)
                and len(data) == info.st_size == size == row["byte_count"]
                and sha256(data).hexdigest() == digest == row["sha256"], "actual_private_webp_file_bytes_and_hashes_required")
    require(catalog.table_names(database) == tables and len(tables) == 106 and catalog.catalog(database, tables) == schema
            and catalog.query(database, "SELECT version_num FROM alembic_version") == [(HEAD,)],
            "all106_catalog_and_separate_e8_version_unchanged_required")
    native.privacy(catalog, database)
    require(all(catalog.query(database, "SELECT count(*) FROM public." + name) == [(0,)] for name in native.INBOX),
            "all_seven_inbox_tables_empty_required")
    require(catalog.query(database, "SELECT pg_postmaster_start_time()") == server_start, "unchanged_database_server_start_required")
    base.verified_checkout()
    return {"status": "PASS", "schema_tables": 106, "model_tables": 105, "retained_native134_run": 45630574556,
        "all_other102_data_tables_sequences_unchanged": True, "exact_profile_delta": True, "exact_media_delta": True,
        "exact_audit_delta": True, "all106_catalog_unchanged": True, "server_start_unchanged": True,
        "private_rls_acl": True, "private_webp_integrity": True, "transient_controls_restored": True,
        "two_version_advances_retained": True, "exact_actor_body_version_media_audit_ledger": True,
        "inbox_writes": 0, "protected_owner_contacts_unchanged": 82, "private_profiles_unchanged": 46,
        "contacts_unchanged": 85, "sources_unchanged": 86, "users_unchanged": 6, "listings_unchanged": 1,
        "accepted_jobs_unchanged": 9, "cms_version_unchanged": 7, "floorplans_unchanged": 2,
        "projects_unchanged": 33, "history_unchanged": 34, "actual_access_boundaries": 16, "actual_logins": 12,
        "ui_mutation_requests": 20, "upload_successes": 6, "profile_saves": 8, "stale_refusals": 2,
        "auth_refusals": 4, "uncertain_profile_writes": 2, "control_transitions": 28, "final_profile_versions": [6, 6],
        "private_media_rows": 8, "profile_audit_additions": 8, "unexpected_mutations": 0, "external_provider_attempts": 0}


def run():
    global STAGE
    logging.disable(logging.CRITICAL)
    import importlib.metadata as metadata
    installed = {re.sub(r"[-_.]+", "-", item.metadata["Name"].lower()): item.version for item in metadata.distributions()}
    accepted = json.loads(Path("/opt/contact-import/baseline-inventory.json").read_text())
    require(installed == {**accepted, "openpyxl": "3.1.5", "defusedxml": "0.7.1", "et-xmlfile": "2.0.0"},
            "exact_dependency_inventory_required")
    native, base = native_helpers()
    application = base.verified_checkout()
    database, url = claim_database()
    base.configure(application, url)
    catalog, retained = base.frozen_helpers()
    server_start = catalog.query(database, "SELECT pg_postmaster_start_time()")
    import httpx, requests, resend, uvicorn
    from utils import private_network_media as storage
    attempts, owned, files = [], [], {}
    observed = {"logins": Counter(), "refused": 0, "controls": [], "events": [], "media": {},
                "boundaries": Counter(), "account_images": 0, "guard_failure": None}
    def refuse(*_args, **_kwargs):
        attempts.append(1)
        raise RuntimeError("external_transport_refused")
    def store(key, data):
        match = re.fullmatch(r"network/([0-9a-f-]{36})/([0-9a-f-]{36})\.webp", key)
        require(match and 0 < len(data) <= 5242880 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
                and key not in files, "only_new_owned_webp_object_keys_required")
        path = FACTS_FILE.parent / (sha256(key.encode()).hexdigest() + ".webp")
        base.write_owned(path, data, owned)
        info = path.lstat()
        files[key] = (path, info.st_dev, info.st_ino, sha256(data).hexdigest(), len(data))
    def read(row):
        require(row.object_key in files, "only_known_private_file_read_required")
        path, device, inode, digest, size = files[row.object_key]
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and not path.is_symlink() and (info.st_dev, info.st_ino) == (device, inode)
                and info.st_size == size == row.byte_count, "private_file_inode_and_size_required")
        data = path.read_bytes()
        require(sha256(data).hexdigest() == digest == row.sha256, "private_file_digest_required")
        return data
    try:
        with (patch.object(httpx.HTTPTransport, "handle_request", refuse), patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse),
              patch.object(requests.Session, "send", refuse), patch.object(urllib_request, "urlopen", refuse),
              patch.object(resend.Emails, "send", refuse), patch.object(smtplib.SMTP, "connect", refuse), patch.object(smtplib.SMTP_SSL, "connect", refuse),
              patch.object(storage, "_get_client", refuse), patch.object(storage, "store_image", store), patch.object(storage, "read_image", read)):
            captured = StringIO()
            with redirect_stdout(captured), redirect_stderr(captured):
                tables, accounts, identities, facts = seed(native, base, catalog, retained, database, url, storage)
            require(len(captured.getvalue().encode()) <= 2 * 1024 * 1024, "bounded_seed_output_required")
            baseline, schema = catalog.snapshot(database, tables), catalog.catalog(database, tables)
            facts["controlSecret"] = secrets.token_urlsafe(32)
            base.write_owned(FACTS_FILE, (canonical({"scope": "profile-customization-only", "accounts": accounts, "facts": facts}) + "\n").encode(), owned)
            from main import app
            server = uvicorn.Server(uvicorn.Config(confined(app, base, accounts, identities, facts, observed, catalog, database),
                host="127.0.0.1", port=8918, lifespan="off", access_log=False, log_level="warning"))
            handlers = {}
            def shutdown(_number, _frame):
                server.should_exit = True
            try:
                for number in (signal.SIGTERM, signal.SIGINT):
                    handlers[number] = signal.signal(number, shutdown)
                STAGE = "browser"
                server.run()
                require(server.started, "actual_api_server_required")
                STAGE = "native_ledger"
                report = verify(native, base, catalog, database, tables, baseline, schema, facts, observed, files, attempts, server_start)
            finally:
                for number, handler in handlers.items():
                    signal.signal(number, handler)
    finally:
        for path, device, inode in reversed(owned):
            if path.exists() or path.is_symlink():
                info = path.lstat()
                require(stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == (device, inode), "owned_private_file_inode_required")
                path.unlink()
        require(all(not path.exists() and not path.is_symlink() for path, _, _ in owned), "private_fixture_file_absence_required")
    require(len(owned) == 9 and len(files) == 8, "exact_nine_private_files_removed_required")
    print("PROFILE_FIXTURE_RESULT " + canonical({**report, "private_facts_removed": 1, "private_webp_files_removed": 8,
                                                "private_files_removed": 9}), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        name = type(error).__name__
        cause = getattr(error, "orig", error)
        state = getattr(cause, "pgcode", None)
        print("PROFILE_FIXTURE_FAILURE " + canonical({"stage": STAGE,
            "error_type": name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name) else "UnhandledError",
            "reason": str(error) if isinstance(error, Failure) and re.fullmatch(r"[a-z_0-9]{1,100}", str(error)) else "unexpected_failure",
            "sqlstate": state if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state) else "unavailable"}), flush=True)
        raise SystemExit(1) from None
