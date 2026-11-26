"""Owned Group author-picture API, original private WebP bytes and exact write ledger.

SOURCE-ONLY DRAFT: final FE union / v5 runtime parent and independent review pending.
"""
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
from urllib.parse import parse_qs
from unittest.mock import patch
from uuid import UUID

COMMIT = "6ef69b249c573eedaea962dad530eaed0a612b24"
TREE = "f1ef617e51e0d8a900d9f4bfc15a559bf676276f"
BASE_COMMIT = "17943ba1714673c51c43cdbb800106f91612f527"
BASE_TREE = "ff940a7b6e983d4c669202746d9676b4b82e9a06"
HEAD = "e8b5a3d1f907"
NATIVE = Path(__file__).resolve().parent.parent / "native-inbox/verify.py"
NATIVE_SHA = "388fcae33bf63234df9739ac7405c1a2d49b4914af2a32d56e9023fdb7ed144e"
FACTS_FILE = Path("/browser-fixtures/fixtures.json")
API, DASH, LANDING = "http://127.0.0.1:8918", "http://127.0.0.1:4175", "http://127.0.0.1:8917"
ROLES = {"admin": "admin", "owner": "owner", "pro": "buyer", "viewer": "realtor", "outsider": "buyer"}
LOCALES = ("en", "es")
CONTROL_NAMES = ("pro_profile_private", "restore_pro_members", "owner_pro_block", "remove_exact_declared_block",
    "pro_avatar_none", "restore_exact_pro_avatar", "viewer_unverified", "restore_viewer_verified")
CONTROL_ORDER = [(locale, name) for locale in LOCALES for name in CONTROL_NAMES]
WRITE_ORDER = ("post_create", "comment_create", "comment_hide", "post_hide")
WRITABLE = ("network_group_posts", "network_group_comments", "network_group_actions")
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
    spec = spec_from_file_location("group_browser_native_helpers", NATIVE)
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
            "ildr-group-author-browser-ci:" + nonce, True), "fresh_owned_database_required")
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


def auxiliary_metadata(catalog, database):
    # Same complete sequence definitions/function ACL projection as native134/162.
    return {
        "sequences": catalog.query(database, """SELECT sequencename,sequenceowner,data_type::text,
            start_value,min_value,max_value,increment_by,cycle,cache_size,last_value
            FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"""),
        "functions": catalog.query(database, """SELECT p.proname,pg_get_function_identity_arguments(p.oid),
            p.prosecdef,p.proleakproof,p.proparallel,p.provolatile,p.proconfig,o.rolname,
            coalesce(r.rolname,'PUBLIC'),a.privilege_type,a.is_grantable
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles o ON o.oid=p.proowner
            CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
            LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE n.nspname='public' AND p.prokind='f'
            ORDER BY p.proname,pg_get_function_identity_arguments(p.oid),coalesce(r.rolname,'PUBLIC'),a.privilege_type,a.is_grantable""")}


