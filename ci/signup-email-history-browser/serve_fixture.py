"""Two isolated hosted modes: actual CustomerDelivery21; real read-only Admin UI."""
import ast
from collections import Counter
from contextlib import closing, redirect_stderr, redirect_stdout
from contextvars import ContextVar
from datetime import timedelta
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
import subprocess
import sys
import unittest
from urllib import request as urllib_request
from unittest.mock import patch
from uuid import UUID

COMMIT = "c4de3e2e25fd3699ccb776c740d6413b402ed317"
TREE = "916d590cd72af5c3f68778441010aa41b59661ea"
BASE_COMMIT = "17943ba1714673c51c43cdbb800106f91612f527"
BASE_TREE = "ff940a7b6e983d4c669202746d9676b4b82e9a06"
HEAD = "e8b5a3d1f907"
NATIVE = Path(__file__).resolve().parent.parent / "native-inbox/verify.py"
NATIVE_SHA = "388fcae33bf63234df9739ac7405c1a2d49b4914af2a32d56e9023fdb7ed144e"
FACTS_FILE = Path("/browser-fixtures/fixtures.json")
ROLES = {"admin": "admin", "buyer": "buyer", "owner": "owner", "realtor": "realtor"}
BOUNDARIES = {"admin_tracked": ("admin", "buyer", 200), "admin_untracked": ("admin", "owner", 200),
              "admin_unknown": ("admin", "unknown", 404), "admin_invalid": ("admin", "invalid", 422),
              "anonymous": (None, "buyer", 401), "buyer": ("buyer", "buyer", 403),
              "owner": ("owner", "buyer", 403), "realtor": ("realtor", "buyer", 403)}
STAGE = "source"
ATTEMPTS = []
INSIDE_HISTORY = ContextVar("owned_signup_history_read", default=False)


