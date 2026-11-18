"""Compare fresh/live-upgrade catalogs and exercise a compatible old application.

Hosted-only, marked disposable PostgreSQL; no production connection or provider
credentials are accepted. Contact-bearing snapshots stay in process memory.
"""

from contextlib import closing
from hashlib import sha1, sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


APPLICATION = Path("/application")
LIVE = Path("/live")
ROLLBACK = Path("/rollback")
APP_COMMIT = "c3c4ac8d0371941b0bfa2d086ade8752c42585c9"
APP_TREE = "88732c8b457f6e6e30ba696fa998a74cafb08316"
LIVE_COMMIT = "7f63fec927f75af3f901f74297973253ad5c743b"
LIVE_TREE = "1a87c3d4c96e2df0c956e18f0f26c982e7ef3289"
HEAD = "6c02d4a1b9e8"
MIGRATION = "alembic/versions/6c02d4a1b9e8_private_newsletter_subscriptions.py"
NEW_TABLES = ("newsletter_subscriptions",)
EXPECTED_TABLES = (
    "activity_log", "alembic_version", "anonymous_leads", "bookings", "bulk_import_jobs",
    "deal_requests", "inquiries", "lead_delivery_jobs", "lead_delivery_worker_state", "leads",
    "listing_edits", "listing_events", "listings", "newsletter_subscriptions", "pending_users", "professional_profile_revisions",
    "professional_profiles", "research_workspaces", "saved_homes", "settings", "trade_contact_sources",
    "trade_contacts", "upgrade_requests", "users", "website_settings", "website_settings_revisions",
)
ROLLBACK_RESULTS = {
    "seed_live": {"status": "PASS", "stage": "seed_live", "historical_users": 4,
                  "historical_leads": 2, "historical_inquiries": 1, "historical_bookings": 1,
                  "configured_team_recipients": 4},
    "seed_candidate": {"status": "PASS", "stage": "seed_candidate", "phone_only_inquiries": 1,
                       "email_only_inquiries": 1, "consented_property_inquiries": 1,
                       "verified_signups": 1, "linked_bookings": 1, "delivery_jobs": 39,
                       "customer_email_jobs": 3},
    "verify_old": {"status": "PASS", "stage": "verify_old", "role_read_checks": 13,
                   "old_client_intakes": 1, "fake_owner_notices": 1, "head_preserved": True,
                   "delivery_jobs_unchanged": True, "no_historical_replay": True},
}
stage = "isolation"


class RehearsalFailure(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise RehearsalFailure(reason)


def advance(name):
    global stage
    stage = name


def git(root, *arguments):
    return subprocess.check_output(
        ["git", "--no-optional-locks", "-c", f"safe.directory={root}", "-C", str(root), *arguments],
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
             "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"},
        stderr=subprocess.DEVNULL, timeout=15,
    )


def verify_source(root, commit, tree):
    require(root.resolve(strict=True) == root and os.statvfs(root).f_flag & os.ST_RDONLY,
            "read_only_source_required")
    require(git(root, "rev-parse", "HEAD", "HEAD^{tree}").decode().splitlines() == [commit, tree]
            and not git(root, "status", "--porcelain=v1", "--untracked-files=all")
            and not (root / ".env").exists() and not (root / ".env").is_symlink(),
            "exact_clean_source_required")


def verify_rollback_archive():
    require(ROLLBACK.resolve(strict=True) == ROLLBACK and os.statvfs(ROLLBACK).f_flag & os.ST_RDONLY,
            "read_only_rollback_required")
    names = set()
    for entry in git(LIVE, "ls-tree", "-rz", "HEAD").split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        name = raw_name.decode("utf-8")
        require(mode in (b"100644", b"100755") and kind == b"blob", "rollback_plain_files_required")
        path = ROLLBACK / name
        require(path.is_file() and not path.is_symlink(), "rollback_source_file_required")
        data = path.read_bytes()
        actual = sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest().encode()
        require(actual == object_id, "rollback_live_blob_mismatch")
        names.add(name)
    require((ROLLBACK / MIGRATION).read_bytes() == (APPLICATION / MIGRATION).read_bytes(),
            "rollback_current_revision_required")
    names.add(MIGRATION)
    actual = set()
    for path in ROLLBACK.rglob("*"):
        require(not path.is_symlink(), "rollback_symlink_refused")
        if path.is_file():
            actual.add(path.relative_to(ROLLBACK).as_posix())
    require(actual == names and not (ROLLBACK / ".env").exists(), "rollback_extra_file_refused")


