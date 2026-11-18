"""Hosted-only fresh/forward/rollback/archive proof for the isolated groups slice."""
import ast
from contextlib import closing, redirect_stdout, redirect_stderr
from hashlib import sha1, sha256
from io import StringIO
import json
import logging
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import unittest

sys.path.insert(0, "/tools")
from catalog import (OLD_TABLES, LEGACY_NEW_TABLES, RehearsalFailure, require, connection, query, table_names,
                     catalog, snapshot, verify_check_render_controls)

APP = Path("/application")
PREVIOUS = Path("/previous")
ROLLBACK = Path("/rollback")
APP_COMMIT = "4e23cc8b643a4b07f4e14042e530e6271dad9c8f"
APP_TREE = "8093ea7aa9fd8f87a842e2527c715a8c482ac1fc"
OLD_COMMIT = "4d21cb3ae2b14475828ff8fbe710b22c34c3a53b"
OLD_TREE = "1e3f72978515a58733188caa235cb6ee61190a26"
HEAD = "c4e2a7319b60"
OLD_HEAD = "b19d8e4c720a"
MIGRATION = "alembic/versions/c4e2a7319b60_private_social_groups.py"
GROUP_TABLES = tuple(sorted(("network_groups", "network_group_memberships", "network_group_invite_links",
    "network_group_posts", "network_group_comments", "network_group_media", "network_group_actions")))
ALL_TABLES = tuple(sorted(OLD_TABLES + GROUP_TABLES))
GROUP_PATHS = sorted((MIGRATION, "main.py", "models/__init__.py", "models/social_groups.py", "routes/social_groups.py",
    "schemas/social_groups.py", "services/social_groups.py", "tests/test_social_groups.py"))
PROVIDER_KEYS = ("RESEND_API_KEY", "GHL_API_KEY", "GHL_LOCATION_ID", "GHL_WEBHOOK_SECRET", "SUPABASE_SERVICE_KEY",
    "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_WEBHOOK_ID")
PHASE = "guard"
NONCE = os.environ.get("ILDR_GROUPS_CI_NONCE", "")
ATTEMPTS = []


def git(root, *args):
    return subprocess.check_output(["git", "--no-optional-locks", "-c", f"safe.directory={root}", "-C", str(root), *args],
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"},
        stderr=subprocess.DEVNULL, timeout=20)


def sources():
    import importlib.metadata as metadata
    provenance = json.loads(Path("/tools/accepted-catalog-provenance.json").read_text())
    require(sha256(Path("/tools/catalog.py").read_bytes()).hexdigest() == provenance["copied_helper_sha256"],
            "accepted_catalog_bytes_required")
    baseline = json.loads(Path("/opt/directory-map/baseline-inventory.json").read_text())
    actual = {re.sub(r"[-_.]+", "-", distribution.metadata["Name"].lower()): distribution.version for distribution in metadata.distributions()}
    require(actual == {**baseline, "openpyxl": "3.1.5", "defusedxml": "0.7.1", "et-xmlfile": "2.0.0"},
            "unchanged_dependency_inventory_required")
    for root, commit, tree in ((APP, APP_COMMIT, APP_TREE), (PREVIOUS, OLD_COMMIT, OLD_TREE)):
        require(root.resolve(strict=True) == root and os.statvfs(root).f_flag & os.ST_RDONLY, "readonly_source_required")
        require(not (root / ".env").exists() and not (root / ".env").is_symlink(), "credential_file_refused")
        require(git(root, "rev-parse", "HEAD", "HEAD^{tree}").decode().splitlines() == [commit, tree]
            and not git(root, "status", "--porcelain=v1", "--untracked-files=all").strip(), "exact_clean_source_required")
    require(git(APP, "diff", "--name-only", OLD_COMMIT, APP_COMMIT).decode().splitlines() == GROUP_PATHS,
            "exact_eight_group_paths_required")
    require(git(APP, "diff", "--name-only", OLD_COMMIT, APP_COMMIT, "--", "alembic").decode().splitlines() == [MIGRATION],
            "one_additive_migration_required")
    require(ROLLBACK.resolve(strict=True) == ROLLBACK and os.statvfs(ROLLBACK).f_flag & os.ST_RDONLY, "readonly_rollback_required")
    names = set()
    for entry in git(PREVIOUS, "ls-tree", "-rz", OLD_COMMIT).split(b"\0")[:-1]:
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        name = raw_name.decode()
        path = ROLLBACK / name
        require(mode in (b"100644", b"100755") and kind == b"blob" and path.is_file() and not path.is_symlink(), "old_blob_required")
        data = path.read_bytes()
        require(sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest().encode() == object_id, "old_blob_required")
        names.add(name)
    require((ROLLBACK / MIGRATION).read_bytes() == git(APP, "cat-file", "blob", APP_COMMIT + ":" + MIGRATION), "exact_new_revision_required")
    names.add(MIGRATION)
    require({p.relative_to(ROLLBACK).as_posix() for p in ROLLBACK.rglob("*") if p.is_file()} == names
        and all(not p.is_symlink() for p in ROLLBACK.rglob("*")), "rollback_only_new_revision_required")
    tree = ast.parse((APP / "tests/test_social_groups.py").read_text())
    inventory = {node.name: len([item for item in node.body if isinstance(item, ast.FunctionDef) and item.name.startswith("test_")])
        for node in tree.body if isinstance(node, ast.ClassDef)}
    require(inventory == {"GroupInputTest": 4, "SocialGroupsTest": 20}, "actual_24_test_inventory_required")


