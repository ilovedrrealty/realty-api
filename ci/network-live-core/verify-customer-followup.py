"""Verify narrow live-base communications on owned hosted Postgres."""

from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from contextlib import closing, ExitStack
import unittest
from unittest.mock import patch


APPLICATION_COMMIT = "01695530df670a94ac28f8b3bf36cc34b5b7a942"
APPLICATION_TREE = "e88ea0c57f9fac39545322532a5992d5a2927676"
MIGRATION = "b19d8e4c720a"
PRIOR_HARNESS = Path("/prior/test_cumulative_communications_acceptance.py")
PRIOR_SHA256 = "31d9792c4fc11f924af0b0ab2db3247f77eb083c1274fab94fb99c75197db60b"
FIXTURE = Path("/prior/serve_cumulative_communications_fixture.py")
FIXTURE_SHA256 = "fd7079170b9307ef4d523a3692b83bba2490bf16b7eb26036e4eb718a00edba7"
LEGACY_HARNESS = Path("/legacy/run-legacy-inquiry-tests.py")
LEGACY_SHA256 = "f29933d32dae31449fe9f56ed3e63946ed302e71fa7184a7117e8fda9a15ebfd"
WHATSAPP_URL = "https://wa.me/18096108094"
LABELS = {"en": "Message our team on WhatsApp", "es": "Escríbenos por WhatsApp"}
CONTRACTS = (
    ("customer_delivery", "CustomerDeliveryTests", 19),
    ("lead_delivery", "LeadDeliveryTests", 53),
    ("owner_leads", "OwnerLeadFeedTests", 8),
    ("property_inquiry", "PropertyInquiryTests", 10),
)

BROAD_CONTRACTS = (
    ('professionals', 'ProfessionalTests', 27),
    ('professional_catalog', 'ProfessionalCatalogTests', 7),
    ('property_network', 'PropertyNetworkRoutes', 18),
    ('property_network', 'PrivateImageContracts', 4),
    ('private_property_registry', 'PrivateRegistryRoutes', 16),
    ('private_property_registry_migration', 'PrivateRegistryMigration', 5),
    ('property_custody', 'PrivatePropertyCustody', 5),
    ('history_share', 'PrivatePropertyHistoryShares', 5),
    ('service_workflow', 'PrivateServiceWorkflow', 35),
    ('service_workflow_migration', 'PrivateServiceMigration', 5),
    ('service_visit', 'PrivateAwardedServiceWork', 5),
    ('service_conversation', 'PrivateAwardedServiceConversation', 5),
    ('service_negotiation', 'PrivateServiceNegotiationConversation', 5),
    ('service_review', 'PrivateCompletedServiceReview', 5),
    ('service_rebooking', 'PrivateCompletedServiceRebooking', 3),
    ('service_favorites', 'PrivateServiceFavorites', 4),
    ('service_referrals', 'PrivateServiceReferrals', 5),
    ('maintenance_reminders', 'PrivateMaintenanceReminders', 5),
    ('due_work_reminders', 'PrivateDueWorkReminders', 6),
    ('service_operations', 'PrivateServiceOperations', 5),
    ('property_management', 'PropertyManagementTests', 19),
    ('transaction_room', 'TransactionRoomTests', 9),
    ('transaction_documents', 'TransactionDocumentTests', 5),
    ('transaction_document_storage', 'TransactionDocumentStorageTests', 7),
    ('listing_floor_plans', 'ListingFloorPlanTests', 4),
    ('admin_trade_contacts', 'TradeContactsTest', 3),
)


