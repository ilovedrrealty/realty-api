"""Four source-pinned dependency modes; only owned synthetic fixture schemas."""
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
import sys
import unittest

TOOLS = Path(__file__).resolve().parent
PLAN = json.loads((TOOLS / "PLAN.json").read_bytes())
STAGE = "source"
PHASES = {"source", "claim", "inventory", "suite", "cleanup"}
ERRORS = {"Failure", "AssertionError", "RuntimeError", "ValueError", "TypeError", "KeyError",
          "TimeoutExpired", "OperationalError", "ProgrammingError", "IntegrityError", "other"}


class Failure(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Failure(reason)


def emit(kind, value):
    print("ASSISTANT_DEPENDENCIES_NATIVE_" + kind + " " + json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)


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
    legacy = load(TOOLS.parent / "assignment-notifications-native/verify.py", "assistant_dependency_accepted_helpers")
    legacy.inventory()
    require(version("pydantic") == PLAN["runtime"]["pydantic"], "exact_project_pydantic_required")
    pin = PLAN["application"]
    _, base = legacy.helpers(pin["commit"], pin["tree"])
    application = base.verified_checkout()
    return legacy, base, application


def claim(base, mode):
    global STAGE
    STAGE = "claim"
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    require(re.fullmatch("[a-f0-9]{32}", nonce), "owned_nonce_required")
    database = "ildr_platform_test_20261025"
    marker = "ildr-assistant-dependencies-" + mode + "-ci:" + nonce
    catalog, _ = base.frozen_helpers()
    rows = catalog.query(database, """SELECT current_database(),host(inet_server_addr()),inet_server_port(),
        current_setting('server_version_num')::int,shobj_description(oid,'pg_database'),
        NOT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),
        NOT EXISTS(SELECT FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg_%%')
        FROM pg_database WHERE datname=current_database()""")
    require(rows == [(database, "127.0.0.1", 55449, 170006, marker, True, True)],
            "fresh_marked_loopback_database_required")
    return catalog, database, "postgresql+psycopg2://postgres@127.0.0.1:55449/" + database


def run():
    global STAGE
    logging.disable(logging.CRITICAL)
    require(len(sys.argv) == 2 and sys.argv[1] in PLAN["suites"], "explicit_mode_required")
    mode = sys.argv[1]
    legacy, base, application = helpers()
    catalog, database, url = claim(base, mode)
    base.configure(application, url)
    os.environ["ALLOWED_ORIGINS"] = "http://testserver"
    os.environ["SEARCH_FILTER_TEST_DATABASE_URL"] = url
    sys.path.insert(0, str(application / "tests"))
    start = catalog.query(database, "SELECT pg_postmaster_start_time()")
    schemas = catalog.query(database, "SELECT nspname FROM pg_namespace ORDER BY nspname")
    definitions, sequences, access = catalog.catalog(database, ("users",)), catalog.snapshot(database, ()), legacy.security(catalog, database)
    spec = PLAN["suites"][mode]
    filename = application / spec["file"]
    require(sha256(filename.read_bytes()).hexdigest() == spec["sha256"], "exact_suite_source_required")
    nodes = [item for item in ast.parse(filename.read_bytes()).body if isinstance(item, ast.ClassDef) and item.name == spec["class"]]
    require(len(nodes) == 1 and [item.name for item in nodes[0].body if isinstance(item, ast.FunctionDef)
            and item.name.startswith("test_")] == spec["all_source_methods"], "whole_source_method_inventory_required")
    captured = StringIO()
    model_count = 0
    STAGE = "suite"
    with ExitStack() as stack, redirect_stdout(captured), redirect_stderr(captured):
        for item in legacy.provider_patches():
            stack.enter_context(item)
        module = load(filename, "owned_assistant_" + mode)
        if mode in {"wiring", "reads"}:
            import models.trade_contact
            from database import Base
            model_count = len(Base.metadata.tables)
            require(model_count == 107, "exact_current107_models_required")
        case = getattr(module, spec["class"])
        require(getattr(case, "__unittest_skip__", False) is False, "source_class_no_skip_required")
        methods = spec["methods"]
        require(len(methods) == len(set(methods)) == spec["count"] and all(
            callable(getattr(case, name, None)) and getattr(getattr(case, name), "__unittest_skip__", False) is False
            for name in methods), "exact_selected_methods_no_skip_required")
        suite = unittest.TestSuite(case(name) for name in methods)
        result = unittest.TestResult()
        suite.run(result)
    emit("COUNTS", {"suite": mode, "expected": spec["count"], "run": result.testsRun,
                    "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped)})
    for name in sorted({getattr(item, "_testMethodName", "fixture_setup_or_cleanup") for item, _ in result.failures + result.errors}):
        emit("FAILED_METHOD", {"method": name if name in methods else "fixture_setup_or_cleanup"})
    require(result.testsRun == spec["count"] and result.wasSuccessful() and not result.skipped
            and not result.expectedFailures and not result.unexpectedSuccesses and len(captured.getvalue().encode()) <= 2 * 1024 * 1024,
            "all_exact_focused_cases_required")
    require(not legacy.EXTERNAL and not legacy.MOCK_CALLS, "zero_provider_mock_attempts_required")
    if mode in {"facts", "help"}:
        require(all(name not in sys.modules for name in ("config", "database", "main", "routes.listings")),
                "pure_module_no_application_configuration_import_required")
    STAGE = "cleanup"
    require(catalog.table_names(database) == () and catalog.query(database, "SELECT nspname FROM pg_namespace ORDER BY nspname") == schemas
            and catalog.catalog(database, ("users",)) == definitions and catalog.snapshot(database, ()) == sequences
            and legacy.security(catalog, database) == access and catalog.query(database, "SELECT pg_postmaster_start_time()") == start,
            "owned_schema_public_security_server_preserved_required")
    base.verified_checkout()
    emit("RESULT", {"status": "PASS", "mode": mode, "tests": spec["count"], "exact_current_source": True,
        "pure_facts_cases": spec["count"] if mode == "facts" else 0,
        "wiring_unit_cases": spec["count"] if mode == "wiring" else 0,
        "owned_pg_read_cases": spec["count"] if mode == "reads" else 0,
        "pure_help_cases": spec["count"] if mode == "help" else 0,
        "selected_module_no_app_imports": mode in {"facts", "help"},
        "model_created_test_schema": mode == "reads", "actual_registered_models": model_count,
        "owned_db_bookkeeping": True, "genuine_migration_claim": False, "http_wire_claim": False,
        "model_or_auth_adapter_claim": False, "all_owned_schema_cleanup": True,
        "public_catalog_sequences_security_unchanged": True, "server_start_unchanged": True,
        "external_provider_attempts": 0, "mocked_provider_calls": 0})


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        kind = type(error).__name__ if type(error).__name__ in ERRORS else "other"
        state = getattr(getattr(error, "orig", error), "pgcode", None)
        reason = str(error) if isinstance(error, Failure) else "unexpected_exception"
        emit("FAILURE", {"stage": STAGE if STAGE in PHASES else "source", "error_type": kind, "reason": reason,
                         "sqlstate": state if isinstance(state, str) and re.fullmatch("[A-Z0-9]{5}", state) else None})
        raise SystemExit(1) from None
