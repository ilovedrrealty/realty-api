"""Owned native assignment checks. Never connect to an unmarked/live database."""
import ast
from contextlib import ExitStack, closing, redirect_stderr, redirect_stdout
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
import unittest
from unittest.mock import patch
from urllib import request as urllib_request
from uuid import UUID

TOOLS = Path(__file__).resolve().parent
PLAN = json.loads((TOOLS / "PLAN.json").read_bytes())
NEW = ("assignment_email_jobs", "assignment_events")
FUNCTIONS = ("assignment_guard_event", "assignment_guard_notice")
SUITES = ("assignment", "admin_assignment", "lead_tracking")
OLD_HEAD, HEAD = "e8b5a3d1f907", "f7a9c3e2d114"
STAGE = "source"
EXTERNAL = []
MOCK_CALLS = []
PHASES = {"source", "unit_inventory", "unit_suite", "unit_cleanup", "previous99", "baseline106", "seed106",
          "migrate108", "projection107", "privacy", "empty_downgrade", "populated_refusal", "preservation"}


class Failure(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Failure(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def emit(kind, value):
    print("ASSIGNMENT_NATIVE_" + kind + " " + canonical(value), flush=True)


def helpers(commit=None, tree=None):
    for relative, digest in PLAN["helper_pins"].items():
        require(relative.startswith("ci/"), "frozen_helper_namespace_required")
        path = TOOLS.parent / relative.removeprefix("ci/")
        require(path.is_file() and not path.is_symlink() and os.statvfs(path).f_flag & os.ST_RDONLY
                and sha256(path.read_bytes()).hexdigest() == digest, "frozen_helper_bytes_required")
    path = TOOLS.parent / "native-inbox/verify.py"
    spec = spec_from_file_location("assignment_inbox_helpers", path)
    native = module_from_spec(spec)
    spec.loader.exec_module(native)
    native.COMMIT = commit or PLAN["application"]["commit"]
    native.TREE = tree or PLAN["application"]["tree"]
    base = native.helpers()
    return native, base


def inventory():
    import importlib.metadata as metadata
    actual = {re.sub(r"[-_.]+", "-", d.metadata["Name"].lower()): d.version for d in metadata.distributions()}
    expected = json.loads(Path("/opt/assignment-native/baseline-inventory.json").read_bytes())
    require(actual == {**expected, "openpyxl": "3.1.5", "defusedxml": "0.7.1", "et-xmlfile": "2.0.0"},
            "exact_dependency_inventory_required")


def claim(mode):
    import psycopg2
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    require(re.fullmatch(r"[a-f0-9]{32}", nonce), "owned_nonce_required")
    name = "ildr_assignment_ci_" + nonce if mode == "canonical" else "ildr_platform_test_20261025"
    marker = "ildr-assignment-" + ("canonical" if mode == "canonical" else "unit") + "-ci:" + nonce
    with closing(psycopg2.connect(host="127.0.0.1", port=55449, dbname=name, user="postgres", password="", connect_timeout=4)) as db, db.cursor() as cursor:
        cursor.execute("""SELECT current_database(),host(inet_server_addr()),inet_server_port(),
            current_setting('server_version_num')::int,shobj_description(oid,'pg_database'),
            NOT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),
            NOT EXISTS(SELECT FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg_%%')
            FROM pg_database WHERE datname=current_database()""")
        require(cursor.fetchone() == (name, "127.0.0.1", 55449, 170006, marker, True, True), "fresh_owned_database_required")
    return name, f"postgresql+psycopg2://postgres@127.0.0.1:55449/{name}"


def refuse(*_args, **_kwargs):
    EXTERNAL.append(1)
    raise RuntimeError("external_transport_refused")


def provider_patches():
    import httpx, requests, resend
    actual_mock = httpx.MockTransport.handle_request
    actual_async_mock = httpx.MockTransport.handle_async_request

    def observed_mock(self, request):
        MOCK_CALLS.append("sync")
        return actual_mock(self, request)

    async def observed_async_mock(self, request):
        MOCK_CALLS.append("async")
        return await actual_async_mock(self, request)

    return (patch.object(httpx.HTTPTransport, "handle_request", refuse),
            patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse),
            patch.object(requests.Session, "send", refuse), patch.object(urllib_request, "urlopen", refuse),
            patch.object(resend.Emails, "send", refuse), patch.object(smtplib.SMTP, "connect", refuse),
            patch.object(smtplib.SMTP_SSL, "connect", refuse),
            patch.object(httpx.MockTransport, "handle_request", observed_mock),
            patch.object(httpx.MockTransport, "handle_async_request", observed_async_mock))


def security(catalog, database):
    return {
        "functions": catalog.query(database, """SELECT p.proname,pg_get_function_identity_arguments(p.oid),p.prosecdef,
            p.proconfig::text,p.proacl::text,p.proowner,p.provolatile,p.proleakproof,p.proparallel
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'
            ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)"""),
        "sequences": catalog.query(database, """SELECT sequencename,sequenceowner,data_type,start_value,min_value,
            max_value,increment_by,cycle,cache_size FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"""),
        "sequence_acl": catalog.query(database, """SELECT c.relname,c.relowner,c.relacl::text
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind='S' ORDER BY c.relname"""),
        "types": catalog.query(database, """SELECT t.typname,t.typtype,t.typcategory,t.typnotnull,t.typdefault,t.typacl::text
            FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public' ORDER BY t.typname"""),
        "schema_acl": catalog.query(database, "SELECT nspowner,nspacl::text FROM pg_namespace WHERE nspname='public'"),
        "default_acl": catalog.query(database, "SELECT defaclrole,defaclnamespace,defaclobjtype,defaclacl::text FROM pg_default_acl ORDER BY 1,2,3"),
        "roles": catalog.query(database, "SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls FROM pg_roles ORDER BY rolname")}


def subprocess_checked(source, root, url, reason, timeout=180):
    result = subprocess.run(["/usr/local/bin/python", "-I", "-B", "-c", source], cwd=root,
                            env=dict(os.environ, DATABASE_URL=url), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=timeout)
    require(result.returncode == 0 and len(result.stdout) <= 2 * 1024 * 1024, reason)
    return result.stdout


def migration(root, url, direction, target):
    require(direction in {"upgrade", "downgrade"} and target in {OLD_HEAD, HEAD}, "fixed_migration_target_required")
    code = "import sys;sys.path.insert(0," + repr(str(root)) + ");from alembic import command;from alembic.config import Config;command."
    code += direction + "(Config('alembic.ini')," + repr(target) + ")"
    subprocess_checked(code, root, url, "actual_migration_process_required")


def seed_baseline():
    """Internal separate child: no current application models can leak into seed."""
    logging.disable(logging.CRITICAL)
    native, base = helpers(PLAN["baseline106"]["commit"], PLAN["baseline106"]["tree"])
    os.environ["ILDR_BROWSER_APPLICATION_ROOT"] = "/baseline106"
    application = base.verified_checkout()
    url = os.environ["DATABASE_URL"]
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    require(url == f"postgresql+psycopg2://postgres@127.0.0.1:55449/ildr_assignment_ci_{nonce}", "seed_owned_url_required")
    base.configure(application, url)
    catalog, retained = base.frozen_helpers()
    require(catalog.query("ildr_assignment_ci_" + nonce, "SELECT version_num FROM alembic_version") == [(OLD_HEAD,)],
            "seed_actual106_head_required")
    with ExitStack() as stack:
        for item in provider_patches():
            stack.enter_context(item)
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            counts = retained.seed_old()
    require(counts == native.EXPECTED_SEED and not EXTERNAL and len(output.getvalue().encode()) <= 2 * 1024 * 1024,
            "actual_retained_seed_required")
    return counts


def preserved(catalog, database, tables, rows, definitions, access, include_new):
    require(catalog.snapshot(database, tables) == rows, "all106_old_rows_sequences_required")
    observed = catalog.catalog(database, tables)
    added = [row for row in observed["functions"] if row not in definitions["functions"]]
    require({(row[0], row[1]) for row in added} == ({(name, "") for name in FUNCTIONS} if include_new else set())
            and len(added) == (2 if include_new else 0), "only_two_new_functions_required")
    observed["functions"] = [row for row in observed["functions"] if row not in added]
    require(observed == definitions, "all106_old_catalog_required")
    actual_access = security(catalog, database)
    if include_new:
        actual_access["functions"] = [row for row in actual_access["functions"] if row[0] not in FUNCTIONS]
        actual_access["types"] = [row for row in actual_access["types"] if row[0] not in set(NEW) | {"_" + name for name in NEW}]
    require(actual_access == access, "old_security_sequences_roles_required")
    require(catalog.query(database, "SELECT version_num FROM alembic_version") == [(HEAD if include_new else OLD_HEAD,)],
            "exact_head_transition_required")


def model_projection(connection, schema):
    from sqlalchemy import text
    statements = {
        "columns": """SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
            pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE n.nspname=:schema AND c.relname=ANY(:tables) AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum""",
        "constraints": """SELECT c.relname,k.conname,k.contype,pg_get_constraintdef(k.oid,true)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_constraint k ON k.conrelid=c.oid
            WHERE n.nspname=:schema AND c.relname=ANY(:tables) ORDER BY c.relname,k.conname""",
        "indexes": """SELECT c.relname,i.relname,x.indisprimary,x.indisunique,x.indisvalid,x.indisready,pg_get_indexdef(i.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_index x ON x.indrelid=c.oid
            JOIN pg_class i ON i.oid=x.indexrelid WHERE n.nspname=:schema AND c.relname=ANY(:tables) ORDER BY c.relname,i.relname"""}

    def normalize(value):
        if isinstance(value, str):
            for name in (schema, "public"):
                value = value.replace('"' + name + '".', "").replace(name + ".", "")
        return value
    return {key: [[normalize(value) for value in row] for row in connection.execute(text(statement),
            {"schema": schema, "tables": list(NEW)}).all()] for key, statement in statements.items()}


def projection(catalog, database):
    from sqlalchemy import text
    import models
    import models.trade_contact  # Existing explicit registration omitted by models/__init__.
    from database import Base, engine
    require(len(Base.metadata.tables) == 107, "actual107_model_inventory_required")
    schema = "assignment_model_" + os.environ["ILDR_BROWSER_CI_NONCE"]
    require(re.fullmatch(r"assignment_model_[a-f0-9]{32}", schema), "owned_model_schema_required")
    catalog.verify_check_render_controls(database)
    with engine.begin() as connection:
        connection.execute(text('CREATE SCHEMA "' + schema + '"'))
        connection.execute(text('SET LOCAL search_path TO "' + schema + '",public'))
        Base.metadata.create_all(connection, checkfirst=False)
        require(connection.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname=:schema"), {"schema": schema}).scalar() == 107,
                "independent107_model_tables_required")
        connection.execute(text("SET LOCAL search_path TO pg_catalog"))
        require(model_projection(connection, "public") == model_projection(connection, schema), "new2_full_native_model_projection_required")
        connection.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))