def load_pinned(name, path, expected):
    if (not path.is_file() or path.is_symlink() or not os.statvfs(path).f_flag & os.ST_RDONLY
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
        raise RuntimeError("immutable_rehearsal_source_required")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def app_isolation(prior):
    prior.require(sys.platform == "linux" and sys.version_info[:2] == (3, 11) and
                  {interface.name for interface in Path("/sys/class/net").iterdir()} == {"lo"},
                  "hosted_loopback_namespace_required")
    fixture = load_pinned("accepted_customer_fixture", FIXTURE, FIXTURE_SHA256)
    prior.require((fixture.APP_COMMIT, fixture.APP_TREE, fixture.MIGRATION_HEAD) == (
        "b38d477c0e2e50f87f1f2e4cc1ceb3c707af92c5", "a53b3f829f70717069f5c637c826fb8f92c02f7c", "f4b27d6e913c"),
        "accepted_fixture_identity_changed")
    fixture.APP_COMMIT, fixture.APP_TREE, fixture.MIGRATION_HEAD = APPLICATION_COMMIT, APPLICATION_TREE, MIGRATION
    application = fixture.verified_checkout()
    prior.require(application == Path("/application") and os.statvfs(application).f_flag & os.ST_RDONLY,
                  "immutable_application_mount_required")
    confirm_disposable_database(prior)
    fixture.configure_application(application, prior.DATABASE_URL)
    os.environ.update(RESEND_API_KEY="synthetic-placeholder-only", EMAIL_FROM=prior.SENDER,
                      LEAD_DELIVERY_ENABLED="true", CUSTOMER_MESSAGES_ENABLED="true",
                      PROPERTY_INQUIRY_REQUIRE_CONSENT="true", GHL_WEBHOOK_SECRET="", LOGO_URL="")
    return fixture


def confirm_disposable_database(prior):
    """Keep the accepted fresh-database guard pinned to production Postgres 17.6."""
    import psycopg2

    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    prior.require(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL") == prior.DATABASE_URL
                  and re.fullmatch(r"[a-f0-9]{32}", nonce), "owned_database_configuration_required")
    with closing(psycopg2.connect(host="127.0.0.1", port=55449, dbname=prior.DATABASE_NAME, user="postgres",
                                 password="", connect_timeout=4)) as connection, connection.cursor() as cursor:
        cursor.execute("""
            SELECT current_database(), host(inet_server_addr()), inet_server_port(),
                   current_setting('server_version_num')::integer,
                   shobj_description(oid, 'pg_database'),
                   NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                               WHERE n.nspname='public')
            FROM pg_database WHERE datname=current_database()
        """)
        actual = cursor.fetchone()
    prior.require(actual == (prior.DATABASE_NAME, "127.0.0.1", 55449, 170006,
                             f"ildr-communications-ci:{nonce}", True),
                  "fresh_marked_postgres_17_6_database_required")


def verify_links(prior):
    old_customer = prior.customer_payload_matches
    old_owner = prior.owner_payload_matches
    customer, owner = Counter(), Counter()

    def customer_match(body, event):
        if not old_customer(body, event):
            return False
        locale, _, _, path = event.customer_copy
        primary_url = f"{prior.CUSTOMER_ORIGIN}{path}"
        text, html = body["text"], body["html"]
        label = LABELS[locale]
        prior.require(re.findall(r"https://\S+", text) == [primary_url, WHATSAPP_URL]
                      and re.findall(r'href="([^"]+)"', html) == [primary_url, WHATSAPP_URL]
                      and f"\n\n{label}: {WHATSAPP_URL}\n\n" in text
                      and f'<a href="{WHATSAPP_URL}">{label}</a>' in html,
                      "customer_followup_url_or_locale_changed")
        customer[locale] += 1
        return True

    def owner_match(body, event, origin):
        if not old_owner(body, event, origin):
            return False
        prior.require(WHATSAPP_URL not in body.get("text", "") and WHATSAPP_URL not in body.get("html", ""),
                      "customer_followup_leaked_into_team_notice")
        owner[event.kind] += 1
        return True

    prior.verified_isolation = lambda: app_isolation(prior)
    prior.customer_payload_matches = customer_match
    prior.owner_payload_matches = owner_match
    result = prior.run()
    prior.require(dict(customer) == {"en": 2, "es": 3} and sum(owner.values()) == 32,
                  "customer_or_team_acceptance_count_changed")
    result.update(customer_link_acceptances=dict(customer), team_notices_checked=sum(owner.values()),
                  exact_public_whatsapp_url=True, frontend_built=False)
    return result


def verify_candidate_contracts(prior):
    import httpx
    import requests
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from urllib import request as urllib_request

    from config import settings
    from database import engine, SessionLocal

    prior.stage = "contract_inventory"
    tests_root = Path("/application/tests")
    prior.require(tests_root.resolve(strict=True) == tests_root and os.statvfs(tests_root).f_flag & os.ST_RDONLY,
                  "immutable_contracts_required")
    sys.path.insert(0, str(tests_root))
    loader = unittest.TestLoader()
    legacy = load_pinned("accepted_legacy_contract", LEGACY_HARNESS, LEGACY_SHA256)
    unexpected_egress = []
    reports = []
    issues = []
    safe_exception_types = {
        "AssertionError", "AttributeError", "CountMismatch", "DataError", "DBAPIError",
        "ForeignKeyViolation", "ImportError", "IntegrityError", "InternalError", "KeyError",
        "ModuleNotFoundError", "NameError", "NotNullViolation", "OperationalError", "OtherException",
        "PermissionError", "ProgrammingError", "RuntimeError", "SkipTest", "TimeoutError",
        "TypeError", "UnexpectedSuccess", "ValidationError", "ValueError",
    }

    def refuse_transport(*_args, **_kwargs):
        unexpected_egress.append(True)
        raise prior.RehearsalFailure("unexpected_real_transport")

    def exception_category(trace):
        # Emit a fixed category only. Never return a traceback or exception message.
        names = re.findall(r"(?m)^([A-Za-z_][A-Za-z0-9_.]*):", trace)
        for name in reversed(names):
            short = name.rsplit(".", 1)[-1]
            if short in safe_exception_types:
                return short
        return "OtherException"

    def issue(module_name, class_name, case, kind, category):
        parent = getattr(case, "test_case", case)
        method = getattr(parent, "_testMethodName", "")
        if not isinstance(method, str) or not re.fullmatch(r"test_[a-z_]{1,180}", method):
            method = "setup_or_cleanup"
        item = {"module": module_name, "class": class_name, "method": method,
                "kind": kind, "exception_type": category}
        if item not in issues:
            issues.append(item)

    def source_module(name):
        module = importlib.import_module(f"test_{name}")
        prior.require(Path(module.__file__).resolve(strict=True) == tests_root / f"test_{name}.py",
                      "candidate_test_module_required")
        return module

    def owned_trade_suite():
        nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
        prior.require(re.fullmatch(r"[a-f0-9]{32}", nonce)
                      and engine.url == make_url(prior.DATABASE_URL)
                      and SessionLocal.kw.get("bind") is engine,
                      "owned_trade_database_binding_required")
        with engine.connect() as connection:
            actual = connection.execute(text("""SELECT current_database(),current_user,
                host(inet_server_addr()),inet_server_port(),current_setting('server_version_num')::int,
                shobj_description(oid,'pg_database'),current_schema(),
                (SELECT version_num FROM public.alembic_version)
                FROM pg_database WHERE datname=current_database()""")).one()
        prior.require(tuple(actual) == (prior.DATABASE_NAME, "postgres", "127.0.0.1", 55449, 170006,
                      f"ildr-communications-ci:{nonce}", "public", MIGRATION),
                      "owned_trade_namespace_and_head_required")
        prior.require("test_admin_trade_contacts" not in sys.modules, "trade_fixture_import_order_required")
        return loader.loadTestsFromTestCase(source_module("admin_trade_contacts").TradeContactsTest)

    def run_suite(module_name, class_name, count, build):
        prior.stage = module_name
        report = {"module": module_name, "class": class_name, "expected": count,
                  "discovered": 0, "run": 0, "failures": 0, "errors": 0, "skipped": 0,
                  "expected_failures": 0, "unexpected_successes": 0}
        try:
            suite = build()
            report["discovered"] = suite.countTestCases()
        except Exception as error:
            category = type(error).__name__
            issue(module_name, class_name, None, "load_error",
                  category if category in safe_exception_types else "OtherException")
            reports.append(report)
            return
        if report["discovered"] != count:
            issue(module_name, class_name, None, "discovered_count_mismatch", "CountMismatch")
        result = unittest.TestResult()
        try:
            suite.run(result)
        except Exception as error:
            category = type(error).__name__
            issue(module_name, class_name, None, "harness_error",
                  category if category in safe_exception_types else "OtherException")
        report.update(run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                      skipped=len(result.skipped), expected_failures=len(result.expectedFailures),
                      unexpected_successes=len(result.unexpectedSuccesses))
        for kind, entries in (("failure", result.failures), ("error", result.errors),
                              ("expected_failure", result.expectedFailures)):
            for case, trace in entries:
                issue(module_name, class_name, case, kind, exception_category(trace))
        for case, _reason in result.skipped:
            issue(module_name, class_name, case, "skip", "SkipTest")
        for case in result.unexpectedSuccesses:
            issue(module_name, class_name, case, "unexpected_success", "UnexpectedSuccess")
        if result.testsRun != count:
            issue(module_name, class_name, None, "run_count_mismatch", "CountMismatch")
        reports.append(report)

    outcomes = {}
    with ExitStack() as stack:
        for target, name in ((httpx.HTTPTransport, "handle_request"), (httpx.AsyncHTTPTransport, "handle_async_request"),
                             (requests.Session, "send"), (urllib_request, "urlopen")):
            stack.enter_context(patch.object(target, name, refuse_transport))
        for flag in ("lead_delivery_enabled", "customer_messages_enabled", "property_inquiry_require_consent"):
            stack.enter_context(patch.object(settings, flag, False))
        for name, class_name, count in CONTRACTS:
            run_suite(name, class_name, count, lambda name=name, class_name=class_name:
                      loader.loadTestsFromTestCase(getattr(source_module(name), class_name)))
            outcomes[name] = count
        run_suite("legacy_inquiry", "LegacyInquiryRolloutTests", 1, lambda: unittest.TestSuite([
            legacy.legacy_rollout_case(source_module("property_inquiry"))]))
        outcomes["legacy_rollout"] = 1
        run_suite("contact_leads", "LeadContactTests", 1, lambda: unittest.TestSuite([
            source_module("contact_leads").LeadContactTests("test_capture_endpoint_retains_contact_validation")]))
        for name, class_name, count in BROAD_CONTRACTS:
            if name == "admin_trade_contacts":
                previous_flag = os.environ.get("TRADE_CONTACT_FIXTURE")
                previous_origins = settings.allowed_origins
                origins = settings.origins_list
                scoped_origins = previous_origins if "http://localhost:5174" in origins else ",".join(
                    [*origins, "http://localhost:5174"])
                with patch.dict(os.environ, {"TRADE_CONTACT_FIXTURE": "yes"}), \
                        patch.object(settings, "allowed_origins", scoped_origins):
                    run_suite(name, class_name, count, owned_trade_suite)
                prior.require(os.environ.get("TRADE_CONTACT_FIXTURE") == previous_flag
                              and settings.allowed_origins == previous_origins,
                              "trade_fixture_scope_restoration_required")
            else:
                run_suite(name, class_name, count, lambda name=name, class_name=class_name:
                          loader.loadTestsFromTestCase(getattr(source_module(name), class_name)))
            outcomes[name] = outcomes.get(name, 0) + count

    prior.stage = "contract_cleanup"
    schemas = None
    try:
        with engine.connect() as connection:
            schemas = connection.execute(text(
                "SELECT COUNT(*) FROM pg_namespace "
                "WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg_%'"
            )).scalar_one()
    except Exception:
        # Retain all case diagnostics while marking an unverified cleanup failed.
        schemas = None
    finally:
        engine.dispose()
    diagnostics = {"status": "PASS" if not issues and not unexpected_egress and schemas == 0 else "FAIL",
                   "required_cases": 314, "required_suites": 32,
                   "run_cases": sum(row["run"] for row in reports), "suite_count": len(reports),
                   "unexpected_transport_attempts": len(unexpected_egress), "schema_cleanup_verified": schemas == 0,
                   "suites": reports, "issues": issues}
    print("NETWORK_CONTRACT_DIAGNOSTICS " + json.dumps(diagnostics, sort_keys=True, separators=(",", ":")), flush=True)
    prior.require(not unexpected_egress, "real_transport_was_attempted")
    prior.require(schemas == 0, "candidate_test_schema_cleanup_required")
    prior.require(not issues and len(reports) == 32 and diagnostics["run_cases"] == 314,
                  "candidate_contract_suite_failures")
    return outcomes


def main():
    prior = None
    try:
        prior = load_pinned("accepted_communications_test", PRIOR_HARNESS, PRIOR_SHA256)
        result = verify_links(prior)
        contracts = verify_candidate_contracts(prior)
        result.update(candidate_contracts=contracts, candidate_contracts_passed=sum(contracts.values()),
                      focused_legacy_string_capture_passed=1,
                      triggered_sms_or_automatic_whatsapp=False, postgres_version_num=170006)
    except Exception as error:
        result = {"status": "FAIL", "stage": prior.stage if prior is not None else "rehearsal_source",
                  "exception_type": type(error).__name__}
        if prior is not None and isinstance(error, prior.RehearsalFailure):
            result["reason"] = str(error)
        print("CUSTOMER_FOLLOWUP_RESULT " + json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
        return 1
    print("CUSTOMER_FOLLOWUP_RESULT " + json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