def seed(native, base, catalog, retained, database, url, storage):
    global STAGE
    STAGE = "previous99"
    native.previous_start(base, url)
    legacy = tuple(sorted(catalog.OLD_TABLES + native.GROUPS))
    require(len(legacy) == 99 and catalog.table_names(database) == legacy and catalog.query(database,
        "SELECT version_num FROM alembic_version") == [(native.OLD_HEAD,)], "genuine99_previous_required")
    require(retained.seed_old() == native.EXPECTED_SEED, "original_retained_seed_required")
    old_rows, old_schema = catalog.snapshot(database, legacy), catalog.catalog(database, legacy)
    STAGE = "baseline106"
    baseline_upgrade(base, url)
    native.old_preserved(catalog, database, legacy, old_rows, old_schema)
    tables = tuple(sorted(legacy + native.INBOX))
    require(len(tables) == 106 and catalog.table_names(database) == tables and catalog.query(database,
        "SELECT version_num FROM alembic_version") == [("d8f4c2a91e67",)], "genuine179_d8_required")
    STAGE = "upgrade_e8"
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config("alembic.ini"), "head")
    from database import Base, SessionLocal
    # Registration is explicit before the unchanged exact native table-set guard.
    import models.trade_contact
    from models.user import User
    from models.property_network import (SocialNetworkProfile as Profile, NetworkProperty as Property,
        NetworkProject as Project, NetworkProjectUpdate as Update, NetworkMedia as Media)
    from models.social_groups import NetworkGroupAction as Action, NetworkGroupMembership as Membership
    require(catalog.table_names(database) == tables and len(Base.metadata.tables) == 105
        and set(Base.metadata.tables) == set(tables) - {"alembic_version"}
        and catalog.query(database, "SELECT version_num FROM alembic_version") == [(HEAD,)],
        "real_current106_model105_e8_required")
    native.privacy(catalog, database)
    from schemas.property_network import ProjectCreate, RegistryCreate
    from schemas.social_groups import GroupCreate, ManageMember, MembershipAction, GroupPostCreate, GroupCommentCreate
    from services.property_network import creation_digest
    from services import social_groups as groups
    from utils.security import hash_password
    identities = {"admin": retained.IDS["admin"], "owner": retained.IDS["owner"],
        "pro": retained.IDS["buyer"], "viewer": retained.IDS["realtor"], "outsider": UUID(int=750000)}
    accounts = {actor: {"email": "", "password": secrets.token_urlsafe(24)} for actor in ROLES}
    facts = {"roles": ROLES, "ids": {key: str(value) for key, value in identities.items()},
        "codes": {}, "accountNames": {}, "networkAliases": {actor: "Network identity " + actor for actor in ("owner", "pro")},
        "groupAliases": {locale: {actor: "Group " + locale + " identity " + actor for actor in ("owner", "pro", "viewer")}
                         for locale in LOCALES}, "groups": {},
        "media": {name: str(UUID(int=750100 + index)) for index, name in enumerate(("ownerAvatar", "proAvatar", "proCover", "projectPhoto"))},
        "privateProjectId": str(UUID(int=750002)), "privateUpdateId": str(UUID(int=750003)),
        "locales": {locale: {"postBody": "Synthetic normal group post " + locale,
            "commentBody": "Synthetic normal group comment " + locale, "postHideReason": "Synthetic post moderation " + locale,
            "commentHideReason": "Synthetic comment moderation " + locale} for locale in LOCALES},
        "controlOrder": [{"locale": locale, "action": action} for locale, action in CONTROL_ORDER]}
    STAGE = "seed_private_scope"
    with SessionLocal.begin() as db:
        db.add(User(id=identities["outsider"], email="synthetic-group-outsider@example.com", display_name="Synthetic account outsider",
                    role="buyer", status="active", email_verified=True, created_at=retained.HISTORY))
        db.flush()
        for actor, identity in identities.items():
            user = db.get(User, identity)
            require(user.role == ROLES[actor] and user.status == "active" and user.email_verified and user.user_code,
                    "five_actual_account_roles_required")
            accounts[actor]["email"] = user.email
            user.password_hash = hash_password(accounts[actor]["password"])
            facts["codes"][actor], facts["accountNames"][actor] = user.user_code, user.display_name
        for actor in ("owner", "pro"):
            require(facts["networkAliases"][actor] != facts["accountNames"][actor]
                    and all(facts["groupAliases"][locale][actor] not in {facts["accountNames"][actor], facts["networkAliases"][actor]}
                            for locale in LOCALES), "separate_deliberate_identity_aliases_required")
            db.add(Profile(user_id=identities[actor], display_name=facts["networkAliases"][actor], bio="Synthetic deliberate profile",
                status_text="", visibility="members", wall_policy="own_only", comments_enabled=True, version=1))
        registry = UUID(int=750001)
        body = RegistryCreate(submission_id=UUID(int=750011), title="Synthetic unrelated private property", location="Synthetic area",
            address="", property_class="house_villa", relationship="owner", notes="Synthetic authorization scope only",
            authority_acknowledged=True)
        db.add(Property(id=registry, custodian_id=identities["owner"], current_custodian_id=identities["owner"],
            **body.model_dump(exclude={"authority_acknowledged"}), status="active", version=1, custody_version=1,
            creation_sha256=creation_digest(body), authority_acknowledged=True, created_at=retained.HISTORY, updated_at=retained.HISTORY))
        db.flush()
        body = ProjectCreate(property_id=registry, submission_id=UUID(int=750012), title="Synthetic Owner-only project",
            summary="Private project is unrelated to both groups", property_class="house_villa", service_category_ids=["pool_service"])
        db.add(Project(id=UUID(facts["privateProjectId"]), property_id=registry, listing_id=None, owner_id=identities["owner"],
            title=body.title, summary=body.summary, property_class=body.property_class, service_category_ids=body.service_category_ids,
            status="in_progress", version=1, created_by_id=identities["owner"], submission_id=body.submission_id,
            creation_sha256=creation_digest(body), created_at=retained.HISTORY, updated_at=retained.HISTORY))
        db.flush()
        for index, (name, value) in enumerate(facts["media"].items()):
            uploader = identities["owner" if name in {"ownerAvatar", "projectPhoto"} else "pro"]
            data, width, height = storage.prepare_image(image_bytes(20, 16, (50 + 35 * index, 70, 140)))
            key = f"network/{uploader}/{value}.webp"
            storage.store_image(key, data)
            db.add(Media(id=UUID(value), uploader_id=uploader, project_id=UUID(facts["privateProjectId"]) if name == "projectPhoto" else None,
                profile_user_id=None if name == "projectPhoto" else uploader,
                profile_kind=None if name == "projectPhoto" else "cover" if name == "proCover" else "avatar",
                object_key=key, sha256=sha256(data).hexdigest(), byte_count=len(data), width=width, height=height, created_at=retained.HISTORY))
        db.flush()
        db.get(Profile, identities["owner"]).avatar_media_id = UUID(facts["media"]["ownerAvatar"])
        pro = db.get(Profile, identities["pro"])
        pro.avatar_media_id, pro.cover_media_id = UUID(facts["media"]["proAvatar"]), UUID(facts["media"]["proCover"])
        db.add(Update(id=UUID(facts["privateUpdateId"]), project_id=UUID(facts["privateProjectId"]), author_id=identities["owner"],
            submission_id=UUID(int=750013), kind="progress", body="Synthetic private work photo",
            occurred_on=retained.HISTORY.date(), media_ids=[facts["media"]["projectPhoto"]], created_at=retained.HISTORY))
        db.flush()
        db.get(Media, UUID(facts["media"]["projectPhoto"])).update_id = UUID(facts["privateUpdateId"])
    STAGE = "seed_groups"
    seed_operations = []
    with SessionLocal() as db:
        users = {actor: db.get(User, identity) for actor, identity in identities.items()}
        for index, locale in enumerate(LOCALES):
            offset = 751000 + index * 100
            value = groups.create_group(db, users["owner"], GroupCreate(submission_id=UUID(int=offset), name="Synthetic private group " + locale,
                owner_alias=facts["groupAliases"][locale]["owner"], visibility="private", post_policy="members"))
            group_id = UUID(value["target_id"])
            seed_operations.append(("owner", "group_create"))
            for number, actor in enumerate(("pro", "viewer")):
                groups.manage_member(db, users["owner"], group_id, users[actor].user_code,
                    ManageMember(submission_id=UUID(int=offset + 1 + number * 2), expected_membership_version=0, action="invite"))
                seed_operations.append(("owner", "invite"))
                groups.own_membership_action(db, users[actor], group_id, MembershipAction(submission_id=UUID(int=offset + 2 + number * 2),
                    expected_membership_version=1, action="accept_invite", member_alias=facts["groupAliases"][locale][actor]))
                seed_operations.append((actor, "accept_invite"))
            groups.manage_member(db, users["owner"], group_id, users["viewer"].user_code,
                ManageMember(submission_id=UUID(int=offset + 5), expected_membership_version=2, action="set_role", role="moderator"))
            seed_operations.append(("owner", "set_role"))
            post = groups.create_post(db, users["owner"], group_id, GroupPostCreate(submission_id=UUID(int=offset + 6), expected_group_version=1,
                body="Synthetic seeded Owner post " + locale), author_visuals=True)["current"]
            seed_operations.append(("owner", "post_create"))
            comment = groups.create_comment(db, users["pro"], UUID(post["id"]), GroupCommentCreate(submission_id=UUID(int=offset + 7),
                body="Synthetic seeded Pro comment " + locale), author_visuals=True)["current"]
            seed_operations.append(("pro", "comment_create"))
            facts["groups"][locale] = {"id": str(group_id), "name": value["current"]["name"], "seedPostId": post["id"], "seedCommentId": comment["id"]}
        require(db.query(Action).count() == 16 and db.query(Membership).count() == 4
                and len(seed_operations) == 16, "exact_sixteen_seed_actions_four_memberships_required")
        require(Counter(seed_operations) == Counter({("owner", "group_create"): 2, ("owner", "invite"): 4,
            ("pro", "accept_invite"): 2, ("viewer", "accept_invite"): 2, ("owner", "set_role"): 2,
            ("owner", "post_create"): 2, ("pro", "comment_create"): 2}), "exact_seed_actor_operation_inventory_required")
    return tables, accounts, identities, facts