def privacy(catalog, database):
    current = catalog.catalog(database, NEW)
    require(current["rls"] == [(name, True, False) for name in NEW] and current["policies"] == [], "two_private_rls_tables_required")
    clients = {"PUBLIC", "anon", "authenticated", "service_role"}
    require(not any(row[1] in clients for row in current["acl"]), "all_client_table_grants_revoked_required")
    functions = catalog.query(database, """SELECT p.proname,p.prosecdef,p.proconfig,coalesce(r.rolname,'PUBLIC'),a.privilege_type
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
        LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE n.nspname='public' AND p.proname=ANY(%s) ORDER BY p.proname""", (list(FUNCTIONS),))
    require({row[0] for row in functions} == set(FUNCTIONS) and all(not row[1] and row[3] not in clients and row[4] == "EXECUTE"
            and len(row[2] or []) == 1 and row[2][0].split("=", 1)[0] == "search_path"
            and tuple(p.strip() for p in row[2][0].split("=", 1)[1].split(",")) == ("pg_catalog", "public") for row in functions),
            "two_private_invoker_fixed_searchpath_functions_required")
    triggers = catalog.query(database, """SELECT c.relname,t.tgname,t.tgenabled,t.tgtype,p.proname,t.tgnargs,
        t.tgqual IS NULL,t.tgconstraint=0,t.tgattr::text FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE n.nspname='public' AND c.relname=ANY(%s) AND NOT t.tgisinternal ORDER BY c.relname""", (list(NEW),))
    require(triggers == [("assignment_email_jobs", "assignment_notice_guard", "O", 31, "assignment_guard_notice", 0, True, True, ""),
                         ("assignment_events", "assignment_event_immutable", "O", 27, "assignment_guard_event", 0, True, True, "")],
            "exact_two_live_guard_triggers_required")


