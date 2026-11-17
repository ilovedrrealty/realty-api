"""Run review-task document evidence cases in nonce-bound PostgreSQL fixtures."""

from contextlib import closing
from hashlib import sha256
from io import StringIO
from pathlib import Path
import json
import os
import re
import subprocess
import sys
import unittest


APP = Path("/application")
BASELINE_HEAD = "e12ac7d49f03"
EVIDENCE_HEAD = "21f2fe166874"
BASELINE_SHAPE = "76eafd85ceb2e126f02efe903cc553297e0ee14622d6f1a4bab6ef70213675cf"
CORE_TABLES = (
    "transaction_rooms", "transaction_room_events", "transaction_room_grants",
    "transaction_room_milestones", "transaction_room_tasks",
)
DOC_TABLES = ("transaction_documents", "transaction_document_versions")
REFERRAL_TABLES = ("network_service_referrals", "network_service_referral_events")
PRIVATE_TABLES = CORE_TABLES + DOC_TABLES + REFERRAL_TABLES
TRIGGERS = ("transaction_room_events_immutable", "transaction_room_lead_changed")
EVIDENCE_COLUMNS = {"evidence_document_id", "evidence_version_id"}
EVIDENCE_CONSTRAINTS = {"ck_transaction_task_evidence_pair", "fk_transaction_task_evidence_version"}
MODES = {"baseline", "fresh", "upgrade", "core", "documents", "adapter"}
TESTS = {
    "core": ("test_transaction_room.py", 9),
    "documents": ("test_transaction_documents.py", 5),
    "adapter": ("test_transaction_document_storage.py", 7),
}
TEST_DB = "ildr_platform_test_20261025"
HOST = "127.0.0.1"
PORT = 55449
stage = "checkout"
failed_methods = []


def check(condition):
    if not condition:
        raise RuntimeError("Rehearsal contract failed")


def verify_runtime(commit, tree):
    check(sys.platform == "linux" and sys.flags.isolated and sys.dont_write_bytecode
          and os.environ.get("GITHUB_ACTIONS") == "true"
          and os.environ.get("GITHUB_REPOSITORY") == "ilovedrrealty/realty-api"
          and {interface.name for interface in Path("/sys/class/net").iterdir()} == {"lo"})
    check(APP.resolve(strict=True) == APP and os.statvfs(APP).f_flag & os.ST_RDONLY)
    config = APP / ".env"
    check(not config.exists() and not config.is_symlink())

    def git(*arguments):
        return subprocess.check_output(
            ["git", "--no-optional-locks", "-c", f"safe.directory={APP}", "-C", str(APP), *arguments],
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"},
            stderr=subprocess.DEVNULL, timeout=10,
        ).decode("ascii").strip()

    check(git("rev-parse", "HEAD", "HEAD^{tree}").splitlines() == [commit, tree]
          and not git("status", "--porcelain=v1", "--untracked-files=all"))
    sys.path.insert(0, str(APP))
    os.chdir(APP)


def connect(database):
    import psycopg2

    return psycopg2.connect(host=HOST, port=PORT, dbname=database, user="postgres", connect_timeout=4,
                            options="-c statement_timeout=10000 -c lock_timeout=5000")


def check_database(database, nonce, head):
    with closing(connect(database)) as conn, conn.cursor() as cursor:
        cursor.execute("""
            SELECT current_database(), current_user, host(inet_server_addr()), inet_server_port(),
                   current_setting('server_version_num')::integer, shobj_description(oid, 'pg_database'),
                   to_regclass('public.alembic_version') IS NOT NULL
            FROM pg_database WHERE datname = current_database()
        """)
        check(cursor.fetchone() == (database, "postgres", HOST, PORT, 170006, f"ildr-d04-ci:{nonce}", head is not None))
        if head is None:
            cursor.execute("""
                SELECT count(*) FROM pg_class rel JOIN pg_namespace ns ON ns.oid = rel.relnamespace
                WHERE ns.nspname='public'
            """)
            check(cursor.fetchone() == (0,))
        else:
            cursor.execute("SELECT version_num FROM public.alembic_version")
            check(cursor.fetchall() == [(head,)])
        cursor.execute("""SELECT rolname, rolcanlogin, rolsuper, rolbypassrls FROM pg_roles
            WHERE rolname IN ('anon','authenticated','service_role') ORDER BY rolname""")
        check(cursor.fetchall() == [(name, False, False, False) for name in ("anon", "authenticated", "service_role")])


