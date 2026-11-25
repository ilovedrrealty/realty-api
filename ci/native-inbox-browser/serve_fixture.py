"""Real private Inbox API, disposable fixture ledger and restored CI controls."""
from collections import Counter
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
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
from uuid import UUID, uuid4

COMMIT = "17943ba1714673c51c43cdbb800106f91612f527"
TREE = "ff940a7b6e983d4c669202746d9676b4b82e9a06"
NATIVE = Path(__file__).resolve().parent.parent / "native-inbox/verify.py"
NATIVE_SHA = "388fcae33bf63234df9739ac7405c1a2d49b4914af2a32d56e9023fdb7ed144e"
DIRECTORY, FACTS_FILE = Path("/browser-fixtures"), Path("/browser-fixtures/fixtures.json")
API, DASH, LANDING = "http://127.0.0.1:8918", "http://127.0.0.1:4175", "http://127.0.0.1:8917"
ROLES = {"admin": "admin", "owner": "owner", "buyer": "buyer", "agent": "realtor",
         "new_agent": "realtor", "member": "owner", "private_member": "owner"}
ACTIONS = {"conversation_opened": 4, "entry_created": 10, "read_marked": 2,
           "visibility_saved": 4, "block_saved": 4, "review_opened": 8}
ROW_COUNTS = {"preferences": 1, "blocks": 0, "conversations": 3, "entries": 46,
              "read_states": 1, "commands": 70, "audit": 70}
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
            "exact_native_helpers_required")
    spec = spec_from_file_location("accepted_native_inbox_browser_helpers", NATIVE)
    native = module_from_spec(spec)
    spec.loader.exec_module(native)
    native.COMMIT, native.TREE = COMMIT, TREE
    base = native.helpers()
    return native, base


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
            "ildr-native-inbox-browser-ci:" + nonce, True), "fresh_owned_database_required")
    return name, f"postgresql+psycopg2://postgres@127.0.0.1:55449/{name}"


def messages():
    return {locale: {"message": f"Synthetic {locale} website <img src=x>\nPlain text 🏡",
        "manual_whatsapp": f"Synthetic {locale} manual WhatsApp record",
        "manual_call": f"Synthetic {locale} manual call record",
        "uncertain": f"Synthetic {locale} committed uncertain entry",
        "admin": f"Synthetic {locale} Admin intervention",
        "denied": f"Synthetic {locale} rejected private draft"} for locale in ("en", "es")}


