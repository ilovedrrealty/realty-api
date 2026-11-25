"""Owned hosted Inbox proof; source tests and schema projections stay distinct."""
import ast
from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
import json
import logging
import os
from pathlib import Path
import re
import smtplib
import subprocess
import sys
from urllib import request as urllib_request
from unittest.mock import patch
import unittest

COMMIT = "2c8b085f9a583ca757cece9ea4ea46c169aab638"
TREE = "8475f663f1628fe2b733507fdfe9f98f2b45adbe"
PREVIOUS_COMMIT = "8957b18520d15dac503e0e4a5ce4b2455135d601"
PREVIOUS_TREE = "11ab69db36eb30881e4f3086578201f584c4092d"
OLD_HEAD, HEAD = "c4e2a7319b60", "d8f4c2a91e67"
ACCEPTED = Path(__file__).resolve().parent.parent / "social-groups-browser/serve_social_groups_fixture.py"
ACCEPTED_SHA = "e0c5f0971c7c13ed47063907f01fa6ef01590b00fa2855eb1ecb16a38b99463e"
INBOX = tuple(sorted(("inbox_preferences", "inbox_blocks", "inbox_conversations", "inbox_commands",
                      "inbox_entries", "inbox_read_states", "inbox_audit")))
GROUPS = ("network_groups", "network_group_memberships", "network_group_invite_links", "network_group_posts",
          "network_group_comments", "network_group_media", "network_group_actions")
EXPECTED_SEED = {"users": 4, "leads": 3, "owner_contacts": 82, "contacts": 85, "sources": 86,
                 "hidden_profiles": 46, "accepted_jobs": 9, "worker_rows": 1, "cms_version": 7, "floorplan_rows": 2}
EXPECTED = {"status": "PASS", "tests": 27, "inbox_tests": 15, "legacy_award_tests": 5,
    "legacy_negotiation_tests": 5, "hosted_safety_tests": 2, "old_tables": 99, "schema_tables": 106,
    "model_tables": 105, "new_private_tables": 7, "owner_contacts_unchanged": 82,
    "private_profiles_unchanged": 46, "accepted_jobs_unchanged": 9, "cms_version_unchanged": 7,
    "floorplans_unchanged": 2, "old_rows_sequences_catalog_unchanged": True,
    "new_model_ddl_projection_equal": True, "private_rls_acl": True, "empty_downgrade_reupgrade_equal": True,
    "external_provider_attempts": 0}
STAGE = "source"
ATTEMPTS = []