def configure(database=None):
    local = f"postgresql+psycopg2://postgres@{HOST}:{PORT}"
    os.environ.update(
        DATABASE_URL=f"{local}/{database}" if database is not None else "sqlite+pysqlite:///:memory:",
        JWT_SECRET="synthetic-hosted-transaction-room-only", ENVIRONMENT="development",
        GHL_ENABLED="false", LEAD_DELIVERY_ENABLED="false", CUSTOMER_MESSAGES_ENABLED="false",
        TRANSACTION_DOCUMENT_BUCKET="", SUPABASE_URL="", SUPABASE_SERVICE_KEY="",
        RESEND_API_KEY="", GHL_API_KEY="", GHL_LOCATION_ID="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="",
    )
    if database == TEST_DB:
        os.environ["SEARCH_FILTER_TEST_DATABASE_URL"] = f"{local}/{TEST_DB}"


def run_cases(mode):
    global stage
    stage = "tests"
    pattern, count = TESTS[mode]
    # Native unittest discover with the conventional tests start directory
    # also resolves the documents suite's shared room fixture import.
    suite = unittest.defaultTestLoader.discover("tests", pattern=pattern)
    sqlstates = set()
    postgres_mode = mode != "adapter"
    if postgres_mode:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        def note_sqlstate(context):
            value = getattr(context.original_exception, "pgcode", None)
            if isinstance(value, str) and re.fullmatch(r"[A-Z0-9]{5}", value):
                sqlstates.add(value)

        event.listen(Engine, "handle_error", note_sqlstate)
    try:
        result = unittest.TextTestRunner(stream=StringIO()).run(suite)
    finally:
        if postgres_mode:
            event.remove(Engine, "handle_error", note_sqlstate)

    for case, trace in result.failures + result.errors:
        name = getattr(case, "_testMethodName", "setup_or_teardown")
        if re.fullmatch(r"[a-z0-9_]{1,100}", name):
            failed_methods.append(name)
            if postgres_mode:
                frame = re.search(
                    rf'(?m)^  File "/application/tests/{re.escape(pattern)}", line ([1-9][0-9]{{0,3}}), in {name}$',
                    trace,
                )
                statuses = re.search(r"(?m)^AssertionError: ([1-5][0-9]{2}) != ([1-5][0-9]{2})(?= :|$)", trace)
                line = frame.group(1) if frame else "none"
                actual, expected = statuses.groups() if statuses else ("none", "none")
                print(f"TASK_EVIDENCE_NATIVE_ASSERTION test={name} line={line} actual={actual} expected={expected}")
    if postgres_mode and not result.wasSuccessful():
        print("TASK_EVIDENCE_NATIVE_SQLSTATES codes=" + (",".join(sorted(sqlstates)[:8]) or "none"))
    check(result.testsRun == count and result.wasSuccessful() and not result.skipped
          and not result.expectedFailures and not result.unexpectedSuccesses)
    return count


def check_test_schema_cleanup():
    global stage
    stage = "test_cleanup"
    with closing(connect(TEST_DB)) as conn, conn.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_namespace WHERE nspname ~ '^transaction_room_test_'")
        check(cursor.fetchone() == (0,))