def expected_ledger(facts):
    result = []
    for index, locale in enumerate(("en", "es")):
        sequence, version = 35 + index * 5, index * 2
        result.extend([
            {"actor": "buyer", "action": "conversation_opened", "fields": {"recipient_id": facts["ids"]["admin"]}},
            {"actor": "buyer", "action": "read_marked", "fields": {"last_read_sequence": sequence}},
            *({"actor": "agent" if kind == "message" else "buyer", "action": "entry_created", "sequence": sequence + number,
               "fields": {"body": facts["messages"][locale][kind], "kind": "message" if kind in {"message", "uncertain"} else kind,
                   **({"reported_direction": "outgoing"} if kind.startswith("manual_") else {}),
                   **({"call_outcome": "no_answer"} if kind == "manual_call" else {})}}
                for number, kind in enumerate(("message", "manual_whatsapp", "manual_call", "uncertain"), 1)),
            {"actor": "owner", "action": "visibility_saved", "fields": {"expected_version": version, "visibility": "private"}},
            {"actor": "owner", "action": "visibility_saved", "fields": {"expected_version": version + 1, "visibility": "members"}},
            {"actor": "owner", "action": "block_saved", "fields": {"blocked": True}},
            {"actor": "owner", "action": "block_saved", "fields": {"blocked": False}},
            {"actor": "admin", "action": "review_opened", "fields": {"source": {"kind": "native", "id": facts["buyerThread"], "recipient_id": None}, "reason": "oversight"}},
            {"actor": "admin", "action": "entry_created", "sequence": sequence + 5, "fields": {"body": facts["messages"][locale]["admin"], "kind": "message"}},
            {"actor": "admin", "action": "review_opened", "fields": {"source": {"kind": "native", "id": facts["buyerThread"], "recipient_id": None}, "reason": "oversight"}},
            *({"actor": "admin", "action": "review_opened", "fields": {"source": facts["legacy"][kind], "reason": "oversight"}}
                for kind in ("service_award", "service_negotiation")),
            {"actor": "member", "action": "conversation_opened", "fields": {"recipient_id": facts["ids"]["owner"]}},
        ])
    require(len(result) == 32, "finite_expected_browser_ledger_required")
    return result


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
    from models.property_network import SocialNetworkProfile, NetworkProject, NetworkProjectMember
    from models.listing import Listing
    from models.service_workflow import NetworkServiceRequest, NetworkServiceRecipient, NetworkServicePreference
    from models.service_conversation import NetworkServiceConversationMessage
    from models.service_negotiation import NetworkServiceNegotiationMessage
    from utils.security import hash_password
    from schemas.native_inbox import ConversationCreate, EntryWrite
    from services import native_inbox as service
    identities = {"admin": retained.IDS["admin"], "owner": retained.IDS["owner"],
        "buyer": retained.IDS["buyer"], "agent": retained.IDS["realtor"],
        **{actor: uuid4() for actor in ("new_agent", "member", "private_member")}}
    accounts = {actor: {"email": f"inbox-browser-{actor}@example.com", "password": secrets.token_urlsafe(24)} for actor in ROLES}
    facts = {"roles": ROLES, "ids": {actor: str(value) for actor, value in identities.items()},
        "names": {actor: "Synthetic Inbox " + actor.replace("_", " ") for actor in ROLES},
        "messages": messages(), "privateMarker": "NEVER_RENDER_PRIVATE_INBOX_PROFILE_OR_CONTACT",
        "initialNativeEntries": 35, "initialUnread": 18, "initialOwnerEntries": 1, "legacyEntries": 35}
    STAGE = "seed_users"
    with SessionLocal.begin() as db:
        for actor, role in ROLES.items():
            user = db.get(User, identities[actor])
            if user is None:
                user = User(id=identities[actor], role=role, status="active", email_verified=True,
                    display_name=facts["names"][actor], email=accounts[actor]["email"])
                db.add(user)
            else:
                accounts[actor]["email"] = user.email
                user.display_name = facts["names"][actor]
            require(user.role == role and user.status == "active" and user.email_verified, "actual_eligible_fixture_roles_required")
            user.password_hash = hash_password(accounts[actor]["password"])
        db.flush()
        db.get(User, identities["buyer"]).assigned_realtor_id = identities["agent"]
        STAGE = "seed_profiles"
        for actor in ("owner", "agent", "new_agent", "member", "private_member"):
            db.add(SocialNetworkProfile(user_id=identities[actor], display_name=facts["names"][actor],
                visibility="private" if actor == "private_member" else "members", wall_policy="own_only",
                bio=facts["privateMarker"], status_text="Private synthetic status", version=1))
        db.flush()
        codes = {actor: db.get(User, identity).user_code for actor, identity in identities.items()}
        facts["codes"] = codes
        listing, project, awarded, negotiation, recipient = (uuid4() for _ in range(5))
        STAGE = "seed_legacy_listing"
        db.add(Listing(id=listing, title="Private Inbox legacy listing", type="villa", transaction="sale", price=1,
            location=facts["privateMarker"], owner_id=identities["owner"], submitted_by=identities["owner"], status="active"))
        db.flush()
        STAGE = "seed_legacy_project"
        db.add(NetworkProject(id=project, listing_id=listing, owner_id=identities["owner"], title="Private legacy project",
            summary=facts["privateMarker"], property_class="house_villa", service_category_ids=["pool_service"], status="open", version=1))
        db.flush()
        STAGE = "seed_legacy_context"
        db.add(NetworkServicePreference(user_id=identities["agent"], service_opt_in=True, member_alias="Synthetic provider",
            category_ids=["pool_service"], property_class_ids=["house_villa"], version=1))
        db.add(NetworkProjectMember(project_id=project, user_id=identities["agent"], role="professional", active=True))
        db.flush()
        for identifier, status, project_id in ((awarded, "awarded", project), (negotiation, "open", None)):
            db.add(NetworkServiceRequest(id=identifier, listing_id=listing, custodian_id=identities["owner"],
                requester_id=identities["owner"], source_listing_type="villa", category_id="pool_service", property_class="house_villa",
                title="Private legacy service", brief=facts["privateMarker"], area_label="Private area", requester_alias="Owner alias",
                recipient_user_codes=[codes["agent"]], scope_sha256="a" * 64, status=status, version=1, project_id=project_id))
        db.flush()
        STAGE = "seed_legacy_recipients"
        db.add(NetworkServiceRecipient(request_id=awarded, provider_id=identities["agent"], user_code=codes["agent"],
            member_alias="Synthetic awarded provider", status="awarded", version=1))
        db.add(NetworkServiceRecipient(id=recipient, request_id=negotiation, provider_id=identities["agent"],
            user_code=codes["agent"], member_alias="Provider alias", status="accepted", version=1))
        db.flush()
        stamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
        STAGE = "seed_legacy_award_history"
        for index in range(35):
            db.add(NetworkServiceConversationMessage(id=UUID(int=71000 + index), request_id=awarded,
                sender_id=identities["owner"], submission_id=uuid4(), sender_role="owner",
                body=f"Synthetic awarded history {index:02d}", created_at=stamp))
        db.flush()
        STAGE = "seed_legacy_negotiation_history"
        for index in range(35):
            db.add(NetworkServiceNegotiationMessage(id=UUID(int=72000 + index), request_id=negotiation, recipient_id=recipient,
                sender_id=identities["agent"], submission_id=uuid4(), sender_role="provider",
                body=f"Synthetic negotiation history {index:02d}", created_at=stamp))
        db.flush()
        STAGE = "seed_legacy_commit"
    facts["legacy"] = {"service_award": service._source("service_award", awarded),
        "service_negotiation": service._source("service_negotiation", negotiation, recipient)}
    with SessionLocal() as db:
        buyer, owner, agent = (db.get(User, identities[actor]) for actor in ("buyer", "owner", "agent"))
        STAGE = "seed_native_open"
        result = service.open_conversation(db, buyer, ConversationCreate(submission_id=uuid4(), recipient_id=identities["agent"]))
        facts["buyerThread"] = result["target_id"]
        STAGE = "seed_native_buyer_history"
        for index in range(35):
            service.post_entry(db, agent if index % 2 == 0 else buyer, UUID(facts["buyerThread"]), EntryWrite(submission_id=uuid4(), kind="message", body=f"Synthetic native history {index:02d}"))
        STAGE = "seed_native_member_history"
        result = service.open_conversation(db, owner, ConversationCreate(submission_id=uuid4(), recipient_id=identities["member"]))
        facts["ownerThread"] = result["target_id"]
        service.post_entry(db, owner, UUID(facts["ownerThread"]), EntryWrite(submission_id=uuid4(), kind="message", body="Synthetic retained member conversation"))
    require(catalog.query(database, "SELECT count(*) FROM users") == [(7,)], "exact_seven_fixture_users_required")
    return legacy, accounts, identities, facts