class Failure(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise Failure(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def helpers():
    require(ACCEPTED.is_file() and not ACCEPTED.is_symlink()
        and sha256(ACCEPTED.read_bytes()).hexdigest()==ACCEPTED_SHA, "accepted_fixture_bytes_required")
    spec = spec_from_file_location("accepted_inbox_fixture_helpers", ACCEPTED)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.APP_COMMIT, module.APP_TREE = COMMIT, TREE
    return module


def refuse(*args, **kwargs):
    ATTEMPTS.append(1)
    raise RuntimeError("external_transport_refused")


def previous_start(base, url):
    previous = Path("/previous")
    base.APP_COMMIT, base.APP_TREE = PREVIOUS_COMMIT, PREVIOUS_TREE
    os.environ["ILDR_BROWSER_APPLICATION_ROOT"] = str(previous)
    require(base.verified_checkout()==previous, "exact_previous_source_required")
    os.environ.pop("ILDR_BROWSER_APPLICATION_ROOT")
    base.APP_COMMIT, base.APP_TREE = COMMIT, TREE
    # Run actual previous source in a clean child: no current-model imports can
    # create Inbox tables before the real c4 migration bookkeeping exists.
    source = "import sys;sys.path.insert(0,'/previous');import bootstrap_db;bootstrap_db.main();from alembic import command;from alembic.config import Config;command.upgrade(Config('alembic.ini'),'head')"
    result = subprocess.run(["/usr/local/bin/python", "-I", "-B", "-c", source], cwd=previous,
        env=dict(os.environ, DATABASE_URL=url), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    require(result.returncode==0 and len(result.stdout)<=2*1024*1024, "actual_previous_bootstrap_required")


def model_projection(connection, schema):
    # Native PostgreSQL parsing/deparsing provides independent type/default/
    # Boolean/FK/index representations for the exact same seven table names.
    statements = {
        "columns": """SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
            pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE n.nspname=:schema AND c.relname=ANY(:tables) AND a.attnum>0 AND NOT a.attisdropped
            ORDER BY c.relname,a.attnum""",
        "constraints": """SELECT c.relname,k.conname,k.contype,pg_get_constraintdef(k.oid,true) FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_constraint k ON k.conrelid=c.oid
            WHERE n.nspname=:schema AND c.relname=ANY(:tables) ORDER BY c.relname,k.conname""",
        "indexes": """SELECT c.relname,i.relname,x.indisprimary,x.indisunique,x.indisvalid,x.indisready,pg_get_indexdef(i.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_index x ON x.indrelid=c.oid
            JOIN pg_class i ON i.oid=x.indexrelid WHERE n.nspname=:schema AND c.relname=ANY(:tables)
            ORDER BY c.relname,i.relname"""}
    from sqlalchemy import text
    def normalize(value):
        if isinstance(value, str):
            for name in (schema, "public"):
                value = value.replace('"'+name+'".', "").replace(name+".", "")
        return value
    return {key: [[normalize(value) for value in row] for row in connection.execute(text(statement),
        {"schema": schema, "tables": list(INBOX)}).all()] for key, statement in statements.items()}


def compare_models(catalog, database):
    from sqlalchemy import text
    from database import Base, engine
    schema = "inbox_model_"+os.environ["ILDR_BROWSER_CI_NONCE"]
    require(re.fullmatch(r"inbox_model_[a-f0-9]{32}", schema) and len(Base.metadata.tables)==105,
        "exact_model_schema_and105_tables_required")
    catalog.verify_check_render_controls(database)
    with engine.begin() as connection:
        connection.execute(text('CREATE SCHEMA "'+schema+'"'))
        connection.execute(text('SET LOCAL search_path TO "'+schema+'",public'))
        # Force independent model materialization, rather than finding existing
        # public tables through search_path and comparing the catalog to itself.
        Base.metadata.create_all(connection, checkfirst=False)
        require(connection.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname=:schema"), {"schema": schema}).scalar()==105,
            "actual105_model_tables_required")
        connection.execute(text("SET LOCAL search_path TO pg_catalog"))
        actual, expected = model_projection(connection, "public"), model_projection(connection, schema)
        require(actual==expected, "new7_native_model_ddl_projection_required")
        connection.execute(text('DROP SCHEMA "'+schema+'" CASCADE'))


def privacy(catalog, database):
    current = catalog.catalog(database, INBOX)
    require(current["rls"]==[(name, True, False) for name in INBOX] and current["policies"]==[],
        "seven_private_rls_tables_required")
    require(not any(row[1] in {"PUBLIC", "anon", "authenticated", "service_role"} for row in current["acl"]),
        "all_client_privileges_revoked_required")
    require(len(current["triggers"])==7 and all(row[1]=="native_inbox_retention" and row[2]=="O" for row in current["triggers"]),
        "seven_live_native_guards_required")
    rows = catalog.query(database, """SELECT p.prosecdef,coalesce(r.rolname,'PUBLIC'),a.privilege_type
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
        LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE n.nspname='public' AND p.proname='native_inbox_guard'""")
    require(rows and all(not row[0] and row[1] not in {"PUBLIC", "anon", "authenticated", "service_role"} for row in rows),
        "guard_server_only_invoker_required")


def old_preserved(catalog, database, legacy, before, schema):
    require(catalog.snapshot(database, legacy)==before, "all_legacy_rows_and_sequences_unchanged_required")
    current = catalog.catalog(database, legacy)
    added = [row for row in current["functions"] if row not in schema["functions"]]
    require(len(added)==1 and added[0][:2]==("native_inbox_guard", ""), "only_one_new_guard_function_required")
    current["functions"] = [row for row in current["functions"] if row not in added]
    require(current==schema, "all99_legacy_catalog_unchanged_required")


def suite_inventory(application, filename, classname, count):
    tree = ast.parse((application/"tests"/filename).read_text())
    inventory = {node.name: {item.name for item in node.body if isinstance(item, ast.FunctionDef) and item.name.startswith("test_")}
        for node in tree.body if isinstance(node, ast.ClassDef)}
    require(set(inventory)=={classname} and len(inventory[classname])==count, "actual_source_test_inventory_required")
    return inventory[classname]


def run_suite(testcase, count, label, methods):
    captured = StringIO()
    result = unittest.TestResult()
    with redirect_stdout(captured), redirect_stderr(captured):
        unittest.defaultTestLoader.loadTestsFromTestCase(testcase).run(result)
    counts = {"suite": label, "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "skipped": len(result.skipped), "provider_attempts": len(ATTEMPTS)}
    print("INBOX_NATIVE_COUNTS "+canonical(counts), flush=True)
    for case, _ in result.failures+result.errors:
        method = getattr(case, "_testMethodName", "fixture_setup_or_cleanup")
        if method not in methods:
            method = "fixture_setup_or_cleanup"
        print("INBOX_NATIVE_FAILED_METHOD "+label+" "+method, flush=True)
    require(len(captured.getvalue().encode())<=2*1024*1024, "bounded_native_output_required")
    require(result.testsRun==count and result.wasSuccessful() and not result.skipped
        and not result.expectedFailures and not result.unexpectedSuccesses and not ATTEMPTS,
        "all_exact_native_cases_required")


def safety_case(inbox):
    from sqlalchemy.orm import Session
    from models.native_inbox import InboxAudit, InboxCommand, InboxEntry
    from routes import native_inbox
    from services.native_inbox import _source

    class HostedSafetyTests(unittest.TestCase):
        setUpClass = classmethod(inbox.NativeInboxTests.setUpClass.__func__)
        cleanup_owned_schema = classmethod(inbox.NativeInboxTests.cleanup_owned_schema.__func__)
        setUp = inbox.NativeInboxTests.setUp
        stop_providers = inbox.NativeInboxTests.stop_providers
        send = inbox.NativeInboxTests.send
        command = inbox.NativeInboxTests.command
        receipt = inbox.NativeInboxTests.receipt
        open = inbox.NativeInboxTests.open
        message = inbox.NativeInboxTests.message
        review = inbox.NativeInboxTests.review

        def test_blocked_pair_refuses_admin_intervention_without_entry_command_or_audit(self):
            conversation = self.open()
            self.message(conversation)
            self.send("PUT", "/blocks/"+str(self.ids["agent"]), body=self.command(blocked=True))
            review = self.review(_source("native", conversation))
            self.assertEqual(self.send("GET", "/admin/reviews/"+review+"/entries", "admin")["read_only_reason"], "blocked")
            def counts():
                with Session(self.engine) as db:
                    return tuple(db.query(model).count() for model in (InboxEntry, InboxCommand, InboxAudit))
            before = counts()
            self.send("POST", "/conversations/"+conversation+"/entries", "admin",
                self.command(kind="message", body="Admin cannot bypass pair block", review_id=review), expected=409)
            self.assertEqual(counts(), before)

        def test_actual_bounded_payload_origin_and_rate_limit_are_enforced(self):
            response = self.client.put("/messaging/preferences", headers={**self.headers["buyer"], "Content-Type": "application/json"},
                content=b"x"*(64*1024+1))
            self.assertEqual(response.status_code, 413)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("xxxxx", response.text)
            response = self.client.put("/messaging/preferences", json=self.command(expected_version=0, visibility="private"),
                headers={**self.headers["buyer"], "Origin": "https://untrusted.invalid"})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.app.state.limiter = native_inbox.limiter
            native_inbox.limiter.reset()
            body = self.command(expected_version=0, visibility="private")
            with patch.object(native_inbox.limiter, "enabled", True):
                for _ in range(30):
                    self.send("PUT", "/preferences", body=body)
                self.send("PUT", "/preferences", body=body, expected=429)
            native_inbox.limiter.reset()

    return HostedSafetyTests


def run():
    global STAGE
    logging.disable(logging.CRITICAL)
    import importlib.metadata as metadata
    baseline = json.loads(Path("/opt/inbox-native/baseline-inventory.json").read_text())
    actual = {re.sub(r"[-_.]+", "-", item.metadata["Name"].lower()): item.version for item in metadata.distributions()}
    require(actual=={**baseline, "openpyxl": "3.1.5", "defusedxml": "0.7.1", "et-xmlfile": "2.0.0"},
        "unchanged_dependency_inventory_required")
    base = helpers()
    application = base.verified_checkout()
    database, url = base.claim_database()
    base.configure(application, url)
    os.environ["ALLOWED_ORIGINS"] = "http://localhost:5174"
    catalog, retained = base.frozen_helpers()
    legacy = tuple(sorted(catalog.OLD_TABLES+GROUPS))
    require(len(legacy)==99 and "alembic_version" in legacy, "exact_legacy99_inventory_required")
    import httpx, requests, resend
    captured = StringIO()
    with (patch.object(httpx.HTTPTransport, "handle_request", refuse),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse),
        patch.object(requests.Session, "send", refuse), patch.object(urllib_request, "urlopen", refuse),
        patch.object(resend.Emails, "send", refuse), patch.object(smtplib.SMTP, "connect", refuse),
        patch.object(smtplib.SMTP_SSL, "connect", refuse)):
        STAGE = "previous99"
        previous_start(base, url)
        require(catalog.table_names(database)==legacy and catalog.query(database, "SELECT version_num FROM alembic_version")==[(OLD_HEAD,)],
            "real_previous99_head_required")
        with redirect_stdout(captured), redirect_stderr(captured):
            require(retained.seed_old()==EXPECTED_SEED, "accepted_retained_seed_required")
        before, schema = catalog.snapshot(database, legacy), catalog.catalog(database, legacy)
        STAGE = "upgrade106"
        from database import engine
        from sqlalchemy import text
        with engine.begin() as connection:
            connection.execute(text("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon,authenticated,service_role"))
        with redirect_stdout(captured), redirect_stderr(captured):
            from alembic import command
            from alembic.config import Config
            command.upgrade(Config("alembic.ini"), "head")
        require(catalog.table_names(database)==tuple(sorted(legacy+INBOX)) and catalog.query(database,
            "SELECT version_num FROM alembic_version")==[(HEAD,)], "actual106_and_new_head_required")
        old_preserved(catalog, database, legacy, before, schema)
        STAGE = "model_parity"
        compare_models(catalog, database)
        privacy(catalog, database)
        STAGE = "empty_rollback"
        upgraded = catalog.catalog(database, tuple(sorted(legacy+INBOX)))
        with redirect_stdout(captured), redirect_stderr(captured):
            command.downgrade(Config("alembic.ini"), OLD_HEAD)
        require(catalog.table_names(database)==legacy and catalog.query(database,
            "SELECT version_num FROM alembic_version")==[(OLD_HEAD,)] and catalog.catalog(database, legacy)==schema
            and catalog.snapshot(database, legacy)==before, "empty_rollback_preserves_all99_required")
        with redirect_stdout(captured), redirect_stderr(captured):
            command.upgrade(Config("alembic.ini"), "head")
        require(catalog.table_names(database)==tuple(sorted(legacy+INBOX)) and catalog.query(database,
            "SELECT version_num FROM alembic_version")==[(HEAD,)] and catalog.catalog(database, tuple(sorted(legacy+INBOX)))==upgraded,
            "empty_reupgrade_full106_catalog_equal_required")
        STAGE = "inbox15"
        os.environ["SEARCH_FILTER_TEST_DATABASE_URL"] = url
        sys.path.insert(0, str(application/"tests"))
        import test_native_inbox as inbox
        methods = suite_inventory(application, "test_native_inbox.py", "NativeInboxTests", 15)
        run_suite(inbox.NativeInboxTests, 15, "inbox", methods)
        STAGE = "safety2"
        safety = safety_case(inbox)
        run_suite(safety, 2, "safety", {name for name in safety.__dict__ if name.startswith("test_")})
        STAGE = "legacy10"
        nonce = os.environ["ILDR_BROWSER_CI_NONCE"]
        legacy_db = "ildr_platform_test_20261025"
        require(catalog.query(legacy_db, "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()")
            ==[("ildr-inbox-legacy-ci:"+nonce,)], "owned_legacy_database_marker_required")
        os.environ["SEARCH_FILTER_TEST_DATABASE_URL"] = "postgresql+psycopg2://postgres@127.0.0.1:55449/"+legacy_db
        import test_service_conversation as award
        import test_service_negotiation as negotiation
        for filename, classname, count, module, label in (
            ("test_service_conversation.py", "PrivateAwardedServiceConversation", 5, award, "legacy_award"),
            ("test_service_negotiation.py", "PrivateServiceNegotiationConversation", 5, negotiation, "legacy_negotiation")):
            methods = suite_inventory(application, filename, classname, count)
            run_suite(getattr(module, classname), count, label, methods)
        STAGE = "preservation"
        require(catalog.table_names(database)==tuple(sorted(legacy+INBOX)) and catalog.query(database,
            "SELECT version_num FROM alembic_version")==[(HEAD,)], "all106_tables_and_head_retained_required")
        old_preserved(catalog, database, legacy, before, schema)
        require(all(catalog.query(database, "SELECT count(*) FROM public."+name)==[(0,)] for name in INBOX),
            "canonical_private_inbox_rows_unmodified_required")
        require(catalog.query(database, "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'native_inbox_test_%' OR nspname LIKE 'inbox_model_%'")==[],
            "owned_unit_schemas_removed_required")
        privacy(catalog, database)
        require(not ATTEMPTS and len(captured.getvalue().encode())<=2*1024*1024, "zero_provider_bounded_capture_required")
    print("INBOX_NATIVE_RESULT "+canonical(EXPECTED), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        name = type(error).__name__
        print("INBOX_NATIVE_FAILURE "+canonical({"stage": STAGE,
            "error_type": name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name) else "UnhandledError",
            "reason": error.args[0] if isinstance(error, Failure) else "external_failure"}), flush=True)
        raise SystemExit(1) from None