def control(facts, observed, identities, locale, action):
    from database import SessionLocal
    from models.user import User
    from models.property_network import SocialNetworkProfile as Profile, NetworkBlock as Block
    index = len(observed["controls"])
    require(index < 16 and CONTROL_ORDER[index] == (locale, action), "exact_ordered_control_required")
    expected_writes = 2 if action in CONTROL_NAMES[:4] else 4
    require(len(observed["events"]) == (0 if locale == "en" else 4) + expected_writes
        and all(event["complete"] for event in observed["events"]), "exact_control_write_interleave_required")
    with SessionLocal.begin() as db:
        admin = db.get(User, identities["admin"])
        require(admin.role == "admin" and admin.status == "active" and admin.email_verified,
                "actual_signed_in_admin_control_required")
        if action in {"owner_pro_block", "remove_exact_declared_block"}:
            pair = (Block.blocker_id == identities["owner"], Block.blocked_id == identities["pro"])
            rows = db.query(Block).all()
            if action == "owner_pro_block":
                require(not rows, "no_preexisting_block_to_overwrite_required")
                db.add(Block(blocker_id=identities["owner"], blocked_id=identities["pro"]))
                db.flush()
                require(db.query(Block).filter(*pair).count() == db.query(Block).count() == 1,
                        "one_exact_declared_bilateral_block_required")
            else:
                require(len(rows) == 1 and rows[0].blocker_id == identities["owner"]
                        and rows[0].blocked_id == identities["pro"], "exact_block_before_removal_required")
                db.delete(rows[0])
                db.flush()
                require(db.query(Block).count() == 0, "exact_declared_block_removed_required")
        else:
            if action in CONTROL_NAMES[:2]:
                row, field = db.get(Profile, identities["pro"]), "visibility"
                old, new = ("members", "private") if action == "pro_profile_private" else ("private", "members")
            elif action in {"pro_avatar_none", "restore_exact_pro_avatar"}:
                row, field = db.get(Profile, identities["pro"]), "avatar_media_id"
                chosen = UUID(facts["media"]["proAvatar"])
                old, new = (chosen, None) if action == "pro_avatar_none" else (None, chosen)
                require(row.cover_media_id == UUID(facts["media"]["proCover"]) and row.visibility == "members",
                        "deselection_keeps_real_cover_selected_required")
            else:
                require(action in {"viewer_unverified", "restore_viewer_verified"}, "finite_source_control_required")
                row, field = db.get(User, identities["viewer"]), "email_verified"
                old, new = (True, False) if action == "viewer_unverified" else (False, True)
                require(row.role == ROLES["viewer"] and row.status == "active", "unchanged_viewer_account_role_required")
            before = {column.name: getattr(row, column.name) for column in row.__table__.columns}
            require(before[field] == old and type(before[field]) is type(old), "exact_control_before_value_required")
            setattr(row, field, new)
            db.flush()
            db.refresh(row)
            after = {column.name: getattr(row, column.name) for column in row.__table__.columns}
            require(after == {**before, field: new}, "control_changes_only_declared_field_required")
    observed["controls"].append((locale, action))


