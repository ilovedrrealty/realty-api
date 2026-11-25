"""Exact current-source recovery/regression suites in one marked loopback DB."""
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

COMMIT = "07b2b7c762b129c5b6ea978299ba69e528e3189e"
TREE = "e51495372b2fefd7223a3dd227af1e505b57fa7f"
HEAD = "c4e2a7319b60"
ACCEPTED = Path(__file__).resolve().parent.parent / "social-groups-browser/serve_social_groups_fixture.py"
ACCEPTED_SHA = "e0c5f0971c7c13ed47063907f01fa6ef01590b00fa2855eb1ecb16a38b99463e"
GROUPS = ("network_groups", "network_group_memberships", "network_group_invite_links", "network_group_posts",
          "network_group_comments", "network_group_media", "network_group_actions")
SUITES = (("test_database_connection_options", "DatabaseConnectionOptionsTests", "options", 4),
          ("test_database_pool_recovery", "DatabasePoolRecoveryTests", "recovery", 3),
          ("test_lead_delivery", "LeadDeliveryTests", "delivery", 53),
          ("test_customer_messages", "CustomerMessageTests", "customer_copy", 7))
EXPECTED = {"status": "PASS", "tests": 68, "source_tests": 67, "pure_options_tests": 4,
    "owned_recovery_tests": 3, "delivery_regression_tests": 53, "customer_copy_tests": 7,
    "owned_handshake_cases": 1, "schema_tables": 99, "modeled_tables": 98,
    "owner_contacts_unchanged": 82, "private_profiles_unchanged": 46, "accepted_jobs_unchanged": 9,
    "cms_version_unchanged": 7, "floorplans_unchanged": 2,
    "all_rows_sequences_catalog_unchanged": True, "owned_schemas_removed": True,
    "external_provider_attempts": 0, "tcp_query_deadline_claimed": False}
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
        and sha256(ACCEPTED.read_bytes()).hexdigest() == ACCEPTED_SHA, "accepted_fixture_bytes_required")
    spec = spec_from_file_location("accepted_resilience_fixture_helpers", ACCEPTED)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.APP_COMMIT, module.APP_TREE = COMMIT, TREE
    return module


def refuse(*args, **kwargs):
    ATTEMPTS.append(1)
    raise RuntimeError("external_transport_refused")


def inventory(application, module, classname, count):
    tree = ast.parse((application / "tests" / (module + ".py")).read_text())
    classes = {node.name: {item.name for item in node.body if isinstance(item, ast.FunctionDef)
        and item.name.startswith("test_")} for node in tree.body if isinstance(node, ast.ClassDef)}
    require(set(classes) == {classname} and len(classes[classname]) == count, "actual_source_inventory_required")
    return classes[classname]


def suite(application, module_name, classname, label, count):
    methods = inventory(application, module_name, classname, count)
    __import__(module_name)
    module = sys.modules[module_name]
    captured = StringIO()
    result = unittest.TestResult()
    with redirect_stdout(captured), redirect_stderr(captured):
        unittest.defaultTestLoader.loadTestsFromTestCase(getattr(module, classname)).run(result)
    print("RESILIENCE_NATIVE_COUNTS " + canonical({"suite": label, "tests": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
        "provider_attempts": len(ATTEMPTS)}), flush=True)
    for case, _ in result.failures + result.errors:
        method = getattr(case, "_testMethodName", "")
        print("RESILIENCE_NATIVE_FAILED_METHOD " + label + " " +
              (method if method in methods else "fixture_setup_or_cleanup"), flush=True)
    require(len(captured.getvalue().encode()) <= 2 * 1024 * 1024, "bounded_native_output_required")
    require(result.testsRun == count and result.wasSuccessful() and not result.skipped
        and not result.expectedFailures and not result.unexpectedSuccesses and not ATTEMPTS,
        "all_exact_native_cases_required")