def database(purpose):
    require(purpose in {"fresh", "forward", "restore"}, "owned_purpose_required")
    return f"ildr_groups_{purpose}_{NONCE}"


def identity(name, purpose):
    require(name == database(purpose), "owned_database_required")
    require(query(name, "SELECT current_database(),current_setting('server_version_num')::int,host(inet_server_addr()),inet_server_port(),shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()") ==
        [(name, 170006, "127.0.0.1", 55449, f"ildr-social-groups-ci:{NONCE}:{purpose}")], "exact_owned_postgres_required")


def create(purpose):
    from psycopg2 import sql
    name = database(purpose)
    with closing(connection("postgres")) as db:
        db.autocommit = True
        with db.cursor() as cursor:
            require(query("postgres", "SELECT current_setting('server_version_num')::int,host(inet_server_addr()),inet_server_port()") == [(170006, "127.0.0.1", 55449)], "exact_postgres_required")
            cursor.execute("SELECT count(*) FROM pg_database WHERE datname=%s", (name,))
            require(cursor.fetchone() == (0,), "fresh_database_required")
            cursor.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
            cursor.execute(sql.SQL("COMMENT ON DATABASE {} IS %s").format(sql.Identifier(name)), (f"ildr-social-groups-ci:{NONCE}:{purpose}",))
    with closing(connection(name)) as db, db.cursor() as cursor:
        cursor.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon,authenticated,service_role")
        db.commit()
    identity(name, purpose)
    return name


def environment(name):
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1",
        "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "ilovedrrealty/realty-api", "ILDR_GROUPS_CI_NONCE": NONCE,
        "DATABASE_URL": f"postgresql+psycopg2://postgres@127.0.0.1:55449/{name}", "JWT_SECRET": secrets.token_urlsafe(48),
        "ENVIRONMENT": "development", "COOKIE_DOMAIN": "", "ALLOWED_ORIGINS": "http://localhost:5174",
        "LANDING_URL": "http://127.0.0.1:8917", "EMAIL_FROM": "rehearsal@example.com", "NOTIFY_EMAIL": "",
        "GHL_ENABLED": "false", "LEAD_DELIVERY_ENABLED": "false", "CUSTOMER_MESSAGES_ENABLED": "false",
        "PROPERTY_INQUIRY_REQUIRE_CONSENT": "false", "SUPABASE_URL": "", **{key: "" for key in PROVIDER_KEYS}}


def native(root, name, *args, expected=0):
    if args == ("bootstrap_db.py",):
        command = ["/usr/local/bin/python", "-I", "-B", "-c",
            "import sys,runpy;sys.path.insert(0," + repr(str(root)) + ");runpy.run_path('bootstrap_db.py',run_name='__main__')"]
    else:
        command = ["/usr/local/bin/python", "-I", "-B", *args]
    result = subprocess.run(command, cwd=root, env=environment(name), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=150)
    require(len(result.stdout) <= 2 * 1024 * 1024, "bounded_private_process_output_required")
    require((result.returncode == 0) if expected == 0 else (result.returncode != 0), "native_process_exit_required")
    return result.stdout


def startup(root, name):
    native(root, name, "bootstrap_db.py")
    native(root, name, "-m", "alembic", "upgrade", "head")