def boundary(facts, locale, name):
    group = facts["groups"][locale]
    media = facts["media"]
    cases = {
        "anonymous_group": (None, "/property-network/groups/" + group["id"], 401),
        "anonymous_profile_avatar": (None, "/property-network/media/" + media["proAvatar"], 401),
        "outsider_private_group": ("outsider", "/property-network/groups/" + group["id"], 404),
        "outsider_members_selected_avatar": ("outsider", "/property-network/media/" + media["proAvatar"], 200),
        "group_moderator_private_project": ("viewer", "/property-network/projects/" + facts["privateProjectId"], 404),
        "group_moderator_private_project_photo": ("viewer", "/property-network/media/" + media["projectPhoto"], 404),
        "owner_private_author_avatar": ("owner", "/property-network/media/" + media["proAvatar"], 404),
        "platform_admin_private_selected_author_avatar": ("admin", "/property-network/media/" + media["proAvatar"], 200),
        "owner_blocked_author_avatar": ("owner", "/property-network/media/" + media["proAvatar"], 404),
        "owner_deselected_author_avatar": ("owner", "/property-network/media/" + media["proAvatar"], 404),
        "unverified_viewer_group": ("viewer", "/property-network/groups/" + group["id"], 403),
        "unverified_viewer_avatar": ("viewer", "/property-network/media/" + media["proAvatar"], 403)}
    require(name in cases, "finite_actual_boundary_required")
    return cases[name]


def current_control_state(catalog, database, facts, name):
    # Bind boundary claims to actual current profile/block/verification state.
    pro = catalog.query(database, "SELECT visibility,avatar_media_id::text,cover_media_id::text,version FROM social_network_profiles WHERE user_id=%s",
                        (facts["ids"]["pro"],))
    require(len(pro) == 1 and pro[0][3] == 1 and pro[0][2] == facts["media"]["proCover"],
            "actual_profile_choice_without_cover_fallback_required")
    if name in {"owner_private_author_avatar", "platform_admin_private_selected_author_avatar"}:
        require(pro[0][:2] == ("private", facts["media"]["proAvatar"]), "boundary_requires_actual_private_author_required")
    elif name == "owner_blocked_author_avatar":
        require(pro[0][:2] == ("members", facts["media"]["proAvatar"])
                and catalog.query(database, "SELECT blocker_id::text,blocked_id::text FROM network_blocks")
                == [(facts["ids"]["owner"], facts["ids"]["pro"])], "boundary_requires_exact_bilateral_block_required")
    elif name == "owner_deselected_author_avatar":
        require(pro[0][:2] == ("members", None), "boundary_requires_actual_avatar_deselection_required")
    elif name == "outsider_members_selected_avatar":
        require(pro[0][:2] == ("members", facts["media"]["proAvatar"]), "boundary_requires_members_selected_author_required")
    if name in {"unverified_viewer_group", "unverified_viewer_avatar"}:
        require(catalog.query(database, "SELECT email_verified FROM users WHERE id=%s", (facts["ids"]["viewer"],)) == [(False,)],
                "boundary_requires_actual_unverified_viewer_required")