def handshake():
    result = subprocess.run(["/usr/local/bin/python", "-I", "-B", str(Path(__file__).with_name("blackhole-connect.py"))],
        env=dict(os.environ), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
    require(result.returncode == 0 and len(result.stdout) <= 2048 and not result.stderr,
        "bounded_handshake_child_required")
    lines = result.stdout.splitlines()
    require(len(lines) == 1 and lines[0].startswith(b"RESILIENCE_BLACKHOLE_RESULT "), "one_handshake_receipt_required")
    report = json.loads(lines[0].split(b" ", 1)[1])
    expected = {"status": "PASS", "case": "owned_startup_handshake_timeout", "connect_timeout_seconds": 10,
        "accepted_connections": 1, "startup_seen": True, "server_thread_joined": True,
        "application_queries": 0, "external_provider_attempts": 0}
    require(set(report) == set(expected) | {"elapsed_ms"} and type(report["elapsed_ms"]) is int
        and 8000 <= report["elapsed_ms"] <= 15000 and all(report.get(k) == v and type(report.get(k)) is type(v)
        for k, v in expected.items()), "actual_handshake_timeout_and_thread_cleanup_required")
    print("RESILIENCE_BLACKHOLE_RESULT " + canonical(report), flush=True)


def run():
    global STAGE
    logging.disable(logging.CRITICAL)
    import importlib.metadata as metadata
    baseline = json.loads(Path("/opt/resilience-native/baseline-inventory.json").read_text())
    actual = {re.sub(r"[-_.]+", "-", item.metadata["Name"].lower()): item.version for item in metadata.distributions()}
    require(actual == {**baseline, "openpyxl": "3.1.5", "defusedxml": "0.7.1", "et-xmlfile": "2.0.0"},
        "unchanged_dependency_inventory_required")
    base = helpers()
    application = base.verified_checkout()
    database, url = base.claim_database()
    base.configure(application, url)
    # Original native regression suite uses this existing explicit trusted origin.
    os.environ["ALLOWED_ORIGINS"] = "http://localhost:5174"
    os.environ["SEARCH_FILTER_TEST_DATABASE_URL"] = url
    catalog, retained = base.frozen_helpers()
    import httpx, requests, resend
    captured = StringIO()
    with (patch.object(httpx.HTTPTransport, "handle_request", refuse),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse),
        patch.object(requests.Session, "send", refuse), patch.object(urllib_request, "urlopen", refuse),
        patch.object(resend.Emails, "send", refuse), patch.object(smtplib.SMTP, "connect", refuse),
        patch.object(smtplib.SMTP_SSL, "connect", refuse)):
        STAGE = "seed"
        with redirect_stdout(captured), redirect_stderr(captured):
            import bootstrap_db
            from alembic import command
            from alembic.config import Config
            from database import Base
            bootstrap_db.main()
            command.upgrade(Config("alembic.ini"), "head")
            require(retained.seed_old() == {"users": 4, "leads": 3, "owner_contacts": 82,
                "contacts": 85, "sources": 86, "hidden_profiles": 46, "accepted_jobs": 9,
                "worker_rows": 1, "cms_version": 7, "floorplan_rows": 2}, "accepted_retained_seed_required")
        tables = tuple(sorted(catalog.OLD_TABLES + GROUPS))
        require(len(Base.metadata.tables) == 98 and len(tables) == 99 and catalog.table_names(database) == tables
            and catalog.query(database, "SELECT version_num FROM alembic_version") == [(HEAD,)], "exact_99_head_required")
        before, schema = catalog.snapshot(database, tables), catalog.catalog(database, tables)
        server_start = catalog.query(database, "SELECT pg_postmaster_start_time()")
        sys.path.insert(0, str(application / "tests"))
        for module, classname, label, count in SUITES:
            STAGE = label
            suite(application, module, classname, label, count)
        STAGE = "owned_handshake"
        handshake()
        STAGE = "preservation"
        require(catalog.table_names(database) == tables and catalog.catalog(database, tables) == schema
            and catalog.snapshot(database, tables) == before and catalog.query(database,
            "SELECT version_num FROM alembic_version") == [(HEAD,)], "all_99_rows_catalog_sequences_unchanged_required")
        require(catalog.query(database, "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'pool_recovery_test_%'"
            " OR nspname LIKE 'lead_delivery_test_%'") == [], "all_owned_test_schemas_removed_required")
        require(catalog.query(database, "SELECT pg_postmaster_start_time()") == server_start,
            "unchanged_postmaster_start_required")
        require(not ATTEMPTS and len(captured.getvalue().encode()) <= 2 * 1024 * 1024, "zero_provider_bounded_capture_required")
    print("RESILIENCE_NATIVE_RESULT " + canonical(EXPECTED), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        name = type(error).__name__
        print("RESILIENCE_NATIVE_FAILURE " + canonical({"stage": STAGE,
            "error_type": name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name) else "UnhandledError",
            "reason": error.args[0] if isinstance(error, Failure) else "external_failure"}), flush=True)
        raise SystemExit(1) from None