class Failure(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Failure(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def helpers():
    require(NATIVE.is_file() and not NATIVE.is_symlink() and os.statvfs(NATIVE).f_flag & os.ST_RDONLY
            and sha256(NATIVE.read_bytes()).hexdigest() == NATIVE_SHA, "exact_readonly_native_helper_required")
    spec = spec_from_file_location("signup_history_native_helpers", NATIVE)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.COMMIT, module.TREE = COMMIT, TREE
    return module, module.helpers()


def inventory():
    import importlib.metadata as metadata
    actual = {re.sub(r"[-_.]+", "-", item.metadata["Name"].lower()): item.version for item in metadata.distributions()}
    expected = json.loads(Path("/opt/contact-import/baseline-inventory.json").read_text())
    require(actual == {**expected, "openpyxl": "3.1.5", "defusedxml": "0.7.1", "et-xmlfile": "2.0.0"},
            "exact_dependency_inventory_required")


def claim(mode):
    import psycopg2
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    require(re.fullmatch(r"[a-f0-9]{32}", nonce), "owned_nonce_required")
    name = "ildr_platform_test_20261025" if mode == "native" else "ildr_browser_ci_" + nonce
    marker = ("ildr-signup-history-native-ci:" if mode == "native" else "ildr-signup-history-browser-ci:") + nonce
    with closing(psycopg2.connect(host="127.0.0.1", port=55449, dbname=name, user="postgres", password="", connect_timeout=4)) as db, db.cursor() as cursor:
        cursor.execute("""SELECT current_database(),host(inet_server_addr()),inet_server_port(),
            current_setting('server_version_num')::int,shobj_description(oid,'pg_database'),
            NOT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),
            NOT EXISTS(SELECT FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg_%%')
            FROM pg_database WHERE datname=current_database()""")
        require(cursor.fetchone() == (name, "127.0.0.1", 55449, 170006, marker, True, True), "fresh_owned_database_required")
    return name, f"postgresql+psycopg2://postgres@127.0.0.1:55449/{name}"


def refuse(*_args, **_kwargs):
    ATTEMPTS.append(1)
    raise RuntimeError("external_transport_refused")


def provider_patches():
    import httpx, requests, resend
    return (patch.object(httpx.HTTPTransport, "handle_request", refuse),
            patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse),
            patch.object(requests.Session, "send", refuse), patch.object(urllib_request, "urlopen", refuse),
            patch.object(resend.Emails, "send", refuse), patch.object(smtplib.SMTP, "connect", refuse),
            patch.object(smtplib.SMTP_SSL, "connect", refuse))


def security(catalog, database):
    return {
        "functions_acl": catalog.query(database, """SELECT p.proname,pg_get_function_identity_arguments(p.oid),
            p.prosecdef,p.proconfig::text,p.proacl::text,p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)"""),
        "sequence_definitions": catalog.query(database, """SELECT sequencename,sequenceowner,data_type,start_value,min_value,
            max_value,increment_by,cycle,cache_size FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"""),
        "public_types": catalog.query(database, """SELECT t.typname,t.typtype,t.typcategory,t.typnotnull,t.typdefault,t.typacl::text
            FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public' ORDER BY t.typname"""),
        "public_schema_acl": catalog.query(database, "SELECT nspowner,nspacl::text FROM pg_namespace WHERE nspname='public'"),
        "roles": catalog.query(database, "SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls FROM pg_roles ORDER BY rolname")}


def native_mode(native, base, application, database, url):
    global STAGE
    STAGE = "native_inventory"
    filename = application / "tests/test_customer_delivery.py"
    methods = native.suite_inventory(application, filename.name, "CustomerDeliveryTests", 21)
    # Do not configure/import main or the browser process's database module. The
    # exact test module deliberately binds application imports to SQLite while
    # its own explicit test engine creates a disposable PostgreSQL model schema.
    os.environ["SEARCH_FILTER_TEST_DATABASE_URL"] = url
    os.chdir(application)
    sys.path.insert(0, str(application))
    catalog, _retained = base.frozen_helpers()
    start = catalog.query(database, "SELECT pg_postmaster_start_time()")
    before = catalog.query(database, "SELECT nspname FROM pg_namespace ORDER BY nspname")
    before_catalog = catalog.catalog(database, ("users",))
    before_sequences = catalog.snapshot(database, ())
    before_security = security(catalog, database)
    captured = StringIO()
    STAGE = "native_customer21"
    patches = provider_patches()
    try:
        for item in patches:
            item.start()
        with redirect_stdout(captured), redirect_stderr(captured):
            spec = spec_from_file_location("owned_signup_customer_delivery", filename)
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            case = module.CustomerDeliveryTests
            require(getattr(case, "__unittest_skip__", False) is False, "native_cases_must_not_skip")
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(case)
            require(suite.countTestCases() == 21 and {item._testMethodName for item in suite} == methods,
                    "exact_native21_inventory_required")
            result = unittest.TestResult()
            suite.run(result)
        counts = {"suite": "customer_delivery", "expected": 21, "run": result.testsRun,
                  "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped)}
        print("SIGNUP_HISTORY_NATIVE_COUNTS " + canonical(counts), flush=True)
        for case, _trace in result.failures + result.errors:
            method = getattr(case, "_testMethodName", "fixture_setup_or_cleanup")
            print("SIGNUP_HISTORY_NATIVE_FAILED_METHOD " + (method if method in methods else "fixture_setup_or_cleanup"), flush=True)
        require(len(captured.getvalue().encode()) <= 2 * 1024 * 1024, "bounded_native_output_required")
        require(result.testsRun == 21 and result.wasSuccessful() and not result.skipped and not result.expectedFailures
                and not result.unexpectedSuccesses and not ATTEMPTS, "all_current_native21_cases_required")
    finally:
        for item in reversed(patches):
            item.stop()
    STAGE = "native_cleanup"
    require(catalog.table_names(database) == () and catalog.query(database,
        "SELECT nspname FROM pg_namespace ORDER BY nspname") == before, "actual_native_schema_cleanup_required")
    require(catalog.catalog(database, ("users",)) == before_catalog and catalog.snapshot(database, ()) == before_sequences
            and security(catalog, database) == before_security, "native_public_catalog_sequences_security_unchanged_required")
    require(catalog.query(database, "SELECT pg_postmaster_start_time()") == start, "native_server_start_unchanged_required")
    base.verified_checkout()
    print("SIGNUP_HISTORY_NATIVE_RESULT " + canonical({"status": "PASS", "tests": 21,
        "exact_current_source": True, "model_created_test_schema": True, "genuine106_claim": False,
        "all_schema_cleanup": True, "public_catalog_unchanged": True, "server_start_unchanged": True,
        "external_provider_attempts": 0}), flush=True)


def upgrade_baseline(base, url):
    reference = Path("/baseline106")
    original = base.APP_COMMIT, base.APP_TREE
    try:
        base.APP_COMMIT, base.APP_TREE = BASE_COMMIT, BASE_TREE
        os.environ["ILDR_BROWSER_APPLICATION_ROOT"] = str(reference)
        require(base.verified_checkout() == reference, "exact_baseline179_required")
    finally:
        base.APP_COMMIT, base.APP_TREE = original
        os.environ.pop("ILDR_BROWSER_APPLICATION_ROOT", None)
    code = "import sys;sys.path.insert(0,'/baseline106');from alembic import command;from alembic.config import Config;command.upgrade(Config('alembic.ini'),'head')"
    result = subprocess.run(["/usr/local/bin/python", "-I", "-B", "-c", code], cwd=reference,
        env=dict(os.environ, DATABASE_URL=url), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    require(result.returncode == 0 and len(result.stdout) <= 2 * 1024 * 1024, "actual_baseline_d8_upgrade_required")


def metadata_item(row):
    def timestamp(value):
        return value.isoformat() if value is not None else None
    return {"id": str(row.id), "event_kind": "signup", "channel": "email", "provider": row.provider,
            "step": row.step, "status": row.status, "attempts": row.attempt_count, "max_attempts": 4,
            "created_at": timestamp(row.created_at), "first_attempt_at": timestamp(row.first_attempt_at),
            "last_attempt_at": timestamp(row.last_attempt_at), "next_attempt_at": timestamp(row.next_attempt_at),
            "accepted_at": timestamp(row.accepted_at), "error_code": row.error_code,
            "outcome_uncertain": row.outcome_uncertain}


def seed(native, base, catalog, retained, database, url):
    global STAGE
    STAGE = "previous99"
    native.previous_start(base, url)
    legacy = tuple(sorted(catalog.OLD_TABLES + native.GROUPS))
    require(catalog.table_names(database) == legacy and catalog.query(database,
        "SELECT version_num FROM alembic_version") == [(native.OLD_HEAD,)], "genuine_previous99_required")
    require(retained.seed_old() == native.EXPECTED_SEED, "exact_original_retained_seed_required")
    old_rows, old_schema = catalog.snapshot(database, legacy), catalog.catalog(database, legacy)
    old_columns = {name: [row[1] for row in old_schema["columns"] if row[0] == name] for name in legacy}
    STAGE = "baseline106"
    upgrade_baseline(base, url)
    native.old_preserved(catalog, database, legacy, old_rows, old_schema)
    tables = tuple(sorted(legacy + native.INBOX))
    require(catalog.table_names(database) == tables and catalog.query(database,
        "SELECT version_num FROM alembic_version") == [("d8f4c2a91e67",)], "actual_baseline106_d8_required")
    STAGE = "current_e8"
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config("alembic.ini"), "head")
    require(catalog.table_names(database) == tables and catalog.query(database,
        "SELECT version_num FROM alembic_version") == [(HEAD,)], "actual_current_e8_106_required")
    require(catalog.snapshot(database, legacy, columns=old_columns) == old_rows,
            "original99_old_columns_rows_sequences_preserved_required")
    native.privacy(catalog, database)
    from database import SessionLocal
    from models.user import User
    from models.lead_delivery import LeadDeliveryJob as Job
    from utils.security import hash_password
    identities = {actor: retained.IDS[actor] for actor in ROLES}
    accounts = {actor: {"email": "", "password": secrets.token_urlsafe(24)} for actor in ROLES}
    facts = {"ids": {actor: str(value) for actor, value in identities.items()}, "roles": ROLES,
             "names": {}, "codes": {}, "tracked": "buyer", "untracked": "owner",
             "unknown_user_id": str(UUID(int=999999)), "invalid_user_id": "not-a-uuid", "boundaries": list(BOUNDARIES),
             "private_markers": ["OWNED_PRIVATE_EMAIL_PAYLOAD_NEVER_PROJECT", "synthetic-never-send@example.com",
                                 "Owned private body never projected", "owned-private-provider-id"],
             "originalAcceptedJobs": 9, "signupJobs": 11}
    STAGE = "seed_signup_jobs"
    statuses = ("pending", "processing", "retry_wait", "accepted", "failed", "needs_review", "skipped",
                "pending", "accepted", "failed", "needs_review")
    steps = ["email_" + str(index) for index in range(1, 11)] + ["customer_email"]
    permutation = (7, 2, 10, 5, 0, 9, 3, 8, 1, 6, 4)
    stamp = retained.HISTORY + timedelta(days=700)
    with SessionLocal.begin() as db:
        for actor, identity in identities.items():
            user = db.get(User, identity)
            require(user.role == ROLES[actor] and user.status == "active" and user.email_verified,
                    "four_original_verified_role_identities_required")
            accounts[actor]["email"] = user.email
            user.password_hash = hash_password(accounts[actor]["password"])
            facts["names"][actor], facts["codes"][actor] = user.display_name, user.user_code
        require(db.query(Job).filter(Job.user_id.isnot(None)).count() == 0, "no_existing_signup_jobs_required")
        require(db.get(User, UUID(facts["unknown_user_id"])) is None, "unused_owned_unknown_user_id_required")
        for index, step in enumerate(steps):
            status = statuses[index]
            attempted = status not in {"pending", "skipped"}
            payload = {"marker": facts["private_markers"][0], "to": "synthetic-never-send@example.com",
                       "html": "Owned private body never projected", "step": step}
            db.add(Job(id=UUID(int=81000 + permutation[index]), user_id=identities["buyer"], lead_id=None,
                step=step, provider="resend", payload=payload, payload_sha256=sha256(canonical(payload).encode()).hexdigest(),
                status=status, attempt_count=1 if attempted else 0, safe_to_retry=False,
                outcome_uncertain=status == "needs_review", created_at=stamp, updated_at=stamp,
                next_attempt_at=stamp, first_attempt_at=stamp if attempted else None,
                last_attempt_at=stamp if attempted else None, accepted_at=stamp if status == "accepted" else None,
                error_code="resend_outcome_unknown" if status == "needs_review" else "resend_http_429" if status == "failed" else None,
                provider_id="owned-private-provider-id-" + str(index) if status == "accepted" else None))
        db.flush()
        rows = db.query(Job).filter(Job.user_id == identities["buyer"], Job.provider == "resend").order_by(Job.created_at.desc(), Job.id.desc()).all()
        require(len(rows) == 11 and {row.step for row in rows} == set(steps), "eleven_canonical_distinct_steps_required")
        require([str(row.id) for row in rows] == sorted([str(row.id) for row in rows], reverse=True)
                and len({row.created_at for row in rows}) == 1, "actual_stored_timestamp_uuid_tie_order_required")
        facts["ordered_items"] = [metadata_item(row) for row in rows]
        facts["expected_pages"] = [[row["id"] for row in facts["ordered_items"][:10]], [facts["ordered_items"][10]["id"]]]
        facts["expected_api_pages"] = [[row["id"] for row in facts["ordered_items"][index:index + 2]] for index in range(0, 11, 2)]
    return tables, accounts, identities, facts


def response_expected(facts, actor, limit, offset):
    items = facts["ordered_items"] if actor == "buyer" else []
    return {"total": len(items), "limit": limit, "offset": offset, "tracked": bool(items), "items": items[offset:offset + limit]}


def normalized(value):
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.]+(?:Z|\+00:00)", value):
        return value.removesuffix("Z").removesuffix("+00:00")
    return value


def diagnostic(prefix, error, phase=None):
    cause = getattr(error, "orig", error)
    name, state = type(error).__name__, getattr(cause, "pgcode", None)
    print(prefix + " " + canonical({"stage": phase or STAGE,
        "error_type": name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name) else "UnhandledError",
        "reason": str(error) if isinstance(error, Failure) and re.fullmatch(r"[a-z_0-9]{1,100}", str(error)) else "unexpected_failure",
        "sqlstate": state if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state) else "unavailable"}), flush=True)