def environment(database):
    values = {
        "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1",
        "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "ilovedrrealty/realty-api",
        "ILDR_BROWSER_CI_NONCE": os.environ["ILDR_BROWSER_CI_NONCE"],
        "DATABASE_URL": f"postgresql+psycopg2://postgres@127.0.0.1:55449/{database}",
        "JWT_SECRET": "synthetic-live-communications-only", "ENVIRONMENT": "development",
        "COOKIE_DOMAIN": "", "ALLOWED_ORIGINS": "http://127.0.0.1:8917,http://127.0.0.1:4175",
        "LANDING_URL": "http://127.0.0.1:8917", "RESEND_API_KEY": "", "EMAIL_FROM": "rehearsal@example.com",
        "NOTIFY_EMAIL": "", "GHL_ENABLED": "false", "GHL_API_KEY": "", "GHL_LOCATION_ID": "",
        "GHL_WEBHOOK_SECRET": "", "LEAD_DELIVERY_ENABLED": "false", "CUSTOMER_MESSAGES_ENABLED": "false",
        "PROPERTY_INQUIRY_REQUIRE_CONSENT": "false", "SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": "",
        "NEWSLETTER_FIXTURE": "yes",
        "PAYPAL_CLIENT_ID": "", "PAYPAL_CLIENT_SECRET": "", "PAYPAL_WEBHOOK_ID": "",
    }
    return values


def native(root, database, *arguments, allow_failure=False):
    # Isolated Python intentionally omits the script directory from sys.path.
    # Restore only this already-verified, read-only application root for the
    # bootstrap entrypoint's local imports. Alembic uses prepend_sys_path=.
    if arguments == ("bootstrap_db.py",):
        arguments = (
            "-c",
            "import runpy,sys; root=sys.argv[1]; sys.path.insert(0,root); "
            "runpy.run_path(root+'/bootstrap_db.py',run_name='__main__')",
            str(root),
        )
    result = subprocess.run(
        [sys.executable, "-I", "-B", *arguments], cwd=root, env=environment(database),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=150,
    )
    require(len(result.stdout) <= 4 * 1024 * 1024, "bounded_native_output_required")
    require(allow_failure or result.returncode == 0, "native_phase_failed")
    return result


def rollback_mode(database, mode):
    roots = {"seed_live": LIVE, "seed_candidate": APPLICATION, "verify_old": ROLLBACK}
    process = native(roots[mode], database, "/tools/verify-live-delivery-rollback.py", mode, allow_failure=True)
    prefix = b"LIVE_DELIVERY_ROLLBACK_STAGE "
    rows = [json.loads(line[len(prefix):]) for line in process.stdout.splitlines() if line.startswith(prefix)]
    require(len(rows) == 1 and isinstance(rows[0], dict) and rows[0].get("stage") == mode,
            "exact_rollback_stage_required")
    row = rows[0]
    if row.get("status") == "FAIL":
        reason = row.get("reason", "failed")
        require(isinstance(reason, str) and re.fullmatch(r"[a-z_]{1,50}", reason), "safe_rollback_failure_required")
        raise RehearsalFailure("rollback_" + mode + "_" + reason)
    require(row.get("status") == "PASS" and process.returncode == 0,
            "successful_rollback_stage_required")
    require(all(re.fullmatch(r"[a-z_]{1,50}", key) for key in row), "safe_rollback_fields_required")
    require(all(type(value) in (bool, int) and 0 <= value <= 1000
                for key, value in row.items() if key not in {"status", "stage"}),
            "aggregate_rollback_values_required")
    require(row == ROLLBACK_RESULTS[mode], "exact_rollback_aggregate_required")
    return rows[0]