def confined(app, base, accounts, identities, facts, observed, clock):
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from utils.auth import _decode_token
    from database import SessionLocal
    from models.user import User
    actors = {str(identity): actor for actor, identity in identities.items()}
    logins = {canonical(value) for value in accounts.values()}
    bodies = {value for fields in facts["messages"].values() for value in fields.values()}
    controls = {"assignment_new", "assignment_restore", "agent_unverified", "agent_verify", "clock_future", "clock_reset"}
    sources = {canonical({"kind": "native", "id": facts["buyerThread"], "recipient_id": None}),
        *(canonical(source) for source in facts["legacy"].values())}

    def control(action):
        if action.startswith("assignment_"):
            with SessionLocal.begin() as db:
                user = db.get(User, identities["buyer"])
                before, after = (identities["agent"], identities["new_agent"]) if action == "assignment_new" else (identities["new_agent"], identities["agent"])
                require(user.assigned_realtor_id == before, "ordered_owned_assignment_control_required")
                user.assigned_realtor_id = after
        elif action.startswith("agent_"):
            with SessionLocal.begin() as db:
                user = db.get(User, identities["agent"])
                expected = action == "agent_unverified"
                require(user.email_verified is expected, "ordered_owned_verification_control_required")
                user.email_verified = not expected
        else:
            before, after = (0, 11) if action == "clock_future" else (11, 0)
            require(clock["minutes"] == before, "ordered_owned_clock_control_required")
            clock["minutes"] = after
        observed["controls"][action] += 1

    async def handle(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        request = Request(scope, receive)
        path, method = scope["path"], scope["method"]
        cookie = request.cookies.get("ildr_token")
        actor = actors.get(_decode_token(cookie)) if cookie else None
        if path == "/__ci/control" and method == "POST":
            raw = await request.body()
            require(len(raw) <= 128 and request.headers.get("x-ci-control") == facts["controlSecret"], "owned_control_secret_required")
            body = json.loads(raw)
            require(set(body) == {"action"} and body["action"] in controls, "finite_owned_control_required")
            control(body["action"])
            return await JSONResponse({"status": "PASS"}, headers={"Cache-Control": "no-store"})(scope, base.replay_body(raw, receive), send)
        if method in {"GET", "HEAD", "OPTIONS"}:
            label = None
            if method == "GET":
                if path == "/messaging/policy" and actor is None: label = "anonymous_policy"
                elif path == "/messaging/recipients" and actor in {"buyer", "owner", "agent"}: label = "recipients_" + actor
                elif path == "/messaging/admin/legacy" and actor == "buyer": label = "buyer_admin_legacy"
                elif path == "/messaging/conversations" and request.query_params.get("view") == "oversight" and actor == "owner": label = "owner_oversight"
                elif path == "/messaging/conversations/" + facts["buyerThread"] and actor in {"private_member", "new_agent"}: label = "hidden_thread_" + actor
            async def read_capture(message):
                if label and message["type"] == "http.response.start": observed["boundaries"][(label, message["status"])] += 1
                await send(message)
            return await app(scope, receive, read_capture)
        raw = await request.body()
        body = None
        if len(raw) <= 8192 and request.headers.get("content-type", "").split(";", 1)[0] == "application/json":
            try: body = json.loads(raw)
            except (ValueError, UnicodeError): pass
        if method == "POST" and path == "/auth/login" and canonical(body) in logins:
            observed["logins"] += 1
            return await app(scope, base.replay_body(raw, receive), send)
        action = None
        if isinstance(body, dict) and actor in identities:
            try: submission = str(UUID(body.get("submission_id", "")))
            except (ValueError, TypeError, AttributeError): submission = None
            if submission:
                if method == "POST" and path == "/messaging/conversations" and body.get("recipient_id") in actors:
                    action = "conversation_opened"
                elif method == "PUT" and path == "/messaging/preferences" and actor == "owner" and body.get("visibility") in {"private", "members"}:
                    action = "visibility_saved"
                elif method == "PUT" and path == "/messaging/blocks/" + facts["ids"]["member"] and actor == "owner" and type(body.get("blocked")) is bool:
                    action = "block_saved"
                elif method == "PUT" and path == "/messaging/conversations/" + facts["buyerThread"] + "/read" and actor == "buyer":
                    action = "read_marked"
                elif method == "POST" and path == "/messaging/conversations/" + facts["buyerThread"] + "/entries" and body.get("body") in bodies:
                    action = "entry_created"
                elif method == "POST" and path == "/messaging/admin/reviews" and canonical(body.get("source")) in sources:
                    action = "review_opened"
        if action is None:
            observed["refused"] += 1
            return await JSONResponse({"detail": "owned_inbox_scope_refused"}, status_code=405,
                headers={"Cache-Control": "no-store"})(scope, base.replay_body(raw, receive), send)
        if actor == "agent" and body.get("body") in {fields["denied"] for fields in facts["messages"].values()}:
            index = sum(observed["denied"].values())
            require(action == "entry_created" and index < 4 and body.get("kind") == "message"
                and body.get("body") == facts["messages"]["en" if index < 2 else "es"]["denied"]
                and set(body) == {"submission_id", "kind", "body"}, "only_exact_denied_agent_entry_allowed")
        else:
            expected = expected_ledger(facts)[len(observed["commands"])] if len(observed["commands"]) < 32 else None
            require(expected is not None and actor == expected["actor"] and action == expected["action"]
                and all(body.get(key) == value for key, value in expected["fields"].items()), "exact_ordered_actor_action_payload_allowlist_required")
        captured, size = [], 0
        async def capture(message):
            nonlocal size
            if message["type"] == "http.response.body":
                size += len(message.get("body", b"")); require(size <= 131072, "bounded_command_response_required")
            captured.append(message)
        await app(scope, base.replay_body(raw, receive), capture)
        start = next(item for item in captured if item["type"] == "http.response.start")
        require(dict(start["headers"]).get(b"cache-control") == b"no-store", "actual_private_command_headers_required")
        if start["status"] in {200, 201}:
            receipt = json.loads(b"".join(item.get("body", b"") for item in captured if item["type"] == "http.response.body"))
            require(set(receipt) == {"submission_id", "action", "target_id"} and receipt["submission_id"] == submission and receipt["action"] == action,
                "actual_minimal_command_receipt_required")
            str(UUID(receipt["target_id"]))
            key = (facts["ids"][actor], submission)
            record = {"actor": actor, "action": action, "body": body, "receipt": receipt}
            require(key not in observed["commands"] and len(observed["commands"]) < 32, "exact_unique_browser_commands_required")
            observed["commands"][key] = record
        else:
            require(action == "entry_created" and actor == "agent" and start["status"] in {403, 404}, "only_expected_current_access_denials_required")
            observed["denied"][start["status"]] += 1
        for message in captured:
            await send(message)
    return handle


def verify(native, catalog, database, legacy, identities, facts, baseline, schema, inbox_before, observed, attempts, clock):
    require(clock["minutes"] == 0 and observed["controls"] == Counter({action: 2 for action in (
        "assignment_new", "assignment_restore", "agent_unverified", "agent_verify", "clock_future", "clock_reset")}), "exact_restored_controls_required")
    require(catalog.snapshot(database, legacy) == baseline and catalog.catalog(database, tuple(sorted(legacy + native.INBOX))) == schema
        and catalog.query(database, "SELECT version_num FROM alembic_version") == [(native.HEAD,)], "all99_rows_sequences_catalog_unchanged_required")
    native.privacy(catalog, database)
    require(not attempts and observed["refused"] == 0 and observed["logins"] == 14 and observed["denied"] == Counter({403: 2, 404: 2}), "finite_browser_transport_and_denials_required")
    expected = {"anonymous_policy": 401, "recipients_buyer": 200, "recipients_owner": 200, "recipients_agent": 200,
        "buyer_admin_legacy": 404, "owner_oversight": 404, "hidden_thread_private_member": 404, "hidden_thread_new_agent": 404}
    require(all(observed["boundaries"][(name, status)] >= 2 for name, status in expected.items())
        and not any(status != expected.get(name) for name, status in observed["boundaries"]), "eight_actual_access_boundaries_required")
    current = catalog.snapshot(database, native.INBOX)
    rows = {name.removeprefix("inbox_"): json.loads(current[name]) for name in native.INBOX}
    require({name: len(values) for name, values in rows.items()} == ROW_COUNTS, "exact_new7_row_counts_required")
    commands = observed["commands"]
    require(len(commands) == 32 and Counter(record["action"] for record in commands.values()) == Counter(ACTIONS), "exact_categorized_browser_commands_required")
    for name in ("commands", "audit", "entries"):
        original = json.loads(inbox_before["inbox_" + name])
        selected = [row for row in rows[name] if row in original]
        require(selected == original, "all_seeded_inbox_rows_immutable_required")
    cmd = {(row["actor_id"], row["submission_id"]): row for row in rows["commands"]}
    audits = {(row["actor_id"], row["submission_id"]): row for row in rows["audit"]}
    entries = {row["id"]: row for row in rows["entries"]}
    for (key, item), expected in zip(commands.items(), expected_ledger(facts)):
        command, audit, body, receipt = cmd[key], audits[key], item["body"], item["receipt"]
        require(item["actor"] == expected["actor"] and item["action"] == expected["action"]
            and all(body.get(field) == value for field, value in expected["fields"].items()), "exact_ordered_browser_ledger_required")
        require(command["action"] == audit["action"] == item["action"] and command["target_id"] == audit["target_id"] == receipt["target_id"]
            and audit["actor_role"] == ROLES[item["actor"]] and audit["policy_version"] == "native-inbox-v1", "exact_command_audit_actor_binding_required")
        if item["action"] == "entry_created":
            entry = entries[receipt["target_id"]]
            require(entry["author_id"] == key[0] and entry["submission_id"] == key[1] and entry["author_role"] == ROLES[item["actor"]]
                and entry["conversation_id"] == facts["buyerThread"] and entry["kind"] == body["kind"] and entry["body"] == body["body"]
                and audit["result_sequence"] == entry["sequence"] == expected["sequence"], "exact_entry_payload_actor_sequence_required")
            require(audit["review_id"] == body.get("review_id"), "actual_admin_review_binding_required")
            for field in ("reported_direction", "call_outcome"):
                require(entry[field] == body.get(field), "exact_manual_field_required")
            actual, expected_time = entry["occurred_at"], body.get("occurred_at")
            require((actual is None and expected_time is None) or (actual is not None and expected_time is not None
                and datetime.fromisoformat(actual) == datetime.fromisoformat(expected_time)), "exact_manual_time_required")
        elif item["action"] == "read_marked":
            require(audit["result_sequence"] == body["last_read_sequence"], "exact_shown_read_command_required")
        elif item["action"] == "visibility_saved":
            require(audit["result_visibility"] == body["visibility"], "exact_visibility_audit_required")
        elif item["action"] == "block_saved":
            require(audit["result_flag"] is body["blocked"], "exact_true_false_block_audit_required")
        elif item["action"] == "review_opened":
            source = body["source"]
            require(audit["id"] == receipt["target_id"] and audit["source_kind"] == source["kind"] and audit["source_id"] == source["id"]
                and audit["recipient_id"] == source["recipient_id"] and audit["reason"] == body["reason"], "exact_native_legacy_review_audit_required")
    require(rows["preferences"][0]["user_id"] == facts["ids"]["owner"] and rows["preferences"][0]["visibility"] == "members"
        and rows["preferences"][0]["version"] == 4, "exact_preference_v4_required")
    require(rows["read_states"][0]["user_id"] == facts["ids"]["buyer"] and rows["read_states"][0]["conversation_id"] == facts["buyerThread"]
        and rows["read_states"][0]["last_read_sequence"] == 40, "exact_read_cursor_required")
    counts = Counter(row["action"] for row in rows["audit"])
    require(counts == Counter({**ACTIONS, "conversation_opened": 6, "entry_created": 46}), "all70_audit_actions_accounted_required")
    threads = {row["id"]: row for row in rows["conversations"]}
    original_threads = {row["id"]: row for row in json.loads(inbox_before["inbox_conversations"])}
    require(len(original_threads) == 2 and all({key: value for key, value in threads[identity].items() if key not in {"last_sequence", "last_entry_at"}}
        == {key: value for key, value in original.items() if key not in {"last_sequence", "last_entry_at"}} for identity, original in original_threads.items()),
        "seeded_conversation_identity_unchanged_required")
    added_threads = [row for identity, row in threads.items() if identity not in original_threads]
    require(len(added_threads) == 1 and {added_threads[0]["user_low_id"], added_threads[0]["user_high_id"]} == {facts["ids"]["buyer"], facts["ids"]["admin"]}
        and added_threads[0]["created_by_id"] == facts["ids"]["buyer"] and added_threads[0]["brokerage_subject_id"] == facts["ids"]["buyer"]
        and added_threads[0]["last_sequence"] == 0 and added_threads[0]["policy_version"] == "native-inbox-v1", "only_expected_new_direct_pair_required")
    require(threads[facts["buyerThread"]]["last_sequence"] == 45 and threads[facts["ownerThread"]]["last_sequence"] == 1,
        "exact_native_thread_sequences_required")
    return {"status": "PASS", "schema_tables": 106, "legacy_tables": 99, "all_legacy_rows_sequences_catalog_unchanged": True,
        "inbox_ledger_exact": True, "private_rls_acl": True, "protected_owner_contacts_unchanged": 82,
        "private_profiles_unchanged": 46, "contacts_unchanged": 85, "sources_unchanged": 86, "users_unchanged": 7,
        "accepted_jobs_unchanged": 9, "cms_version_unchanged": 7, "floorplans_unchanged": 2,
        "actual_access_boundaries": 8, "actual_logins": 14, "seed_commands": 38, "browser_commands": 32,
        "browser_command_actions": ACTIONS, "denied_entry_posts": 4, "control_transitions": 12, "inbox_rows": ROW_COUNTS,
        "preference_version": 4, "unexpected_mutations": 0, "external_provider_attempts": 0}


def run():
    global STAGE
    logging.disable(logging.CRITICAL)
    import importlib.metadata as metadata
    installed = {re.sub(r"[-_.]+", "-", item.metadata["Name"].lower()): item.version for item in metadata.distributions()}
    accepted_inventory = json.loads(Path("/opt/contact-import/baseline-inventory.json").read_text())
    require(installed == {**accepted_inventory, "openpyxl": "3.1.5", "defusedxml": "0.7.1", "et-xmlfile": "2.0.0"},
        "exact_accepted_dependency_inventory_required")
    native, base = native_helpers()
    application = base.verified_checkout()
    database, url = claim_database()
    base.configure(application, url)
    catalog, retained = base.frozen_helpers()
    import httpx, requests, resend, uvicorn
    attempts, owned = [], []
    observed = {"commands": {}, "denied": Counter(), "controls": Counter(), "boundaries": Counter(), "logins": 0, "refused": 0}
    clock = {"minutes": 0}
    def refuse(*_args, **_kwargs):
        attempts.append(1)
        raise RuntimeError("external_transport_refused")
    try:
        with (patch.object(httpx.HTTPTransport, "handle_request", refuse), patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse),
            patch.object(requests.Session, "send", refuse), patch.object(urllib_request, "urlopen", refuse),
            patch.object(resend.Emails, "send", refuse), patch.object(smtplib.SMTP, "connect", refuse), patch.object(smtplib.SMTP_SSL, "connect", refuse)):
            captured = StringIO()
            with redirect_stdout(captured), redirect_stderr(captured):
                legacy, accounts, identities, facts = seed(native, base, catalog, retained, database, url)
            require(len(captured.getvalue().encode()) <= 2 * 1024 * 1024, "bounded_seed_output_required")
            before, schema = catalog.snapshot(database, legacy), catalog.catalog(database, tuple(sorted(legacy + native.INBOX)))
            inbox_before = catalog.snapshot(database, native.INBOX)
            require(len(json.loads(inbox_before["inbox_commands"])) == 38 and len(json.loads(inbox_before["inbox_entries"])) == 36,
                "exact_seeded_inbox_ledger_required")
            facts["controlSecret"] = secrets.token_urlsafe(32)
            base.write_owned(FACTS_FILE, (canonical({"scope": "native-inbox-only", "accounts": accounts, "facts": facts}) + "\n").encode(), owned)
            from main import app
            from services import native_inbox as service
            original_now = service.now
            server = uvicorn.Server(uvicorn.Config(confined(app, base, accounts, identities, facts, observed, clock),
                host="127.0.0.1", port=8918, lifespan="off", access_log=False, log_level="warning"))
            handlers = {}
            def shutdown(_number, _frame): server.should_exit = True
            try:
                for number in (signal.SIGTERM, signal.SIGINT): handlers[number] = signal.signal(number, shutdown)
                with patch.object(service, "now", side_effect=lambda: original_now() + timedelta(minutes=clock["minutes"])):
                    STAGE = "browser"
                    server.run()
                require(server.started, "actual_native_inbox_server_required")
                STAGE = "authoritative_ledger"
                report = verify(native, catalog, database, legacy, identities, facts, before, schema, inbox_before, observed, attempts, clock)
            finally:
                for number, handler in handlers.items(): signal.signal(number, handler)
    finally:
        for path, device, inode in reversed(owned):
            if path.exists() or path.is_symlink():
                info = path.lstat()
                require(stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == (device, inode), "owned_fixture_inode_required")
                path.unlink()
        require(all(not path.exists() and not path.is_symlink() for path, _, _ in owned), "private_fixture_cleanup_required")
    print("INBOX_FIXTURE_RESULT " + canonical({**report, "private_files_removed": 1}), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        name = type(error).__name__
        original = getattr(error, "orig", None)
        if original is not None and getattr(original, "pgcode", None) is not None:
            states = {"23502": "not_null", "23503": "foreign_key", "23505": "unique", "23514": "check_or_trigger",
                "23000": "integrity", "55000": "retention", "25P02": "aborted_transaction"}
            constraints = {"users_email_key": "user_email", "users_pkey": "user_identity", "uq_users_user_code": "user_code",
                "ck_network_profile_visibility": "profile_visibility", "ck_network_wall_policy": "profile_wall",
                "ck_network_project_one_property": "project_property", "ck_network_project_submission": "project_submission",
                "ck_service_request_source": "request_source", "ck_service_request_project": "request_project",
                "ck_service_request_recipients": "request_recipients", "ck_service_recipient_bounds": "recipient_bounds"}
            constraint = getattr(getattr(original, "diag", None), "constraint_name", None)
            print("INBOX_FIXTURE_SQL_FAILURE " + canonical({"stage": STAGE,
                "sqlstate_kind": states.get(original.pgcode, "other"),
                "constraint_kind": "none" if constraint is None else constraints.get(constraint, "other")}), flush=True)
        print("INBOX_FIXTURE_FAILURE " + canonical({"stage": STAGE,
            "error_type": name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name) else "UnhandledError",
            "reason": error.args[0] if isinstance(error, Failure) else "external_failure"}), flush=True)
        raise SystemExit(1) from None