def catalog(database, *, evidence):
    selected = list(PRIVATE_TABLES)
    with closing(connect(database)) as conn, conn.cursor() as cursor:
        cursor.execute("""
            SELECT rel.relname, rel.relrowsecurity, EXISTS (
                SELECT 1 FROM pg_roles role WHERE role.rolname IN ('anon','authenticated','service_role')
                AND has_table_privilege(role.oid, rel.oid, 'SELECT,INSERT,UPDATE,DELETE')
            ) AS has_grants
            FROM pg_class rel JOIN pg_namespace ns ON ns.oid=rel.relnamespace
            WHERE ns.nspname='public' AND rel.relname::text=ANY(%s) AND rel.relkind='r'
            ORDER BY rel.relname
        """, (selected,))
        check(cursor.fetchall() == [(name, True, False) for name in sorted(selected)])
        cursor.execute("""
            SELECT table_name, column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name::text=ANY(%s) ORDER BY table_name,ordinal_position
        """, (selected,))
        columns = cursor.fetchall()
        cursor.execute("""
            SELECT rel.relname, con.conname, pg_get_constraintdef(con.oid)
            FROM pg_constraint con JOIN pg_class rel ON rel.oid=con.conrelid
            JOIN pg_namespace ns ON ns.oid=rel.relnamespace
            WHERE ns.nspname='public' AND rel.relname::text=ANY(%s) ORDER BY rel.relname,con.conname
        """, (selected,))
        checks = cursor.fetchall()
        cursor.execute("""
            SELECT tablename,indexname,indexdef FROM pg_indexes
            WHERE schemaname='public' AND tablename::text=ANY(%s) ORDER BY tablename,indexname
        """, (selected,))
        indexes = cursor.fetchall()
        cursor.execute("""
            SELECT t.tgname, t.tgenabled, pg_get_triggerdef(t.oid)
            FROM pg_trigger t JOIN pg_class rel ON rel.oid=t.tgrelid
            JOIN pg_namespace ns ON ns.oid=rel.relnamespace WHERE ns.nspname='public'
            AND NOT t.tgisinternal AND t.tgname::text=ANY(%s) ORDER BY t.tgname
        """, (list(TRIGGERS),))
        triggers = cursor.fetchall()
        check(len(columns) > 0 and len(checks) > 0 and len(indexes) > 0
              and [(name, enabled) for name, enabled, _ in triggers] == [(name, "O") for name in TRIGGERS])
    task_columns = [row for row in columns if row[0] == "transaction_room_tasks" and row[1] in EVIDENCE_COLUMNS]
    task_constraints = [row for row in checks if row[0] == "transaction_room_tasks" and row[1] in EVIDENCE_CONSTRAINTS]
    if evidence:
        check([(row[1], *row[2:]) for row in task_columns] == [
            ("evidence_document_id", "uuid", "YES", None), ("evidence_version_id", "uuid", "YES", None),
        ])
        check({row[1] for row in task_constraints} == EVIDENCE_CONSTRAINTS)
    else:
        check(not task_columns and not task_constraints)
    inherited = ([row for row in columns if row not in task_columns],
                 [row for row in checks if row not in task_constraints], indexes, triggers)
    check(sha256(json.dumps(inherited, separators=(",", ":")).encode()).hexdigest() == BASELINE_SHAPE)
    return sha256(json.dumps((columns, checks, indexes, triggers), separators=(",", ":")).encode()).hexdigest()


def sentinel(database, nonce, *, create=False, evidence=False):
    with closing(connect(database)) as conn, conn, conn.cursor() as cursor:
        if create:
            cursor.execute("""
                INSERT INTO users (id, email, display_name, role, status, email_verified)
                VALUES (%s::uuid, 'd04-fixture@example.invalid', 'Synthetic migration sentinel', 'buyer', 'active', true)
            """, (nonce,))
            cursor.execute("""
                INSERT INTO listings (id, title, type, "transaction", price, location, submitted_by, status)
                VALUES (%s::uuid, 'Synthetic migration listing', 'villa', 'sale', 1000, 'Synthetic area', %s::uuid, 'active')
            """, (nonce, nonce))
            cursor.execute("""
                INSERT INTO leads (id, type, name, property_id, from_user_id)
                VALUES (%s::uuid, 'property_inquiry', 'Synthetic migration lead', %s::uuid, %s::uuid)
            """, (nonce, nonce, nonce))
            cursor.execute("""
                INSERT INTO transaction_rooms (id, lead_id, listing_id, buyer_id, title, status, version, created_at)
                VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, 'Synthetic migration room', 'open', 1, now())
            """, (nonce, nonce, nonce, nonce))
            cursor.execute("""
                INSERT INTO transaction_room_tasks (id, room_id, title, assignee_user_id, status, version)
                VALUES (%s::uuid, %s::uuid, 'Synthetic original review task', %s::uuid, 'open', 1)
            """, (nonce, nonce, nonce))
        cursor.execute("""
            SELECT display_name, role::text, status::text, email_verified, user_code IS NOT NULL
            FROM users WHERE id=%s::uuid
        """, (nonce,))
        check(cursor.fetchall() == [("Synthetic migration sentinel", "buyer", "active", True, True)])
        cursor.execute("""
            SELECT title, status, version, room_id = %s::uuid, assignee_user_id = %s::uuid
            FROM transaction_room_tasks WHERE id = %s::uuid
        """, (nonce, nonce, nonce))
        check(cursor.fetchall() == [("Synthetic original review task", "open", 1, True, True)])
        if evidence:
            cursor.execute("""
                SELECT evidence_document_id, evidence_version_id FROM transaction_room_tasks WHERE id=%s::uuid
            """, (nonce,))
            check(cursor.fetchall() == [(None, None)])