def populated_refusal(application):
    from sqlalchemy import text
    from database import engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = application / "alembic/versions/f7a9c3e2d114_private_assignment_notifications.py"
    spec = spec_from_file_location("actual_assignment_migration", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            connection.execute(text("""INSERT INTO assignment_events(id,source_kind,user_id,actor_id,request_id,
                request_sha256,cycle,action) VALUES(:id,'account',:owner,:admin,:request,:digest,1,'cleared')"""),
                {"id": UUID(int=910001), "owner": UUID(int=102), "admin": UUID(int=101), "request": UUID(int=910002), "digest": "0" * 64})
            nested = connection.begin_nested()
            refused = False
            try:
                with Operations.context(MigrationContext.configure(connection)):
                    module.downgrade()
            except Exception as error:
                refused = getattr(getattr(error, "orig", None), "pgcode", None) == "23000"
                nested.rollback()
            require(refused, "actual_populated_downgrade_refusal_required")
            require(connection.execute(text("SELECT count(*) FROM assignment_events")).scalar() == 1,
                    "refusal_retains_transaction_history_required")
        finally:
            outer.rollback()


def canonical_mode(native, base, application, database, url):
    global STAGE
    catalog, _retained = base.frozen_helpers()
    start = catalog.query(database, "SELECT pg_postmaster_start_time()")
    STAGE = "previous99"
    native.previous_start(base, url)
    legacy = tuple(sorted(catalog.OLD_TABLES + native.GROUPS))
    require(len(legacy) == 99 and catalog.table_names(database) == legacy and catalog.query(database,
            "SELECT version_num FROM alembic_version") == [("c4e2a7319b60",)], "actual_previous99_c4_required")
    STAGE = "baseline106"
    migration(Path("/baseline106"), url, "upgrade", OLD_HEAD)
    tables = tuple(sorted(legacy + native.INBOX))
    require(len(tables) == 106 and catalog.table_names(database) == tables and catalog.query(database,
            "SELECT version_num FROM alembic_version") == [(OLD_HEAD,)], "genuine106_e8_baseline_required")
    STAGE = "seed106"
    seed_code = "from importlib.util import spec_from_file_location,module_from_spec;" \
                "s=spec_from_file_location('assignment_seed','/ci-tools/assignment-notifications-native/verify.py');" \
                "m=module_from_spec(s);s.loader.exec_module(m);m.seed_baseline();print('ASSIGNMENT_NATIVE_SEED_OK')"
    output = subprocess_checked(seed_code,
                                Path("/baseline106"), url, "baseline_seed_process_required")
    require(output == b"ASSIGNMENT_NATIVE_SEED_OK\n", "exact_seed_child_result_required")
    with closing(catalog.connection(database)) as db, db, db.cursor() as cursor:
        cursor.execute("""ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO PUBLIC,anon,authenticated,service_role;
            ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO PUBLIC,anon,authenticated,service_role""")
    rows, definitions, access = catalog.snapshot(database, tables), catalog.catalog(database, tables), security(catalog, database)
    STAGE = "migrate108"
    migration(application, url, "upgrade", HEAD)
    require(catalog.table_names(database) == tuple(sorted(tables + NEW)), "genuine108_catalog_required")
    preserved(catalog, database, tables, rows, definitions, access, True)
    STAGE = "projection107"
    projection(catalog, database)
    STAGE = "privacy"
    privacy(catalog, database)
    full_catalog = catalog.catalog(database, tables + NEW)
    full_security = security(catalog, database)
    STAGE = "empty_downgrade"
    migration(application, url, "downgrade", OLD_HEAD)
    require(catalog.table_names(database) == tables, "empty_downgrade_only_new2_removed_required")
    preserved(catalog, database, tables, rows, definitions, access, False)
    migration(application, url, "upgrade", HEAD)
    require(catalog.catalog(database, tables + NEW) == full_catalog and security(catalog, database) == full_security,
            "empty_downgrade_reupgrade_exact_required")
    STAGE = "populated_refusal"
    populated_refusal(application)
    STAGE = "preservation"
    preserved(catalog, database, tables, rows, definitions, access, True)
    require(catalog.snapshot(database, NEW) == {"assignment_email_jobs": "[]", "assignment_events": "[]", "sequences": rows["sequences"]},
            "new_assignment_tables_empty_after_controls_required")
    require(catalog.query(database, "SELECT count(*) FROM pg_namespace WHERE nspname LIKE %s", ("assignment_model_%",)) == [(0,)],
            "all_owned_model_schemas_removed_required")
    require(catalog.query(database, "SELECT pg_postmaster_start_time()") == start and not EXTERNAL,
            "same_server_zero_external_required")
    base.verified_checkout()
    emit("RESULT", {"status": "PASS", "mode": "canonical", "old_tables": 106, "schema_tables": 108, "model_tables": 107,
        "new_private_tables": 2, "old_application_rows_exact": 105, "owner_contacts_unchanged": 82,
        "private_profiles_unchanged": 46, "accepted_jobs_unchanged": 9, "cms_version_unchanged": 7, "floorplans_unchanged": 2,
        "old_rows_sequences_catalog_unchanged": True, "old_security_unchanged": True, "current_head_exact": True,
        "new_model_ddl_projection_equal": True, "private_rls_acl": True, "empty_downgrade_reupgrade_equal": True,
        "populated_downgrade_refused": True, "all_owned_schemas_removed": True, "server_start_unchanged": True,
        "external_provider_attempts": 0})


def unit_mode(native, base, application, database, url, label):
    global STAGE
    STAGE = "unit_inventory"
    suite_plan = PLAN["source_suites"][label]
    filename = application / suite_plan["file"]
    require(sha256(filename.read_bytes()).hexdigest() == suite_plan["test_file_sha256"], "exact_source_suite_bytes_required")
    methods = native.suite_inventory(application, filename.name, suite_plan["class"], suite_plan["count"])
    require(methods == set(suite_plan["methods"]), "exact_frozen_method_inventory_required")
    os.environ["SEARCH_FILTER_TEST_DATABASE_URL"] = url
    os.chdir(application)
    sys.path.insert(0, str(application))
    catalog, _retained = base.frozen_helpers()
    start = catalog.query(database, "SELECT pg_postmaster_start_time()")
    schemas = catalog.query(database, "SELECT nspname FROM pg_namespace ORDER BY nspname")
    original = catalog.catalog(database, ("users",))
    sequences, access = catalog.snapshot(database, ()), security(catalog, database)
    captured = StringIO()
    STAGE = "unit_suite"
    with redirect_stdout(captured), redirect_stderr(captured):
        spec = spec_from_file_location("owned_assignment_notification_tests", filename)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        case = getattr(module, suite_plan["class"])
        require(getattr(case, "__unittest_skip__", False) is False, "source_suite_must_not_skip")
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(case)
        require(suite.countTestCases() == suite_plan["count"] and {item._testMethodName for item in suite} == methods,
                "loaded_exact_source_cases_required")
        result = unittest.TestResult()
        suite.run(result)
    emit("COUNTS", {"suite": label, "expected": suite_plan["count"], "run": result.testsRun,
                    "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped)})
    failed = set()
    for item, _trace in result.failures + result.errors:
        name = getattr(item, "_testMethodName", "fixture_setup_or_cleanup")
        failed.add(name if name in methods else "fixture_setup_or_cleanup")
    for name in sorted(failed):
        emit("FAILED_METHOD", {"method": name})
    require(len(captured.getvalue().encode()) <= 2 * 1024 * 1024, "bounded_captured_unit_output_required")
    require(result.testsRun == suite_plan["count"] and result.wasSuccessful() and not result.skipped
            and not result.expectedFailures and not result.unexpectedSuccesses and not EXTERNAL,
            "all_current_source_cases_required")
    require(len(MOCK_CALLS) <= 10000 and (label != "assignment" or MOCK_CALLS.count("async") > 0),
            "actual_bounded_mock_transport_calls_required")
    STAGE = "unit_cleanup"
    require(catalog.table_names(database) == () and catalog.query(database, "SELECT nspname FROM pg_namespace ORDER BY nspname") == schemas,
            "all_unit_schemas_removed_required")
    require(catalog.catalog(database, ("users",)) == original and catalog.snapshot(database, ()) == sequences
            and security(catalog, database) == access, "unit_public_rows_catalog_security_unchanged_required")
    require(catalog.query(database, "SELECT pg_postmaster_start_time()") == start, "unit_server_start_unchanged_required")
    base.verified_checkout()
    emit("RESULT", {"status": "PASS", "mode": label, "tests": suite_plan["count"], "exact_current_source": True,
        "model_created_test_schema": True, "genuine108_claim": False, "all_schema_cleanup": True,
        "public_catalog_unchanged": True, "server_start_unchanged": True, "external_provider_attempts": 0,
        "mocked_provider_calls": len(MOCK_CALLS), "mocked_async_provider_calls": MOCK_CALLS.count("async"),
        "mocked_sync_provider_calls": MOCK_CALLS.count("sync")})


def run():
    logging.disable(logging.CRITICAL)
    require(len(sys.argv) == 2 and sys.argv[1] in {"canonical", *SUITES}, "explicit_worker_mode_required")
    mode = sys.argv[1]
    app = PLAN["application"]
    require(all(isinstance(app[k], str) and re.fullmatch(r"[a-f0-9]{40}", app[k]) for k in ("commit", "tree")),
            "final_reviewed_application_pin_required")
    require(all(type(PLAN["source_suites"][name]["count"]) is int and PLAN["source_suites"][name]["count"] > 0
            and isinstance(PLAN["source_suites"][name]["methods"], list) for name in SUITES), "frozen_source_inventory_required")
    inventory()
    native, base = helpers()
    application = base.verified_checkout()
    database, url = claim(mode)
    base.configure(application, url)
    with ExitStack() as stack:
        for item in provider_patches():
            stack.enter_context(item)
        if mode == "canonical":
            canonical_mode(native, base, application, database, url)
        else:
            unit_mode(native, base, application, database, url, mode)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        reason = str(error) if isinstance(error, Failure) else "unexpected_exception"
        kind = type(error).__name__ if type(error).__name__ in {"Failure", "AssertionError", "RuntimeError", "ValueError",
                "TypeError", "KeyError", "TimeoutExpired", "OperationalError", "ProgrammingError", "IntegrityError"} else "other"
        state = getattr(getattr(error, "orig", error), "pgcode", None)
        state = state if isinstance(state, str) and re.fullmatch(r"[A-Z0-9]{5}", state) else None
        emit("FAILURE", {"stage": STAGE if STAGE in PHASES else "source", "error_type": kind, "reason": reason, "sqlstate": state})
        raise SystemExit(1) from None