def connection(database):
    import psycopg2

    return psycopg2.connect(host="127.0.0.1", port=55449, dbname=database, user="postgres", password="",
                            connect_timeout=4, options="-cstatement_timeout=10000")


def create_database(name, nonce, purpose):
    from psycopg2 import sql

    with closing(connection("postgres")) as admin:
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute("SELECT current_setting('server_version_num')::int, host(inet_server_addr()), inet_server_port()")
            require(cursor.fetchone() == (170006, "127.0.0.1", 55449), "exact_loopback_postgres_required")
            cursor.execute("SELECT count(*) FROM pg_database WHERE datname=%s", (name,))
            require(cursor.fetchone() == (0,), "fresh_owned_database_required")
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            cursor.execute(sql.SQL("COMMENT ON DATABASE {} IS %s").format(sql.Identifier(name)),
                           (f"ildr-live-communications-ci:{nonce}:{purpose}",))
    with closing(connection(name)) as db, db.cursor() as cursor:
        cursor.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated, service_role")
        db.commit()


def query(database, statement, params=()):
    with closing(connection(database)) as db, db.cursor() as cursor:
        cursor.execute(statement, params)
        return cursor.fetchall()


def table_names(database):
    return tuple(row[0] for row in query(database, """
        SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename
    """))


def catalog(database, tables):
    require(tables and all(re.fullmatch(r"[a-z_]+", name) for name in tables), "catalog_table_allowlist_required")
    selected = list(tables)
    definitions = {
        "columns": """SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
            pg_get_expr(d.adbin,d.adrelid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY c.relname,a.attnum""",
        "constraints": """SELECT c.relname,k.conname,k.contype,pg_get_constraintdef(k.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_constraint k ON k.conrelid=c.oid
            WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY c.relname,k.conname""",
        "indexes": """SELECT t.relname,i.relname,x.indisprimary,x.indisunique,pg_get_indexdef(i.oid)
            FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace JOIN pg_index x ON x.indrelid=t.oid
            JOIN pg_class i ON i.oid=x.indexrelid WHERE n.nspname='public' AND t.relname=ANY(%s)
            ORDER BY t.relname,i.relname""",
        "rls": """SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY c.relname""",
        "acl": """SELECT c.relname,coalesce(r.rolname,'PUBLIC'),a.privilege_type,a.is_grantable
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
            LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE n.nspname='public' AND c.relname=ANY(%s)
            ORDER BY c.relname,coalesce(r.rolname,'PUBLIC'),a.privilege_type""",
        "policies": """SELECT c.relname,p.polname,pg_get_expr(p.polqual,p.polrelid),pg_get_expr(p.polwithcheck,p.polrelid)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_policy p ON p.polrelid=c.oid
            WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY c.relname,p.polname""",
    }
    return {key: query(database, sql, (selected,)) for key, sql in definitions.items()}


def snapshot(database, tables):
    from psycopg2 import sql

    result = {}
    with closing(connection(database)) as db, db.cursor() as cursor:
        for table in tables:
            if table == "alembic_version":
                continue
            cursor.execute(sql.SQL("SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb)::text FROM public.{} t")
                           .format(sql.Identifier(table)))
            result[table] = cursor.fetchone()[0]
        cursor.execute("SELECT sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")
        result["sequences"] = cursor.fetchall()
    return result