def write_plan(facts, observed, actor, method, path, body):
    from schemas.social_groups import GroupPostCreate, GroupCommentCreate, HideComment, ModeratePost
    index = len(observed["events"])
    require(index < 8, "only_eight_normal_ui_commands_required")
    locale, operation = LOCALES[index // 4], WRITE_ORDER[index % 4]
    selected = facts["groups"][locale]
    events = [event for event in observed["events"] if event["locale"] == locale]
    if operation == "post_create":
        target, endpoint, model, expected_actor, status = selected["id"], "/property-network/groups/" + selected["id"] + "/posts", GroupPostCreate, "pro", 201
        expected = {"expected_group_version": 1, "body": facts["locales"][locale]["postBody"], "media_ids": []}
    elif operation == "comment_create":
        require(len(events) == 1 and events[0]["complete"], "real_previous_post_receipt_required")
        target, model, expected_actor, status = events[0]["receipt"]["target_id"], GroupCommentCreate, "owner", 201
        endpoint = "/property-network/group-posts/" + target + "/comments"
        expected = {"body": facts["locales"][locale]["commentBody"], "media_id": None}
    elif operation == "comment_hide":
        require(len(events) == 2 and all(row["complete"] for row in events), "real_previous_comment_receipt_required")
        target, model, expected_actor, status = events[1]["receipt"]["target_id"], HideComment, "owner", 200
        endpoint = "/property-network/group-comments/" + target + "/actions"
        expected = {"expected_version": 1, "action": "hide", "reason": facts["locales"][locale]["commentHideReason"]}
    else:
        require(len(events) == 3 and all(row["complete"] for row in events), "comment_moderation_before_uncertain_post_required")
        target, model, expected_actor, status = events[0]["receipt"]["target_id"], ModeratePost, "owner", 200
        endpoint = "/property-network/group-posts/" + target + "/actions"
        expected = {"expected_version": 1, "action": "hide", "reason": facts["locales"][locale]["postHideReason"]}
    require(actor == expected_actor and method == "POST" and path == endpoint, "exact_actor_operation_endpoint_required")
    require(isinstance(body, dict) and set(body) == {"submission_id", *expected}, "exact_submission_body_fields_required")
    submission = UUID(body["submission_id"])
    require(str(submission) == body["submission_id"] and all(body[key] == value for key, value in expected.items()),
            "exact_original_submission_and_body_required")
    validated = model.model_validate(body).model_dump(mode="json")
    require(validated == body, "real_source_schema_defaults_required")
    digest_input = {"operation": operation, "target": target, "input": validated}
    # Independent implementation; never call the service's digest to prove itself.
    digest = sha256(json.dumps(digest_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    require(all(row["submission_id"] != body["submission_id"] for row in observed["events"]), "no_duplicate_or_replayed_ui_write_required")
    return {"locale": locale, "operation": operation, "actor": actor, "status": status, "target": target,
        "submission_id": body["submission_id"], "input": validated, "creation_sha256": digest,
        "complete": False, "expected_version": 1 if operation.endswith("create") else 2}


def route_family(method, path):
    uuid = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    patterns = {
        ("GET", "posts"): r"/property-network/groups/" + uuid + r"/posts",
        ("GET", "comments"): r"/property-network/group-posts/" + uuid + r"/comments",
        ("GET", "resolver"): r"/property-network/group-submissions/" + uuid,
        ("POST", "post_create"): r"/property-network/groups/" + uuid + r"/posts",
        ("POST", "comment_create"): r"/property-network/group-posts/" + uuid + r"/comments",
        ("POST", "comment_hide"): r"/property-network/group-comments/" + uuid + r"/actions",
        ("POST", "post_hide"): r"/property-network/group-posts/" + uuid + r"/actions"}
    return next((name for (verb, name), pattern in patterns.items() if verb == method and re.fullmatch(pattern, path)), None)


def check_author_shape(value, visuals):
    keys = {"user_code", "display_name", "avatar_media_id"} if visuals else {"user_code", "display_name"}
    if isinstance(value, dict):
        if "author" in value:
            require(isinstance(value["author"], dict) and set(value["author"]) == keys,
                    "real_old_two_new_three_author_projection_required")
        for key in ("items", "current"):
            if key in value:
                check_author_shape(value[key], visuals)
    elif isinstance(value, list):
        for row in value:
            check_author_shape(row, visuals)


def verify_one_write(facts, identities, event, before, after, receipt):
    old = {table: {row["id"]: row for row in json.loads(before[table])} for table in WRITABLE}
    new = {table: {row["id"]: row for row in json.loads(after[table])} for table in WRITABLE}
    require(before["sequences"] == after["sequences"], "normal_group_commands_do_not_advance_sequences_required")
    is_comment = event["operation"].startswith("comment")
    table = "network_group_comments" if is_comment else "network_group_posts"
    other = "network_group_posts" if is_comment else "network_group_comments"
    require(new[other] == old[other], "only_expected_group_content_table_changes_required")
    target = receipt["target_id"]
    if event["operation"].endswith("create"):
        require(set(new[table]) - set(old[table]) == {target} and not set(old[table]) - set(new[table])
                and all(new[table][key] == value for key, value in old[table].items()), "one_exact_append_only_content_row_required")
        row = new[table][target]
        common = {"id": target, "author_id": identities[event["actor"]], "submission_id": event["submission_id"],
            "body": event["input"]["body"], "version": 1}
        common = {key: str(value) if isinstance(value, UUID) else value for key, value in common.items()}
        if is_comment:
            expected = {**common, "post_id": event["target"], "media_id": None, "hidden": False}
        else:
            expected = {**common, "group_id": event["target"], "media_ids": [], "created_audience": "private", "status": "visible"}
        require(set(row) == {*expected, "created_at"} and all(row[key] == value for key, value in expected.items())
            and datetime.fromisoformat(row["created_at"]) == datetime.fromisoformat(receipt["current"]["created_at"]),
            "exact_actor_body_scope_and_created_content_required")
    else:
        require(target == event["target"] and set(new[table]) == set(old[table]) and target in old[table]
            and all(new[table][key] == value for key, value in old[table].items() if key != target), "only_actual_target_moderated_required")
        expected = {**old[table][target], "version": 2, **({"hidden": True} if is_comment else {"status": "hidden"})}
        require(new[table][target] == expected, "exact_hidden_state_and_version_only_required")
    actions = set(new["network_group_actions"]) - set(old["network_group_actions"])
    require(len(actions) == 1 and not set(old["network_group_actions"]) - set(new["network_group_actions"])
            and all(new["network_group_actions"][key] == value for key, value in old["network_group_actions"].items()),
            "exact_one_immutable_action_per_browser_command_required")
    action = new["network_group_actions"][next(iter(actions))]
    expected = {"group_id": facts["groups"][event["locale"]]["id"], "actor_id": str(identities[event["actor"]]),
        "submission_id": event["submission_id"], "operation": event["operation"], "target_kind": "comment" if is_comment else "post",
        "target_id": target, "creation_sha256": event["creation_sha256"], "membership_id": None, "link_id": None,
        "result_versions": {"version": event["expected_version"]}, "reason": event["input"].get("reason", "")}
    require(set(action) == {*expected, "id", "created_at"} and all(action[key] == value for key, value in expected.items()),
            "independent_action_actor_target_body_digest_version_exact_required")
    applied = datetime.fromisoformat(receipt["applied_at"])
    require(datetime.fromisoformat(action["created_at"]) == applied
        and event["started"] - timedelta(seconds=1) <= applied <= datetime.now(timezone.utc) + timedelta(seconds=1),
        "actual_committed_action_timestamp_required")
    event["action_id"], event["after"] = action["id"], after


def confined(app, base, accounts, identities, facts, observed, catalog, database):
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from utils.auth import _decode_token
    actor_ids = {str(identity): actor for actor, identity in identities.items()}
    login_bodies = {canonical(value): actor for actor, value in accounts.items()}

    async def handle(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        request = Request(scope, receive)
        method, path = scope["method"], scope["path"]
        token = request.cookies.get("ildr_token")
        actor = actor_ids.get(_decode_token(token)) if token else None
        if path == "/__ci/control" and method == "POST":
            raw = await request.body()
            require(actor == "admin" and len(raw) <= 256 and request.headers.get("x-ci-control") == facts["controlSecret"],
                    "owned_signed_in_admin_control_required")
            value = json.loads(raw)
            require(set(value) == {"locale", "action"} and value["locale"] in LOCALES and type(value["action"]) is str,
                    "exact_source_control_body_required")
            control(facts, observed, identities, value["locale"], value["action"])
            return await JSONResponse({"status": "PASS"}, headers={"Cache-Control": "no-store"})(scope, base.replay_body(raw, receive), send)
        if method in {"GET", "HEAD", "OPTIONS"}:
            label = request.headers.get("x-ci-boundary")
            expected = None
            if label is not None:
                match = re.fullmatch(r"(en|es)_([a-z_]+)", label)
                require(method == "GET" and match, "known_actual_boundary_label_required")
                expected = boundary(facts, match[1], match[2])
                require((actor, path) == expected[:2] and not scope["query_string"] and not request.headers.get("authorization"),
                        "exact_actual_cookie_actor_and_boundary_required")
                require(actor is not None or not request.headers.get("cookie"), "genuinely_anonymous_boundary_required")
                current_control_state(catalog, database, facts, match[2])
            family = route_family(method, path)
            query = parse_qs(scope["query_string"].decode("ascii"), strict_parsing=True)
            if family:
                require(set(query) <= {"page", "author_visuals"} and all(len(value) == 1 for value in query.values())
                        and query.get("author_visuals", ["false"])[0] in {"true", "false"}, "bounded_actual_read_projection_required")
            visuals = query.get("author_visuals", ["false"]) == ["true"]
            starts, chunks = [], []
            async def capture_read(message):
                if message["type"] == "http.response.start":
                    starts.append(message["status"])
                    if label is not None:
                        require(message["status"] == expected[2] and dict(message["headers"]).get(b"cache-control") == b"no-store",
                                "real_no_store_authorization_status_required")
                        observed["boundaries"][(label, message["status"])] += 1
                    if family:
                        require(dict(message["headers"]).get(b"cache-control") == b"no-store", "real_group_read_no_store_required")
                elif message["type"] == "http.response.body" and family:
                    chunks.append(message.get("body", b""))
                    require(sum(map(len, chunks)) <= 131072, "bounded_group_read_response_required")
                await send(message)
            await app(scope, receive, capture_read)
            if family and starts == [200]:
                check_author_shape(json.loads(b"".join(chunks)), visuals)
                observed["read_projections"][(family, visuals)] += 1
            return
        raw = await request.body()
        if method == "POST" and path == "/auth/login":
            require(len(raw) <= 8192 and canonical(json.loads(raw)) in login_bodies, "only_five_owned_login_bodies_required")
            login_actor = login_bodies[canonical(json.loads(raw))]
            async def capture_login(message):
                if message["type"] == "http.response.start":
                    require(message["status"] == 200, "actual_login_without_limiter_bypass_required")
                    observed["logins"][login_actor] += 1
                await send(message)
            return await app(scope, base.replay_body(raw, receive), capture_login)
        family = route_family(method, path)
        if family not in WRITE_ORDER:
            observed["refused"] += 1
            return await JSONResponse({"detail": "owned_group_scope_refused"}, status_code=405)(scope, base.replay_body(raw, receive), send)
        require(len(raw) <= 8192 and scope["query_string"] == b"author_visuals=true", "original_opted_in_ui_write_required")
        event = write_plan(facts, observed, actor, method, path, json.loads(raw))
        before = catalog.snapshot(database, WRITABLE)
        event["started"] = datetime.now(timezone.utc)
        observed["events"].append(event)
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
        require(starts == [event["status"]], "actual_expected_native_write_status_required")
        receipt = json.loads(b"".join(chunks))
        require(set(receipt) == {"submission_id", "operation", "target_id", "applied_at", "result_versions", "current"}
            and receipt["submission_id"] == event["submission_id"] and receipt["operation"] == event["operation"]
            and receipt["result_versions"] == {"version": event["expected_version"]}, "exact_original_native_action_receipt_required")
        current = receipt["current"]
        author = "owner" if event["operation"].startswith("comment") else "pro"
        require(isinstance(current, dict) and current["id"] == receipt["target_id"]
            and current["version"] == event["expected_version"] and current["author"] == {
                "user_code": facts["codes"][author], "display_name": facts["groupAliases"][event["locale"]][author],
                "avatar_media_id": facts["media"][author + "Avatar"]}, "real_group_alias_selected_avatar_receipt_required")
        check_author_shape(receipt, True)
        if event["operation"].startswith("comment"):
            require(current["body"] == facts["locales"][event["locale"]]["commentBody"] and current["media_id"] is None
                    and current["hidden"] is (event["operation"] == "comment_hide"), "exact_native_comment_body_state_required")
        else:
            require(current["body"] == facts["locales"][event["locale"]]["postBody"] and current["media_ids"] == []
                    and current["status"] == ("hidden" if event["operation"] == "post_hide" else "visible"),
                    "exact_native_post_body_state_required")
        after = catalog.snapshot(database, WRITABLE)
        verify_one_write(facts, identities, event, before, after, receipt)
        event["receipt"], event["before"], event["finished"], event["complete"] = receipt, before, datetime.now(timezone.utc), True

    async def guarded(scope, receive, send):
        try:
            return await handle(scope, receive, send)
        except Exception as error:
            if observed["guard_failure"] is None:
                phase = "read" if scope.get("method") in {"GET", "HEAD", "OPTIONS"} else "control" if scope.get("path") == "/__ci/control" else "mutation"
                state = getattr(getattr(error, "orig", error), "pgcode", None)
                name = type(error).__name__
                value = {"phase": phase, "error_type": name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name) else "UnhandledError",
                    "reason": str(error) if isinstance(error, Failure) and re.fullmatch(r"[a-z_0-9]{1,100}", str(error)) else "unexpected_failure",
                    "sqlstate": state if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state) else "unavailable"}
                observed["guard_failure"] = value
                print("GROUP_AUTHOR_FIXTURE_GUARD " + canonical(value), flush=True)
            raise
    return guarded


def verify(native, base, catalog, database, tables, baseline, schema, auxiliary, facts, observed, files, attempts, server_start):
    require(not attempts and not observed["refused"] and observed["guard_failure"] is None,
            "zero_provider_unexpected_write_or_guard_failures_required")
    require(observed["logins"] == Counter({actor: 2 for actor in ROLES}), "ten_real_cookie_logins_without_bypass_required")
    require(observed["controls"] == CONTROL_ORDER and len(observed["events"]) == 8
        and all(event["complete"] for event in observed["events"]), "complete_eight_command_sixteen_control_ledger_required")
    require([(event["locale"], event["operation"], event["actor"], event["status"]) for event in observed["events"]]
        == [(locale, operation, "pro" if operation == "post_create" else "owner", 201 if operation.endswith("create") else 200)
            for locale in LOCALES for operation in WRITE_ORDER], "all_ordered_actor_action_status_bindings_required")
    boundaries = {(locale + "_" + name, boundary(facts, locale, name)[2]): 1 for locale in LOCALES for name in (
        "anonymous_group", "anonymous_profile_avatar", "outsider_private_group", "outsider_members_selected_avatar",
        "group_moderator_private_project", "group_moderator_private_project_photo", "owner_private_author_avatar",
        "platform_admin_private_selected_author_avatar", "owner_blocked_author_avatar", "owner_deselected_author_avatar",
        "unverified_viewer_group", "unverified_viewer_avatar")}
    require(observed["boundaries"] == Counter(boundaries), "all_twentyfour_exact_real_no_store_boundaries_required")
    require(all(observed["read_projections"][(family, value)] >= 2 for family in ("posts", "comments", "resolver") for value in (False, True)),
            "both_locales_actual_default_and_opted_read_projection_required")
    after = catalog.snapshot(database, tables)
    unchanged = set(baseline) - {*WRITABLE, "sequences"}
    require(len(unchanged) == 102 and all(after[name] == baseline[name] for name in unchanged)
        and after["sequences"] == baseline["sequences"], "all_other102_data_and_all_sequence_values_exact_required")
    require(all(after[table] == observed["events"][-1]["after"][table] for table in WRITABLE), "no_unobserved_later_group_write_required")
    actions = {row["id"]: row for row in json.loads(after["network_group_actions"])}
    posts = {row["id"]: row for row in json.loads(after["network_group_posts"])}
    comments = {row["id"]: row for row in json.loads(after["network_group_comments"])}
    require(len(actions) == 24 and len(posts) == len(comments) == 4
        and len({event["submission_id"] for event in observed["events"]}) == 8
        and len({event["action_id"] for event in observed["events"]}) == 8,
        "exact_final24_actions_four_posts_four_comments_no_resend_required")
    for event in observed["events"]:
        action = actions[event["action_id"]]
        require(action["creation_sha256"] == event["creation_sha256"] and action["result_versions"] == event["receipt"]["result_versions"],
                "flag_outside_independently_reconstructed_digest_and_versions_required")
        target = event["receipt"]["target_id"]
        row = comments[target] if event["operation"].startswith("comment") else posts[target]
        require(row["version"] == 2 and (row["hidden"] is True if event["operation"].startswith("comment") else row["status"] == "hidden"),
                "all_ui_content_final_moderated_v2_required")
    require(catalog.table_names(database) == tables and len(tables) == 106 and catalog.catalog(database, tables) == schema
        and catalog.query(database, "SELECT version_num FROM alembic_version") == [(HEAD,)]
        and auxiliary_metadata(catalog, database) == auxiliary, "all106_catalog_e8_function_acl_and_all_sequence_definitions_exact_required")
    native.privacy(catalog, database)
    require(all(catalog.query(database, "SELECT count(*) FROM public." + table) == [(0,)] for table in native.INBOX),
            "all_seven_inbox_tables_unchanged_empty_required")
    require(catalog.query(database, "SELECT pg_postmaster_start_time()") == server_start, "unchanged_database_server_start_required")
    media = json.loads(after["network_media"])
    require(len(media) == len(files) == 4 and set(files) == {row["object_key"] for row in media}, "four_exact_private_webp_rows_and_files_required")
    for row in media:
        path, device, inode, digest, size = files[row["object_key"]]
        info, data = path.lstat(), path.read_bytes()
        require(stat.S_ISREG(info.st_mode) and not path.is_symlink() and (info.st_dev, info.st_ino) == (device, inode)
            and info.st_size == len(data) == size == row["byte_count"] and sha256(data).hexdigest() == digest == row["sha256"],
            "all_four_private_webp_bytes_inode_hashes_exact_required")
    require(catalog.query(database, "SELECT count(*) FROM users") == [(5,)]
        and catalog.query(database, "SELECT count(*) FROM network_groups") == [(2,)]
        and catalog.query(database, "SELECT count(*) FROM network_group_memberships") == [(4,)]
        and catalog.query(database, "SELECT count(*) FROM network_projects") == [(1,)]
        and catalog.query(database, "SELECT count(*) FROM network_project_updates") == [(1,)], "declared_seed_scope_counts_required")
    base.verified_checkout()
    return {"status": "PASS", "schema_tables": 106, "model_tables": 105, "retained_native162_run": 45652164070,
        "all_other102_data_tables_sequences_unchanged": True, "exact_group_content_action_ledger": True,
        "all106_catalog_unchanged": True, "server_start_unchanged": True, "private_rls_acl": True,
        "private_function_acl_and_sequence_definitions_unchanged": True, "private_webp_integrity": True,
        "transient_controls_restored": True, "presentation_flag_outside_digest": True,
        "protected_owner_contacts_unchanged": 82, "private_profiles_unchanged": 46, "contacts_unchanged": 85, "sources_unchanged": 86,
        "users_unchanged": 5, "accepted_jobs_unchanged": 9, "cms_version_unchanged": 7, "floorplans_unchanged": 2,
        "groups_unchanged": 2, "memberships_unchanged": 4, "projects_unchanged": 1, "history_unchanged": 1,
        "private_media_unchanged": 4, "seed_actions_unchanged": 16, "browser_actions": 8, "final_actions": 24,
        "new_posts": 2, "new_comments": 2, "ui_mutation_requests": 8, "actual_access_boundaries": 24, "actual_logins": 10,
        "control_transitions": 16, "read_projection_families": 3, "inbox_writes": 0, "unexpected_mutations": 0, "external_provider_attempts": 0}


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
    server_start = catalog.query(database, "SELECT pg_postmaster_start_time()")
    import httpx, requests, resend, uvicorn
    from utils import private_network_media as storage
    attempts, owned, files = [], [], {}
    observed = {"logins": Counter(), "refused": 0, "controls": [], "events": [], "boundaries": Counter(),
        "read_projections": Counter(), "guard_failure": None}
    def refuse(*_args, **_kwargs):
        attempts.append(1)
        raise RuntimeError("external_transport_refused")
    def store(key, data):
        require(re.fullmatch(r"network/[0-9a-f-]{36}/[0-9a-f-]{36}\.webp", key) and 0 < len(data) <= 5242880
            and data[:4] == b"RIFF" and data[8:12] == b"WEBP" and key not in files, "only_four_new_owned_webp_keys_required")
        path = FACTS_FILE.parent / (sha256(key.encode()).hexdigest() + ".webp")
        base.write_owned(path, data, owned)
        info = path.lstat()
        files[key] = (path, info.st_dev, info.st_ino, sha256(data).hexdigest(), len(data))
    def read(row):
        require(row.object_key in files, "only_known_private_webp_reads_required")
        path, device, inode, digest, size = files[row.object_key]
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and not path.is_symlink() and (info.st_dev, info.st_ino) == (device, inode)
            and info.st_size == size == row.byte_count, "private_file_inode_and_size_required")
        data = path.read_bytes()
        require(sha256(data).hexdigest() == digest == row.sha256, "private_webp_digest_required")
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
            baseline, schema, auxiliary = catalog.snapshot(database, tables), catalog.catalog(database, tables), auxiliary_metadata(catalog, database)
            facts["controlSecret"] = secrets.token_urlsafe(32)
            base.write_owned(FACTS_FILE, (canonical({"scope": "group-author-only", "accounts": accounts, "facts": facts}) + "\n").encode(), owned)
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
                report = verify(native, base, catalog, database, tables, baseline, schema, auxiliary, facts, observed, files, attempts, server_start)
            finally:
                for number, handler in handlers.items():
                    signal.signal(number, handler)
    finally:
        for path, device, inode in reversed(owned):
            if path.exists() or path.is_symlink():
                info = path.lstat()
                require(stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == (device, inode), "owned_private_file_inode_required")
                path.unlink()
        require(all(not path.exists() and not path.is_symlink() for path, _, _ in owned), "all_private_fixture_files_absent_required")
    require(len(owned) == 5 and len(files) == 4, "exact_five_private_files_removed_required")
    print("GROUP_AUTHOR_FIXTURE_RESULT " + canonical({**report, "private_facts_removed": 1, "private_webp_files_removed": 4,
                                                   "private_files_removed": 5}), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        name = type(error).__name__
        state = getattr(getattr(error, "orig", error), "pgcode", None)
        print("GROUP_AUTHOR_FIXTURE_FAILURE " + canonical({"stage": STAGE,
            "error_type": name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name) else "UnhandledError",
            "reason": str(error) if isinstance(error, Failure) and re.fullmatch(r"[a-z_0-9]{1,100}", str(error)) else "unexpected_failure",
            "sqlstate": state if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state) else "unavailable"}), flush=True)
        raise SystemExit(1) from None


