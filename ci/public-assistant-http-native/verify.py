"""Focused18 actual HTTP/model-schema PG cases; no lifespan/provider/migration/UI proof."""
import ast
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
import json
import logging
import os
from pathlib import Path
import re
import socket
import sys
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parent
PLAN = json.loads((TOOLS / "PLAN.json").read_bytes())
STAGE = "source"
PHASES = {"source", "claim", "inventory", "suite", "cleanup"}
ERRORS = {"Failure", "AssertionError", "RuntimeError", "ValueError", "TypeError", "KeyError",
          "TimeoutExpired", "OperationalError", "ProgrammingError", "IntegrityError", "other"}
PG_CONNECTIONS, LOCAL_SOCKETS, EXTERNAL_SOCKETS = [], [], []
HTTP_DISPATCHES, LIFESPAN_ENTRIES = [], []


class Failure(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Failure(reason)


def emit(kind, value):
    print("PUBLIC_ASSISTANT_HTTP_NATIVE_" + kind + " " + json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)


def load(path, name):
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def helpers():
    from importlib.metadata import version
    for relative, digest in PLAN["helper_pins"].items():
        path = TOOLS.parent / relative.removeprefix("ci/")
        require(relative.startswith("ci/") and path.is_file() and not path.is_symlink()
                and os.statvfs(path).f_flag & os.ST_RDONLY and sha256(path.read_bytes()).hexdigest() == digest,
                "exact_readonly_helper_required")
    legacy = load(TOOLS.parent / "assignment-notifications-native/verify.py", "public_http_accepted_helpers")
    legacy.inventory()
    require(version("pydantic") == PLAN["runtime"]["pydantic"], "exact_project_pydantic_required")
    require(version("httpx") == PLAN["runtime"]["httpx"], "exact_project_httpx_required")
    _, base = legacy.helpers(PLAN["application"]["commit"], PLAN["application"]["tree"])
    return legacy, base, base.verified_checkout()


def claim(base):
    global STAGE
    STAGE = "claim"
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    require(re.fullmatch("[a-f0-9]{32}", nonce), "owned_nonce_required")
    database = "ildr_platform_test_20261025"
    marker = "ildr-public-http-ci:" + nonce
    catalog, _ = base.frozen_helpers()
    rows = catalog.query(database, """SELECT current_database(),host(inet_server_addr()),inet_server_port(),
        current_setting('server_version_num')::int,shobj_description(oid,'pg_database'),
        NOT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),
        NOT EXISTS(SELECT FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg_%%')
        FROM pg_database WHERE datname=current_database()""")
    require(rows == [(database, "127.0.0.1", 55449, 170006, marker, True, True)],
            "fresh_marked_loopback_database_required")
    return catalog, database, "postgresql+psycopg2://postgres@127.0.0.1:55449/" + database


def connection_guards(database):
    import psycopg2
    from psycopg2.extensions import parse_dsn
    actual_pg = psycopg2.connect
    actual_connect, actual_connect_ex = socket.socket.connect, socket.socket.connect_ex
    actual_create, actual_resolve = socket.create_connection, socket.getaddrinfo

    def endpoint(address):
        good = (type(address) is tuple and len(address) >= 2 and address[0] == "127.0.0.1"
                and type(address[1]) is int and address[1] == 55449)
        if not good:
            EXTERNAL_SOCKETS.append(1)
            raise Failure("external_socket_refused_required")
        LOCAL_SOCKETS.append(1)

    def connect(sock, address):
        endpoint(address)
        return actual_connect(sock, address)

    def connect_ex(sock, address):
        endpoint(address)
        return actual_connect_ex(sock, address)

    def create(address, *args, **kwargs):
        endpoint(address)
        return actual_create(address, *args, **kwargs)

    def resolve(host, port, *args, **kwargs):
        endpoint((host, port))
        return actual_resolve(host, port, *args, **kwargs)

    def pg(dsn=None, *args, **kwargs):
        params = dict(parse_dsn(dsn or ""))
        params.update({k: v for k, v in kwargs.items() if k not in {"connection_factory", "cursor_factory", "async_", "async"}})
        good = (not params.get("service") and not params.get("hostaddr") and params.get("host") == "127.0.0.1"
                and str(params.get("port")) == "55449" and params.get("dbname", params.get("database")) == database
                and params.get("user") == "postgres" and params.get("password") in (None, ""))
        if not good:
            EXTERNAL_SOCKETS.append(1)
        require(good, "only_owned_loopback_pg_required")
        kwargs.setdefault("connect_timeout", 4)
        connection = actual_pg(dsn, *args, **kwargs)
        PG_CONNECTIONS.append(1)
        return connection

    return (patch.object(psycopg2, "connect", pg), patch.object(socket.socket, "connect", connect),
            patch.object(socket.socket, "connect_ex", connect_ex), patch.object(socket, "create_connection", create),
            patch.object(socket, "getaddrinfo", resolve))


def public_objects(catalog, database):
    return catalog.query(database, """SELECT c.relname,c.relkind,c.relpersistence,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname,c.relkind""")


def run():
    global STAGE
    logging.disable(logging.CRITICAL)
    require(len(sys.argv) == 2 and sys.argv[1] == "http", "explicit_mode_required")
    legacy, base, application = helpers()
    catalog, database, url = claim(base)
    base.configure(application, url)
    os.environ["ALLOWED_ORIGINS"] = "http://testserver"
    os.environ["SEARCH_FILTER_TEST_DATABASE_URL"] = url
    start = catalog.query(database, "SELECT pg_postmaster_start_time()")
    schemas = catalog.query(database, "SELECT nspname FROM pg_namespace ORDER BY nspname")
    definitions, sequences, access = catalog.catalog(database, ("users",)), catalog.snapshot(database, ()), legacy.security(catalog, database)
    objects = public_objects(catalog, database)
    spec = PLAN["suites"]["http"]
    filename = application / spec["file"]
    require(sha256(filename.read_bytes()).hexdigest() == spec["sha256"], "exact_suite_source_required")
    nodes = [item for item in ast.parse(filename.read_bytes()).body if isinstance(item, ast.ClassDef) and item.name == spec["class"]]
    require(len(nodes) == 1 and [item.name for item in nodes[0].body if isinstance(item, ast.FunctionDef)
            and item.name.startswith("test_")] == spec["methods"], "whole_source_method_inventory_required")
    methods = spec["methods"]
    require(len(methods) == len(set(methods)) == spec["count"] == 18, "exact18_methods_required")
    captured = StringIO()
    STAGE = "suite"
    with ExitStack() as stack, redirect_stdout(captured), redirect_stderr(captured):
        for guard in (*legacy.provider_patches(), *connection_guards(database)):
            stack.enter_context(guard)
        module = load(filename, "owned_public_assistant_http")
        from database import Base
        require(len(Base.metadata.tables) == 107, "exact_current107_models_required")
        require("main" in sys.modules and module.app is sys.modules["main"].app, "actual_main_app_required")
        actual_app = module.app
        async def observed_app(scope, receive, send):
            if scope["type"] == "lifespan":
                LIFESPAN_ENTRIES.append(1)
                raise Failure("application_lifespan_forbidden_required")
            require(scope["type"] == "http" and scope["method"] in ("POST", "OPTIONS")
                    and scope["path"] in {"/public/assistant/" + name for name in
                        ("session", "turn", "contact-review", "contact-confirm")}, "finite_testclient_http_scope_required")
            HTTP_DISPATCHES.append(1)
            return await actual_app(scope, receive, send)
        stack.enter_context(patch.object(module, "app", observed_app))
        case = getattr(module, spec["class"])
        require(getattr(case, "__unittest_skip__", False) is False and all(
            callable(getattr(case, name, None)) and getattr(getattr(case, name), "__unittest_skip__", False) is False
            for name in methods), "exact_selected_methods_no_skip_required")
        result = unittest.TestResult()
        unittest.TestSuite(case(name) for name in methods).run(result)
    emit("COUNTS", {"suite": "http", "expected": 18, "run": result.testsRun,
                    "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped)})
    for name in sorted({getattr(item, "_testMethodName", "fixture_setup_or_cleanup") for item, _ in result.failures + result.errors}):
        emit("FAILED_METHOD", {"method": name if name in methods else "fixture_setup_or_cleanup"})
    require(result.testsRun == 18 and result.wasSuccessful() and not result.skipped and not result.expectedFailures
            and not result.unexpectedSuccesses and len(captured.getvalue().encode()) <= 2 * 1024 * 1024,
            "all_exact18_cases_required")
    require(not legacy.EXTERNAL and not EXTERNAL_SOCKETS and legacy.MOCK_CALLS == ["async", "async"],
            "exact2_async_mock_zero_live_provider_socket_required")
    require(not LIFESPAN_ENTRIES and 18 <= len(HTTP_DISPATCHES) <= 256,
            "actual_bounded_http_no_lifespan_required")
    require(0 < len(PG_CONNECTIONS) <= 1024 and len(LOCAL_SOCKETS) <= 1024, "positive_bounded_owned_pg_connections_required")
    STAGE = "cleanup"
    require(catalog.table_names(database) == () and catalog.query(database, "SELECT nspname FROM pg_namespace ORDER BY nspname") == schemas
            and catalog.catalog(database, ("users",)) == definitions and catalog.snapshot(database, ()) == sequences
            and legacy.security(catalog, database) == access and public_objects(catalog, database) == objects
            and catalog.query(database, "SELECT pg_postmaster_start_time()") == start,
            "owned_schema_public_security_server_preserved_required")
    base.verified_checkout()
    emit("RESULT", {"status": "PASS", "mode": "http", "tests": 18, "exact_current_source": True,
        "model_created_test_schema": True, "actual_registered_models": 107, "actual_main_imported": True,
        "inprocess_actual_asgi_requests": len(HTTP_DISPATCHES), "app_lifespan_entries": len(LIFESPAN_ENTRIES),
        "origin_csrf_session_assertions_passed": True, "explicit_confirm_replay_unknown_assertions_passed": True,
        "actual_rate_worker_bounds_assertions_passed": True, "actual_transport_public_reader_projection_assertions_passed": True,
        "owned_pg_connections": len(PG_CONNECTIONS), "loopback_socket_calls": len(LOCAL_SOCKETS),
        "all_owned_schema_cleanup": True, "public_catalog_sequences_security_unchanged": True,
        "server_start_unchanged": True, "external_provider_attempts": 0, "async_mock_calls": 2, "sync_mock_calls": 0,
        "external_socket_attempts": 0, "genuine_migration_claim": False, "real_http_server_claim": False,
        "model_invocation_claim": False, "customer_UI_confirmation_claim": False, "delivery_claim": False})


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        kind = type(error).__name__ if type(error).__name__ in ERRORS else "other"
        state = getattr(getattr(error, "orig", error), "pgcode", None)
        emit("FAILURE", {"stage": STAGE if STAGE in PHASES else "source", "error_type": kind,
             "reason": str(error) if isinstance(error, Failure) else "unexpected_exception",
             "sqlstate": state if isinstance(state, str) and re.fullmatch("[A-Z0-9]{5}", state) else None})
        raise SystemExit(1) from None