def repeat_migration(database):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    spec = importlib.util.spec_from_file_location("reviewed_live_delivery_migration", APPLICATION / MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    require(migration.revision == HEAD and migration.down_revision == "d721f0b6a84c", "migration_ancestry_required")
    engine = create_engine(environment(database)["DATABASE_URL"])
    try:
        with engine.begin() as db:
            migration.op = Operations(MigrationContext.configure(db))
            migration.upgrade()
            migration.upgrade()
    finally:
        engine.dispose()


def newsletter_contracts(database):
    # Reuse six accepted contracts plus the in-flight unsubscribe regression.
    # TestResult keeps synthetic payloads/errors private; emit aggregates only.
    code = """
import json, sys, unittest
sys.path.insert(0, '/application')
suite = unittest.defaultTestLoader.discover('/application/tests', pattern='test_newsletter.py')
assert suite.countTestCases() == 7
result = unittest.TestResult()
suite.run(result)
print('NEWSLETTER_NATIVE_CONTRACTS_RESULT ' + json.dumps({
    'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
    'skipped': len(result.skipped), 'expected_failures': len(result.expectedFailures),
    'unexpected_successes': len(result.unexpectedSuccesses),
}, sort_keys=True))
sys.exit(0 if result.wasSuccessful() and not result.skipped else 1)
"""
    process = native(APPLICATION, database, "-c", code, allow_failure=True)
    prefix = b"NEWSLETTER_NATIVE_CONTRACTS_RESULT "
    rows = [json.loads(line[len(prefix):]) for line in process.stdout.splitlines() if line.startswith(prefix)]
    require(rows == [{"tests": 7, "failures": 0, "errors": 0, "skipped": 0,
                      "expected_failures": 0, "unexpected_successes": 0}] and process.returncode == 0,
            "seven_native_newsletter_contracts_required")
    return 7


def verify_private(value):
    require(value["rls"] == [(name, True, False) for name in NEW_TABLES], "private_rls_required")
    require(not value["policies"] and not any(row[1] in {"PUBLIC", "anon", "authenticated"} for row in value["acl"]),
            "private_data_api_grants_required")


def run():
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_REPOSITORY") == "ilovedrrealty/realty-api"
            and {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"}, "hosted_loopback_required")
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    require(re.fullmatch(r"[a-f0-9]{32}", nonce), "owned_nonce_required")
    verify_source(APPLICATION, APP_COMMIT, APP_TREE)
    verify_source(LIVE, LIVE_COMMIT, LIVE_TREE)
    verify_rollback_archive()
    fresh = f"ildr_f01_fresh_{nonce}"
    upgrade = f"ildr_f01_upgrade_{nonce}"
    advance("fresh_database")
    create_database(fresh, nonce, "fresh")
    create_database(upgrade, nonce, "upgrade")
    advance("fresh_bootstrap")
    native(APPLICATION, fresh, "bootstrap_db.py")
    native(APPLICATION, fresh, "-m", "alembic", "upgrade", "head")
    require(table_names(fresh) == EXPECTED_TABLES, "exact_26_tables_required")
    require(query(fresh, "SELECT version_num FROM alembic_version") == [(HEAD,)], "fresh_head_required")
    require(query(fresh, "SELECT (SELECT count(*) FROM lead_delivery_jobs),(SELECT count(*) FROM lead_delivery_worker_state)") == [(0, 0)],
            "fresh_delivery_tables_empty_required")
    require(query(fresh, "SELECT count(*) FROM newsletter_subscriptions") == [(0,)], "fresh_newsletter_empty_required")
    fresh_catalog = catalog(fresh, NEW_TABLES)
    fresh_full_catalog = catalog(fresh, EXPECTED_TABLES)
    verify_private(fresh_catalog)
    advance("fresh_newsletter_contracts")
    fresh_contracts = newsletter_contracts(fresh)
    advance("live_bootstrap")
    native(LIVE, upgrade, "bootstrap_db.py")
    native(LIVE, upgrade, "-m", "alembic", "upgrade", "head")
    require(query(upgrade, "SELECT version_num FROM alembic_version") == [("d721f0b6a84c",)], "exact_live_head_required")
    old_tables = tuple(name for name in EXPECTED_TABLES if name not in NEW_TABLES)
    require(table_names(upgrade) == old_tables, "exact_live_table_inventory_required")
    baseline = rollback_mode(upgrade, "seed_live")
    old_rows = snapshot(upgrade, old_tables)
    old_catalog = catalog(upgrade, old_tables)
    advance("live_upgrade")
    native(APPLICATION, upgrade, "-m", "alembic", "upgrade", "head")
    require(table_names(upgrade) == EXPECTED_TABLES and query(upgrade, "SELECT version_num FROM alembic_version") == [(HEAD,)],
            "upgraded_head_and_inventory_required")
    require(snapshot(upgrade, old_tables) == old_rows and catalog(upgrade, old_tables) == old_catalog,
            "historical_rows_sequences_and_catalog_unchanged_required")
    require(query(upgrade, "SELECT (SELECT count(*) FROM lead_delivery_jobs),(SELECT count(*) FROM lead_delivery_worker_state)") == [(0, 0)],
            "no_historical_delivery_replay_required")
    require(query(upgrade, "SELECT count(*) FROM newsletter_subscriptions") == [(0,)], "no_historical_enrollment_required")
    require(catalog(upgrade, NEW_TABLES) == fresh_catalog, "fresh_upgrade_catalog_parity_required")
    require(catalog(upgrade, EXPECTED_TABLES) == fresh_full_catalog, "all_26_catalogs_equal_required")
    advance("forward_newsletter_contracts")
    forward_contracts = newsletter_contracts(upgrade)
    advance("candidate_data")
    seeded = rollback_mode(upgrade, "seed_candidate")
    current = snapshot(upgrade, EXPECTED_TABLES)
    repeated = catalog(upgrade, NEW_TABLES)
    repeat_migration(upgrade)
    require(snapshot(upgrade, EXPECTED_TABLES) == current and catalog(upgrade, NEW_TABLES) == repeated,
            "repeated_migration_preservation_required")
    advance("compatible_old_startup")
    native(ROLLBACK, upgrade, "bootstrap_db.py")
    native(ROLLBACK, upgrade, "-m", "alembic", "upgrade", "head")
    require(snapshot(upgrade, EXPECTED_TABLES) == current, "compatible_old_startup_no_mutation_required")
    advance("compatible_old_runtime")
    rolled_back = rollback_mode(upgrade, "verify_old")
    after = snapshot(upgrade, EXPECTED_TABLES)
    # The old endpoint intentionally creates one new synthetic lead. Everything
    # else, including every queued job, must remain byte-for-byte unchanged.
    require(all(after[name] == current[name] for name in current if name != "leads"), "rollback_preserved_records_required")
    before_leads = json.loads(current["leads"])
    after_leads = json.loads(after["leads"])
    require(len(after_leads) == len(before_leads) + 1 and all(row in after_leads for row in before_leads),
            "one_old_client_intake_only_required")
    require(catalog(upgrade, NEW_TABLES) == repeated and query(upgrade, "SELECT version_num FROM alembic_version") == [(HEAD,)],
            "rollback_schema_head_preserved_required")
    return {"status": "PASS", "application_commit": APP_COMMIT, "application_tree": APP_TREE,
            "live_commit": LIVE_COMMIT, "alembic_head": HEAD, "table_count": 26,
            "fresh_upgrade_catalog_equal": True, "historical_rows_sequences_unchanged": True,
            "catalog_comparison_table_count": 26, "historical_catalogs_preserved": True,
            "delivery_tables_empty_after_migration": True, "private_direct_api_tables": 1,
            "newsletter_contracts": {"fresh": fresh_contracts, "forward": forward_contracts},
            "repeated_migration_preserved_data": True, "compatible_old_source_started": True,
            "compatible_old_runtime": rolled_back, "baseline": baseline, "new_code_data": seeded,
            "retained_delivery_jobs": True, "one_old_client_intake": True,
            "live_provider_requests": 0,
            "fresh_catalog_sha256": sha256(json.dumps(fresh_full_catalog, sort_keys=True).encode()).hexdigest()}


if __name__ == "__main__":
    try:
        result = run()
    except Exception as error:
        result = {"status": "FAIL", "stage": stage, "error_type": type(error).__name__}
        if isinstance(error, RehearsalFailure):
            result["reason"] = str(error)
        print("LIVE_DELIVERY_SCHEMA_RESULT " + json.dumps(result, sort_keys=True), flush=True)
        raise SystemExit(1) from None
    print("LIVE_DELIVERY_SCHEMA_RESULT " + json.dumps(result, sort_keys=True), flush=True)
