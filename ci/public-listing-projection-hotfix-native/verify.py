"""Six fresh, source-pinned focused modes; synthetic loopback schemas only."""
import ast
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from copy import deepcopy
from decimal import Decimal
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
from uuid import UUID

TOOLS = Path(__file__).resolve().parent
PLAN = json.loads((TOOLS / "PLAN.json").read_bytes())
STAGE = "source"
PHASES = {"source", "claim", "inventory", "suite", "canary", "cleanup"}
ERRORS = {"Failure", "AssertionError", "RuntimeError", "ValueError", "TypeError", "KeyError",
          "TimeoutExpired", "OperationalError", "ProgrammingError", "IntegrityError", "other"}


class Failure(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Failure(reason)


def emit(kind, value):
    print("PUBLIC_LISTING_NATIVE_" + kind + " " + json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)


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
    legacy = load(TOOLS.parent / "assignment-notifications-native/verify.py", "listing_native_accepted_helpers")
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
    marker = "ildr-public-listing-" + mode + "-ci:" + nonce
    catalog, _ = base.frozen_helpers()
    rows = catalog.query(database, """SELECT current_database(),host(inet_server_addr()),inet_server_port(),
        current_setting('server_version_num')::int,shobj_description(oid,'pg_database'),
        NOT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),
        NOT EXISTS(SELECT FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg_%%')
        FROM pg_database WHERE datname=current_database()""")
    require(rows == [(database, "127.0.0.1", 55449, 170006, marker, True, True)],
            "fresh_marked_loopback_database_required")
    return catalog, database, "postgresql+psycopg2://postgres@127.0.0.1:55449/" + database


def canary_case(module):
    """Real JWT/ASGI routes over the unchanged owned LocationReview fixture."""
    class PublicRouteCanary(module.LocationReviewTests):
        def test_anonymous_projection_and_authorized_private_retention(self):
            global STAGE
            STAGE = "canary"
            identity = self.listing_ids[0]
            stored = {
                "owner_paypal_email": "private-payee@example.invalid",
                "co_listing_notes": "PRIVATE_BROKER_NOTES_CANARY",
                "co_listing_commission_split": Decimal("42"),
                "co_listing_status": "PRIVATE_NEGOTIATION_CANARY",
                "co_listing_agreement_accepted": True,
                "co_listing_agreement_url": "https://private.example.invalid/agreement.pdf",
                "rejection_reason": "PRIVATE_REVIEW_CANARY",
                "source_ref": "PRIVATE_IMPORT_CANARY",
                "submitted_by": self.ids["owner"], "owner_id": self.ids["owner"],
                "assigned_realtor_id": self.ids["realtor"],
            }
            with module.Session(self.engine) as db:
                row = db.get(module.Listing, UUID(identity))
                for key, value in stored.items():
                    setattr(row, key, value)
                row.co_listing_enabled = True
                row.co_listing_agent_email = "published-business@example.invalid"
                db.commit()
                before = deepcopy({column.key: getattr(row, column.key) for column in row.__table__.columns})
                owner_email = db.get(module.User, self.ids["owner"]).email
            private_keys = set(PLAN["removed_public_keys"])
            secret_strings = [value for value in stored.values() if isinstance(value, str)] + [owner_email]
            expected_public = set(PLAN["public_keys"])
            for path in ("/listings", "/listings/deal", "/listings/" + identity):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers.get("cache-control"), "no-store")
                data = response.json()
                items = data.get("items", [data]) if isinstance(data, dict) else data
                item = next(item for item in items if item["id"] == identity)
                self.assertEqual(set(item), expected_public | ({"room_photo_tour"} if path.endswith(identity) else set()))
                self.assertFalse(private_keys & item.keys())
                self.assertIsNone(item["submitted_by_email"])
                self.assertTrue(all(value not in json.dumps(item) for value in secret_strings))
                self.assertEqual(item["co_listing_agent_email"], "published-business@example.invalid")
                self.assertEqual(Decimal(str(item["price"])), Decimal("199000"))
                self.assertEqual(item["currency"], "USD")
            for path, actor in (("/listings/mine", "owner"), ("/listings/mine/page", "owner"),
                                ("/admin/listings", "admin"), ("/admin/listings/" + identity, "admin")):
                response = self.client.get(path, headers=self.headers[actor])
                self.assertEqual(response.status_code, 200)
                data = response.json()
                items = data.get("items", [data]) if isinstance(data, dict) else data
                item = next(item for item in items if item["id"] == identity)
                self.assertTrue(private_keys <= item.keys())
                for key, value in stored.items():
                    if isinstance(value, Decimal):
                        self.assertEqual(Decimal(str(item[key])), value)
                    else:
                        self.assertEqual(item[key], str(value) if isinstance(value, UUID) else value)
                self.assertEqual(item["submitted_by_email"], owner_email if actor == "admin" else None)
            for path, actor, status in (("/admin/listings", None, 401),
                                        ("/admin/listings/" + identity, "owner", 403),
                                        ("/listings/mine", "buyer", 403)):
                response = self.client.get(path, headers=self.headers.get(actor, {}))
                self.assertEqual(response.status_code, status)
                self.assertTrue(all(value not in response.text for value in secret_strings))
            with module.Session(self.engine) as db:
                after = db.get(module.Listing, UUID(identity))
                self.assertEqual({column.key: getattr(after, column.key) for column in after.__table__.columns}, before)
    return PublicRouteCanary


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
    STAGE = "suite"
    with ExitStack() as stack, redirect_stdout(captured), redirect_stderr(captured):
        for item in legacy.provider_patches():
            stack.enter_context(item)
        module = load(filename, "owned_listing_" + mode)
        if mode != "pure":
            import models.trade_contact
            from database import Base
            require(len(Base.metadata.tables) == 98, "exact_current98_models_required")
        case = canary_case(module) if mode == "canary" else getattr(module, spec["class"])
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
    if mode == "pure":
        require(all(name not in sys.modules for name in ("config", "database", "main", "routes.listings")),
                "pure_cases_no_application_configuration_import_required")
    STAGE = "cleanup"
    require(catalog.table_names(database) == () and catalog.query(database, "SELECT nspname FROM pg_namespace ORDER BY nspname") == schemas
            and catalog.catalog(database, ("users",)) == definitions and catalog.snapshot(database, ()) == sequences
            and legacy.security(catalog, database) == access and catalog.query(database, "SELECT pg_postmaster_start_time()") == start,
            "owned_schema_public_security_server_preserved_required")
    base.verified_checkout()
    emit("RESULT", {"status": "PASS", "mode": mode, "tests": spec["count"], "exact_current_source": True,
        "model_created_test_schema": mode != "pure", "pure_dto_only": mode == "pure", "genuine108_claim": False,
        "all_owned_schema_cleanup": True, "public_catalog_sequences_security_unchanged": True,
        "server_start_unchanged": True, "public_canary_routes": 3 if mode == "canary" else 0,
        "private_retention_routes": 4 if mode == "canary" else 0, "private_denial_routes": 3 if mode == "canary" else 0,
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