def records(root, name, mode):
    raw = native(root, name, "/tools/records.py", mode)
    rows = [json.loads(line[len(b"GROUPS_RECORDS_RESULT "):]) for line in raw.splitlines() if line.startswith(b"GROUPS_RECORDS_RESULT ")]
    require(len(rows) == 1 and rows[0]["mode"] == mode and rows[0]["external_provider_attempts"] == 0, "record_proof_required")
    return rows[0]


def head(name, revision):
    require(query(name, "SELECT version_num FROM alembic_version") == [(revision,)], "exact_migration_head_required")


def private_groups(name):
    current = catalog(name, GROUP_TABLES)
    require(current["rls"] == [(t, True, False) for t in GROUP_TABLES] and current["policies"] == [], "seven_private_rls_tables_required")
    require(not any(row[1] in {"PUBLIC", "anon", "authenticated", "service_role"} for row in current["acl"]), "group_api_grants_refused")
    rows = query(name, """SELECT p.prosecdef,coalesce(r.rolname,'PUBLIC'),a.privilege_type
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
        LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE n.nspname='public' AND p.proname='network_group_guard'""")
    require(rows and all(not row[0] and row[1] not in {"PUBLIC", "anon", "authenticated", "service_role"} for row in rows),
            "group_guard_server_only_invoker_required")


def old_preserved(name, old_catalog, old_rows):
    current = catalog(name, OLD_TABLES)
    new_functions = [row for row in current["functions"] if row not in old_catalog["functions"]]
    require(len(new_functions) == 1 and new_functions[0][:2] == ("network_group_guard", ""), "only_one_new_group_function_required")
    current["functions"] = [row for row in current["functions"] if row not in new_functions]
    require(current == old_catalog, "all_old_catalog_acl_unchanged_required")
    require(snapshot(name, OLD_TABLES) == old_rows, "all_old_rows_sequences_unchanged_required")


def tests(name):
    os.environ.update(environment(name))
    os.environ["SOCIAL_GROUPS_FIXTURE"] = "yes"
    sys.path.insert(0, str(APP))
    sys.path.insert(0, str(APP / "tests"))
    logging.disable(logging.CRITICAL)
    import requests, httpx, resend, smtplib, urllib.request
    def refuse(*_args, **_kwargs):
        ATTEMPTS.append(1)
        raise RuntimeError("external_transport_refused")
    requests.sessions.Session.request = refuse
    httpx.HTTPTransport.handle_request = refuse
    httpx.AsyncHTTPTransport.handle_async_request = refuse
    resend.Emails.send = refuse
    smtplib.SMTP = smtplib.SMTP_SSL = refuse
    urllib.request.urlopen = refuse
    from database import SessionLocal, engine
    require(str(engine.url) == os.environ["DATABASE_URL"] and SessionLocal.kw["bind"] is engine, "native_test_engine_required")
    import test_social_groups as module
    expected = (("GroupInputTest", 4), ("SocialGroupsTest", 20))
    diagnostics, total, successful = [], 0, True
    class Result(unittest.TestResult):
        def addError(self, test, error):
            super().addError(test, error)
            diagnostics.append((test.__class__.__name__, getattr(test, "_testMethodName", "setup_or_cleanup"), error[0].__name__))
        def addFailure(self, test, error):
            super().addFailure(test, error)
            diagnostics.append((test.__class__.__name__, getattr(test, "_testMethodName", "setup_or_cleanup"), error[0].__name__))
        def addSubTest(self, test, subtest, error):
            super().addSubTest(test, subtest, error)
            if error is not None:
                diagnostics.append((test.__class__.__name__, getattr(test, "_testMethodName", "setup_or_cleanup"), error[0].__name__))
    for classname, count in expected:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(getattr(module, classname))
        require(suite.countTestCases() == count, "exact_native_test_count_required")
        result = Result()
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            suite.run(result)
        total += result.testsRun
        successful = successful and result.wasSuccessful()
        require(not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses, "no_skipped_or_expected_failures_required")
    require(not ATTEMPTS, "no_external_attempts_required")
    for classname, method, error in diagnostics:
        require(classname in {item[0] for item in expected} and re.fullmatch(r"test_[a-z_]+|setup_or_cleanup", method)
            and re.fullmatch(r"[A-Za-z_]{1,80}", error), "safe_test_diagnostic_required")
        print("GROUPS_NATIVE_TEST_DIAGNOSTIC " + json.dumps({"class": classname, "test": method, "error_type": error}))
    require(total == 24 and successful and not diagnostics, "all_24_native_contracts_required")
    return total