def confined(app, base, accounts, identities, facts, observed):
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from utils.auth import _decode_token
    actors = {str(value): actor for actor, value in identities.items()}
    logins = {canonical(value): actor for actor, value in accounts.items()}
    paths = {actor: "/admin/users/" + (facts[actor + "_user_id"] if actor in {"unknown", "invalid"} else str(value)) + "/communications"
             for actor, value in {**identities, "unknown": None, "invalid": None}.items()}

    async def handle(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        request = Request(scope, receive)
        method, path = scope["method"], scope["path"]
        token = request.cookies.get("ildr_token")
        actor = actors.get(_decode_token(token)) if token else None
        phase = "read" if method in {"GET", "HEAD", "OPTIONS"} else "mutation"
        try:
            if method in {"GET", "HEAD", "OPTIONS"}:
                label = request.headers.get("x-ci-boundary")
                expected_status, target = None, None
                if label is not None:
                    match = re.fullmatch(r"(en|es)_([a-z_]+)", label)
                    require(method == "GET" and match and match[2] in BOUNDARIES, "known_boundary_label_required")
                    expected_actor, target, expected_status = BOUNDARIES[match[2]]
                    require(actor == expected_actor and path == paths[target] and not request.headers.get("authorization"),
                            "exact_boundary_cookie_actor_path_required")
                    require(not scope["query_string"], "boundary_uses_unchanged_default25_required")
                    require(actor is not None or not request.headers.get("cookie"), "actual_anonymous_request_required")
                history = path in paths.values()
                if path.startswith("/admin/users/") and path.endswith("/communications"):
                    require(history, "only_owned_account_history_targets_required")
                if history:
                    target = next(key for key, value in paths.items() if value == path)
                    pairs = list(request.query_params.multi_items())
                    require(len(pairs) == len(dict(pairs)) and set(dict(pairs)) <= {"limit", "offset"}, "bounded_history_query_keys_required")
                    limit, offset = request.query_params.get("limit", "25"), request.query_params.get("offset", "0")
                    require(limit.isdigit() and offset.isdigit() and 1 <= int(limit) <= 100 and 0 <= int(offset) <= 10000,
                            "bounded_history_paging_required")
                    if expected_status is None:
                        expected_status = 401 if actor is None else 403 if actor != "admin" else 404 if target == "unknown" else 422 if target == "invalid" else 200
                    starts, chunks = [], []
                    async def capture(message):
                        if message["type"] == "http.response.start":
                            require(message["status"] == expected_status and dict(message["headers"]).get(b"cache-control") == b"no-store",
                                    "actual_private_history_status_required")
                            starts.append(message["status"])
                        elif message["type"] == "http.response.body":
                            chunks.append(message.get("body", b""))
                            require(sum(map(len, chunks)) <= 65536, "bounded_actual_metadata_response_required")
                        await send(message)
                    marker = INSIDE_HISTORY.set(True)
                    try:
                        await app(scope, receive, capture)
                    finally:
                        INSIDE_HISTORY.reset(marker)
                    require(starts == [expected_status], "exact_one_history_response_required")
                    body = json.loads(b"".join(chunks))
                    if expected_status == 200:
                        require(normalized(body) == normalized(response_expected(facts, target, int(limit), int(offset))),
                                "actual_metadata_fields_order_paging_required")
                        observed["pages"][(target, int(limit), int(offset))] += 1
                    require(not any(marker in canonical(body) for marker in facts["private_markers"]),
                            "no_payload_recipient_provider_id_projection_required")
                    if label is not None:
                        observed["boundaries"][(label, expected_status)] += 1
                    observed["history_reads"] += 1
                    return
                return await app(scope, receive, send)
            raw = await request.body()
            if method == "POST" and path == "/auth/login":
                require(len(raw) <= 8192 and canonical(json.loads(raw)) in logins, "only_four_owned_login_bodies_required")
                login_actor = logins[canonical(json.loads(raw))]
                require(observed["logins"][login_actor] < 2, "at_most_two_logins_each_actor_required")
                async def capture_login(message):
                    if message["type"] == "http.response.start":
                        require(message["status"] == 200, "actual_login_without_limiter_bypass_required")
                        observed["logins"][login_actor] += 1
                    await send(message)
                return await app(scope, base.replay_body(raw, receive), capture_login)
            observed["refused"] += 1
            return await JSONResponse({"detail": "owned_read_only_scope_refused"}, status_code=405)(scope, base.replay_body(raw, receive), send)
        except Exception as error:
            if observed["guard_failure"] is None:
                observed["guard_failure"] = True
                diagnostic("SIGNUP_HISTORY_FIXTURE_GUARD", error, phase)
            raise
    return handle


def browser_mode(native, base, application, database, url):
    global STAGE
    base.configure(application, url)
    catalog, retained = base.frozen_helpers()
    server_start = catalog.query(database, "SELECT pg_postmaster_start_time()")
    owned, observed = [], {"logins": Counter(), "boundaries": Counter(), "pages": Counter(), "refused": 0,
                           "history_reads": 0, "payload_queries": 0, "safe_job_queries": 0, "guard_failure": None}
    patches = provider_patches()
    try:
        for item in patches:
            item.start()
        captured = StringIO()
        with redirect_stdout(captured), redirect_stderr(captured):
            tables, accounts, identities, facts = seed(native, base, catalog, retained, database, url)
        require(len(captured.getvalue().encode()) <= 2 * 1024 * 1024, "bounded_seed_output_required")
        from database import Base, engine
        from main import app
        from sqlalchemy import event
        import uvicorn
        require(set(Base.metadata.tables) == set(tables) - {"alembic_version"} and len(Base.metadata.tables) == 105,
                "actual_current105_model_table_set_required")
        baseline, schema, secure = catalog.snapshot(database, tables), catalog.catalog(database, tables), security(catalog, database)
        def inspect_select(_connection, _cursor, statement, _parameters, _context, _executemany):
            if INSIDE_HISTORY.get() and statement.lstrip().lower().startswith("select") and "lead_delivery_jobs" in statement.lower():
                if re.search(r"\bpayload(?:_sha256)?\b", statement.lower()):
                    observed["payload_queries"] += 1
                    raise Failure("private_payload_select_forbidden")
                observed["safe_job_queries"] += 1
        event.listen(engine, "before_cursor_execute", inspect_select)
        base.write_owned(FACTS_FILE, (canonical({"scope": "signup-email-history-only", "accounts": accounts, "facts": facts}) + "\n").encode(), owned)
        server = uvicorn.Server(uvicorn.Config(confined(app, base, accounts, identities, facts, observed),
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
        finally:
            event.remove(engine, "before_cursor_execute", inspect_select)
            for number, handler in handlers.items():
                signal.signal(number, handler)
        STAGE = "final_preservation"
        require(not observed["guard_failure"] and not observed["refused"] and not ATTEMPTS, "no_guard_mutation_or_provider_attempt_required")
        require(observed["logins"] == Counter({actor: 2 for actor in ROLES}), "exact_eight_real_logins_required")
        expected = Counter({(locale + "_" + label, value[2]): 1 for locale in ("en", "es") for label, value in BOUNDARIES.items()})
        require(observed["boundaries"] == expected, "all_sixteen_actual_boundary_reads_required")
        require(all(observed["pages"][("buyer", 10, offset)] >= 2 for offset in (0, 10))
                and all(observed["pages"][("buyer", 2, offset)] >= 2 for offset in range(0, 11, 2))
                and observed["pages"][("owner", 10, 0)] >= 2, "real_ui10_1_and_api2_pages_both_locales_required")
        require(observed["safe_job_queries"] >= 2 and observed["payload_queries"] == 0, "actual_payload_free_history_selects_required")
        require(catalog.snapshot(database, tables) == baseline and catalog.catalog(database, tables) == schema
                and security(catalog, database) == secure, "all106_rows_sequences_catalog_security_unchanged_required")
        require(catalog.query(database, "SELECT version_num FROM alembic_version") == [(HEAD,)]
                and catalog.query(database, "SELECT pg_postmaster_start_time()") == server_start,
                "current_head_and_server_start_unchanged_required")
        require(all(catalog.query(database, "SELECT count(*) FROM public." + name) == [(0,)] for name in native.INBOX), "all7_inbox_empty_required")
        native.privacy(catalog, database)
        base.verified_checkout()
    finally:
        for item in reversed(patches):
            item.stop()
        for path, device, inode in reversed(owned):
            if path.exists() or path.is_symlink():
                info = path.lstat()
                require(stat.S_ISREG(info.st_mode) and not path.is_symlink() and (info.st_dev, info.st_ino) == (device, inode), "owned_private_file_inode_required")
                path.unlink()
        require(all(not path.exists() and not path.is_symlink() for path, _, _ in owned), "private_facts_absence_required")
    require(len(owned) == 1, "exact_one_private_facts_file_removed_required")
    print("SIGNUP_HISTORY_FIXTURE_RESULT " + canonical({"status": "PASS", "schema_tables": 106, "model_tables": 105,
        "all106_rows_sequences_catalog_unchanged": True, "all_function_acl_roles_sequences_unchanged": True,
        "genuine_previous99_to_d8_to_e8": True, "current_head_unchanged": True, "server_start_unchanged": True,
        "private_rls_acl": True, "protected_owner_contacts_unchanged": 82, "private_profiles_unchanged": 46,
        "contacts_unchanged": 85, "sources_unchanged": 86, "users_unchanged": 4, "listings_unchanged": 1,
        "original_accepted_jobs_unchanged": 9, "signup_jobs_unchanged": 11, "all_delivery_jobs_unchanged": 20,
        "cms_version_unchanged": 7, "floorplans_unchanged": 2, "actual_access_boundaries": 16,
        "actual_logins": 8, "ui_mutation_requests": 0, "inbox_writes": 0, "history_payload_selects": 0,
        "actual_ui_pages": [10, 1], "actual_api_page_limit": 2, "private_files_removed": 1,
        "unexpected_mutations": 0, "external_provider_attempts": 0}), flush=True)


def run():
    global STAGE
    require(len(sys.argv) == 2 and sys.argv[1] in {"native", "browser"}, "explicit_native_or_browser_mode_required")
    mode = sys.argv[1]
    logging.disable(logging.CRITICAL)
    inventory()
    native, base = helpers()
    application = base.verified_checkout()
    database, url = claim(mode)
    if mode == "native":
        native_mode(native, base, application, database, url)
    else:
        browser_mode(native, base, application, database, url)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        diagnostic("SIGNUP_HISTORY_FIXTURE_FAILURE", error)
        raise SystemExit(1) from None