def migrate(mode, database, nonce):
    global stage
    from alembic import command
    from alembic.config import Config
    import bootstrap_db

    alembic_config = Config(str(APP / "alembic.ini"))
    if mode in {"baseline", "fresh"}:
        stage = "bootstrap"
        bootstrap_db.main()
    if mode == "baseline":
        stage = "migrate_baseline"
        command.upgrade(alembic_config, BASELINE_HEAD)
        stage = "catalog"
        check_database(database, nonce, BASELINE_HEAD)
        shape = catalog(database, evidence=False)
        sentinel(database, nonce, create=True)
        return BASELINE_HEAD, len(PRIVATE_TABLES), shape
    if mode == "upgrade":
        catalog(database, evidence=False)
        sentinel(database, nonce)
    stage = "migrate_evidence"
    command.upgrade(alembic_config, EVIDENCE_HEAD)
    stage = "catalog"
    check_database(database, nonce, EVIDENCE_HEAD)
    if mode == "upgrade":
        sentinel(database, nonce, evidence=True)
    return EVIDENCE_HEAD, len(PRIVATE_TABLES), catalog(database, evidence=True)


def run(mode, nonce, commit, tree):
    global stage
    check(mode in MODES and re.fullmatch(r"[a-f0-9]{32}", nonce)
          and all(re.fullmatch(r"[a-f0-9]{40}", value) for value in (commit, tree)))
    verify_runtime(commit, tree)
    head, private_tables, shape, count = "none", 0, "none", 0
    if mode == "adapter":
        configure()
        count = run_cases(mode)
    else:
        database = {
            "baseline": f"ildr_d04_upgrade_{nonce}", "fresh": f"ildr_d04_fresh_{nonce}",
            "upgrade": f"ildr_d04_upgrade_{nonce}", "core": TEST_DB, "documents": TEST_DB,
        }[mode]
        stage = "disposable_db"
        check_database(database, nonce, BASELINE_HEAD if mode == "upgrade" else None)
        configure(database)
        if mode in TESTS:
            count = run_cases(mode)
            check_test_schema_cleanup()
        else:
            head, private_tables, shape = migrate(mode, database, nonce)
    print(f"TRANSACTION_ROOM_NATIVE {mode} head={head} private_tables={private_tables} "
          f"shape={shape} tests={count} pass={count}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in MODES else "refused"
    try:
        run(*sys.argv[1:])
    except Exception as error:
        kind = type(error).__name__
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", kind):
            kind = "UnhandledError"
        sqlstate = getattr(getattr(error, "orig", error), "pgcode", None)
        if not isinstance(sqlstate, str) or not re.fullmatch(r"[A-Z0-9]{5}", sqlstate):
            sqlstate = "none"
        methods = ",".join(failed_methods) or "none"
        print(f"TRANSACTION_ROOM_NATIVE_FAILURE {mode} stage={stage} class={kind} "
              f"sqlstate={sqlstate} methods={methods}")
        raise SystemExit(1) from None