def run():
    global PHASE
    PHASE = "fresh"
    fresh = create("fresh")
    startup(APP, fresh)
    head(fresh, HEAD)
    require(table_names(fresh) == ALL_TABLES, "fresh_99_required")
    private_groups(fresh)
    fresh_catalog = catalog(fresh, ALL_TABLES)
    native(APP, fresh, "-m", "alembic", "downgrade", OLD_HEAD)
    head(fresh, OLD_HEAD)
    require(table_names(fresh) == OLD_TABLES, "empty_only_downgrade_required")
    native(APP, fresh, "-m", "alembic", "upgrade", "head")
    require(catalog(fresh, ALL_TABLES) == fresh_catalog, "empty_reupgrade_catalog_required")
    PHASE = "contracts"
    count = tests(fresh)
    PHASE = "previous_seed"
    forward = create("forward")
    startup(PREVIOUS, forward)
    head(forward, OLD_HEAD)
    require(table_names(forward) == OLD_TABLES, "previous_92_required")
    retained = records(PREVIOUS, forward, "seed_old")
    require(retained == {"mode": "seed_old", "external_provider_attempts": 0, "users": 4, "leads": 3, "owner_contacts": 82,
        "contacts": 85, "sources": 86, "hidden_profiles": 46, "accepted_jobs": 9, "worker_rows": 1,
        "cms_version": 7, "floorplan_rows": 2}, "retained_fixture_shape_required")
    require(query(forward, """SELECT
        (SELECT count(*) FROM trade_contacts WHERE details->>'verification_status'='owner_source'),
        (SELECT count(*) FROM trade_contact_sources),
        (SELECT count(*) FROM professional_profiles WHERE user_id IS NULL AND published_data IS NULL AND listing_status='hidden' AND review_status='draft'),
        (SELECT count(*) FROM lead_delivery_jobs WHERE status='accepted' AND attempt_count=1),
        (SELECT count(*) FROM lead_delivery_worker_state),
        (SELECT version FROM website_settings WHERE id=1),
        (SELECT count(*) FROM listing_floor_plans WHERE octet_length(payload)=byte_count)
        """) == [(82, 86, 46, 9, 1, 7, 2)], "actual_retained_private_and_outbox_shape_required")
    before_catalog, before_rows = catalog(forward, OLD_TABLES), snapshot(forward, OLD_TABLES)
    PHASE = "forward"
    startup(APP, forward)
    head(forward, HEAD)
    require(table_names(forward) == ALL_TABLES and catalog(forward, ALL_TABLES) == fresh_catalog, "fresh_forward_99_full_catalog_required")
    private_groups(forward)
    old_preserved(forward, before_catalog, before_rows)
    PHASE = "group_seed"
    groups = records(APP, forward, "seed_groups")
    require(groups == {"mode": "seed_groups", "external_provider_attempts": 0, "groups": 1, "memberships": 1,
        "posts": 1, "comments": 1, "media": 1, "links": 1, "actions": 7, "synthetic_storage_writes": 1}, "group_seed_shape_required")
    expected_counts = {"network_groups": 1, "network_group_memberships": 1, "network_group_invite_links": 1,
        "network_group_posts": 1, "network_group_comments": 1, "network_group_media": 1, "network_group_actions": 7}
    require(all(query(forward, 'SELECT count(*) FROM public.' + table) == [(value,)] for table, value in expected_counts.items()),
            "actual_populated_groups_required")
    old_preserved(forward, before_catalog, before_rows)
    all_rows, all_catalog = snapshot(forward, ALL_TABLES), catalog(forward, ALL_TABLES)
    PHASE = "compatible_previous_runtime"
    startup(ROLLBACK, forward)
    reads = records(ROLLBACK, forward, "verify_old")
    require(reads["actual_old_reads"] == 9 and reads["new_group_route_absent"] is True, "actual_old_read_contracts_required")
    head(forward, HEAD)
    require(snapshot(forward, ALL_TABLES) == all_rows and catalog(forward, ALL_TABLES) == all_catalog, "old_runtime_retains_all_99_required")
    PHASE = "populated_downgrade_refusal"
    native(APP, forward, "-m", "alembic", "downgrade", OLD_HEAD, expected=1)
    head(forward, HEAD)
    require(snapshot(forward, ALL_TABLES) == all_rows and catalog(forward, ALL_TABLES) == all_catalog, "refused_downgrade_is_atomic_required")
    return {"status": "PASS", "mode": "run", "tests": count, "fresh_tables": 99, "forward_from_tables": 92,
        "forward_tables": 99, "old_tables_preserved": 92, "new_private_tables": 7, "accepted_jobs_retained": 9,
        "hidden_profiles_retained": 46, "owner_contacts_retained": 82, "cms_version_retained": 7,
        "floorplan_rows_retained": 2, "actual_old_reads": 9, "populated_downgrade_refused": True,
        "external_provider_attempts": 0, "fresh_forward_catalog_sha256": sha256(json.dumps(fresh_catalog, sort_keys=True).encode()).hexdigest()}


