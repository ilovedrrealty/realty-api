"""Hosted-only contact authorization regression in one nonce-owned database."""
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
import sys
from urllib import request as urllib_request
from unittest.mock import patch
import unittest

COMMIT = "8957b18520d15dac503e0e4a5ce4b2455135d601"
TREE = "11ab69db36eb30881e4f3086578201f584c4092d"
HEAD = "c4e2a7319b60"
ACCEPTED = Path(__file__).resolve().parent.parent / "social-groups-browser/serve_social_groups_fixture.py"
ACCEPTED_SHA = "e0c5f0971c7c13ed47063907f01fa6ef01590b00fa2855eb1ecb16a38b99463e"
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
            and sha256(ACCEPTED.read_bytes()).hexdigest() == ACCEPTED_SHA,
            "accepted_fixture_bytes_required")
    spec = spec_from_file_location("accepted_contact_fixture_helpers", ACCEPTED)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.APP_COMMIT, module.APP_TREE = COMMIT, TREE
    return module


def refuse(*args, **kwargs):
    ATTEMPTS.append(1)
    raise RuntimeError("external_transport_refused")


def preserved_contacts(before, after, table, expected):
    old, current = json.loads(before[table]), json.loads(after[table])
    old_ids = {row["id"] for row in old}
    require([row for row in current if row["id"] in old_ids] == old,
            "all_retained_contact_rows_unchanged_required")
    added = [row for row in current if row["id"] not in old_ids]
    require(len(added) == expected, "exact_synthetic_contact_additions_required")
    return added


def run():
    global STAGE
    logging.disable(logging.CRITICAL)
    import importlib.metadata as metadata
    baseline = json.loads(Path("/opt/contact-native/baseline-inventory.json").read_text())
    actual = {re.sub(r"[-_.]+", "-", item.metadata["Name"].lower()): item.version for item in metadata.distributions()}
    require(actual == {**baseline, "openpyxl": "3.1.5", "defusedxml": "0.7.1", "et-xmlfile": "2.0.0"},
            "unchanged_dependency_inventory_required")
    base = helpers()
    application = base.verified_checkout()
    database, url = base.claim_database()
    base.configure(application, url)
    # The unchanged native suite intentionally uses its original trusted origin.
    os.environ["ALLOWED_ORIGINS"] = "http://localhost:5174"
    os.environ["TRADE_CONTACT_FIXTURE"] = "yes"
    catalog, retained = base.frozen_helpers()
    source = ast.parse((application / "tests/test_admin_trade_contacts.py").read_text())
    inventory = {node.name: [item.name for item in node.body
        if isinstance(item, ast.FunctionDef) and item.name.startswith("test_")]
        for node in source.body if isinstance(node, ast.ClassDef)}
    require(set(inventory) == {"TradeContactsTest"} and len(inventory["TradeContactsTest"]) == 10
        and "test_contact_deep_link_is_admin_only_and_preserves_owner_records" in inventory["TradeContactsTest"],
        "actual_ten_native_inventory_required")
    import httpx
    import requests
    import resend
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
            bootstrap_db.main()
            command.upgrade(Config("alembic.ini"), "head")
            require(retained.seed_old() == {"users": 4, "leads": 3, "owner_contacts": 82,
                "contacts": 85, "sources": 86, "hidden_profiles": 46, "accepted_jobs": 9,
                "worker_rows": 1, "cms_version": 7, "floorplan_rows": 2}, "accepted_retained_seed_required")
        tables = catalog.table_names(database)
        require(len(tables) == 99 and catalog.query(database,
            "SELECT version_num FROM alembic_version") == [(HEAD,)], "exact_99_head_required")
        before, schema = catalog.snapshot(database, tables), catalog.catalog(database, tables)
        STAGE = "native"
        with redirect_stdout(captured), redirect_stderr(captured):
            suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_admin_trade_contacts")
            result = unittest.TestResult()
            suite.run(result)
        summary = {"tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
            "skipped": len(result.skipped), "provider_attempts": len(ATTEMPTS)}
        print("CONTACT_NATIVE_COUNTS " + canonical(summary), flush=True)
        for case, _ in result.failures + result.errors:
            name = getattr(case, "_testMethodName", "")
            if name in inventory["TradeContactsTest"]:
                print("CONTACT_NATIVE_FAILED_METHOD " + name, flush=True)
        require(len(captured.getvalue().encode()) <= 2 * 1024 * 1024, "bounded_native_output_required")
        require(summary == {"tests": 10, "failures": 0, "errors": 0, "skipped": 0,
            "provider_attempts": 0}, "actual_ten_native_cases_required")
        STAGE = "preservation"
        require(catalog.table_names(database) == tables and catalog.catalog(database, tables) == schema
            and catalog.query(database, "SELECT version_num FROM alembic_version") == [(HEAD,)],
            "all_99_catalog_and_head_unchanged_required")
        after = catalog.snapshot(database, tables)
        for table, value in before.items():
            if table not in {"trade_contacts", "trade_contact_sources"}:
                require(after[table] == value, "unrelated_rows_and_sequences_unchanged_required")
        new_contacts = preserved_contacts(before, after, "trade_contacts", 66)
        new_sources = preserved_contacts(before, after, "trade_contact_sources", 68)
        new_ids = {row["id"] for row in new_contacts}
        require(all(re.fullmatch(r"Synthetic contractor [0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}"
            r"(?: distinct| website| no contact| full| other subtype| wildcard decoy| mapped [0-9]{1,2}| fallback| conflict| partial)?",
            row["name"]) for row in new_contacts), "only_test_owned_contact_names_required")
        require(all(row["contact_id"] in new_ids and row["evidence"]["source_authority"]
            in {"Synthetic source", "Second directory", "Second synthetic source"} for row in new_sources),
            "only_test_owned_source_observations_required")
        require(not ATTEMPTS, "zero_provider_attempts_required")
    print("CONTACT_NATIVE_RESULT " + canonical({"status": "PASS", "tests": 10, "schema_tables": 99,
        "retained_contacts": 85, "retained_sources": 86, "owner_contacts_unchanged": 82,
        "private_profiles_unchanged": 46, "accepted_jobs_unchanged": 9, "cms_version_unchanged": 7,
        "floorplans_unchanged": 2, "new_test_contacts": 66, "new_test_sources": 68,
        "other_table_rows_and_sequences_unchanged": True, "catalog_unchanged": True,
        "external_provider_attempts": 0}), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        name = type(error).__name__
        print("CONTACT_NATIVE_FAILURE " + canonical({"stage": STAGE,
            "error_type": name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name) else "UnhandledError",
            "reason": error.args[0] if isinstance(error, Failure) else "external_failure"}), flush=True)
        raise SystemExit(1) from None