def restore():
    global PHASE
    PHASE = "restore"
    source, target = database("forward"), database("restore")
    identity(source, "forward")
    identity(target, "restore")
    head(source, HEAD)
    head(target, HEAD)
    require(table_names(source) == table_names(target) == ALL_TABLES, "archive_99_tables_required")
    original, recovered = catalog(source, ALL_TABLES), catalog(target, ALL_TABLES)
    require({k: v for k, v in original.items() if k != "constraints"} == {k: v for k, v in recovered.items() if k != "constraints"}, "archive_eight_raw_components_required")
    left, right = original["constraints"], recovered["constraints"]
    require(len(left) == len(right) == 529 and [r[:3] for r in left] == [r[:3] for r in right], "all_529_constraint_identities_required")
    differences = [(a, b) for a, b in zip(left, right) if a != b]
    old_differences = [(a, b) for a, b in differences if a[0] in OLD_TABLES]
    new_differences = [(a, b) for a, b in differences if a[0] in GROUP_TABLES]
    require(len(old_differences) == 13 and all(a[0] in LEGACY_NEW_TABLES and a[2] == b[2] == "c" for a, b in old_differences), "retained_13_old_check_renderings_required")
    allowed = {"ck_group_bounds", "ck_group_link_bounds", "ck_group_comment_bounds", "ck_group_media_bounds"}
    require(len(new_differences) <= 4 and all(a[2] == b[2] == "c" and a[1] in allowed for a, b in new_differences), "only_reviewed_new_check_renderings_required")
    statement = """SELECT c.relname,k.conname,k.contype,pg_get_constraintdef(k.oid,true)
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_constraint k ON k.conrelid=c.oid
        WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY c.relname,k.conname"""
    require(query(source, statement, (list(ALL_TABLES),)) == query(target, statement, (list(ALL_TABLES),)), "all_server_pretty_constraints_required")
    verify_check_render_controls(target)
    require(snapshot(source, ALL_TABLES) == snapshot(target, ALL_TABLES), "archive_all_rows_sequences_and_bytea_required")
    private_groups(target)
    require(query(target, "SELECT count(*) FROM listing_floor_plans WHERE octet_length(payload)=byte_count AND encode(sha256(payload),'hex')=sha256") == [(2,)], "two_exact_typed_floorplan_bytes_required")
    return {"status": "PASS", "mode": "restore", "tables": 99, "constraints": 529, "old_raw_check_variations": 13,
        "new_raw_check_variations": len(new_differences), "raw_exact_constraints": 529 - len(differences),
        "server_pretty_constraints_equal": 529, "native_deparser_controls": 3, "floorplan_rows": 2,
        "all_rows_sequences_equal": True, "eight_raw_catalog_components_equal": True, "external_provider_attempts": 0}


if __name__ == "__main__":
    try:
        require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true" and
            os.environ.get("GITHUB_REPOSITORY") == "ilovedrrealty/realty-api" and re.fullmatch(r"[a-f0-9]{32}", NONCE), "exact_hosted_context_required")
        require({p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}, "loopback_only_required")
        require(all(not os.environ.get(k) for k in PROVIDER_KEYS), "credentials_refused")
        sources()
        require(sys.argv[1:] in [["run"], ["restore"]], "exact_mode_required")
        report = run() if sys.argv[1] == "run" else restore()
        print("GROUPS_NATIVE_RESULT " + json.dumps(report, sort_keys=True))
    except BaseException as error:
        reason = str(error) if type(error) is RehearsalFailure else "unexpected_exception"
        require(re.fullmatch(r"[a-z_]{1,100}", reason), "safe_failure_reason_required")
        print("GROUPS_NATIVE_RESULT " + json.dumps({"status": "FAIL", "phase": PHASE,
            "reason": reason, "error_type": type(error).__name__ if re.fullmatch(r"[A-Za-z_]{1,80}", type(error).__name__) else "Unexpected"}, sort_keys=True))
        raise SystemExit(1)
