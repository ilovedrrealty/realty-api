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


APPLICATION_COMMIT = "081868373f318498ddaa835de6496aa6e62d6f57"
APPLICATION_TREE = "89c1d970a7a25dcc40452e0c5e17b22229ffab83"
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
    from urllib import request as urllib_request

    from config import settings
    from database import engine

    prior.stage = "contract_inventory"
    tests_root = Path("/application/tests")
    prior.require(tests_root.resolve(strict=True) == tests_root and os.statvfs(tests_root).f_flag & os.ST_RDONLY,
                  "immutable_contracts_required")
    sys.path.insert(0, str(tests_root))
    loader = unittest.TestLoader()
    legacy = load_pinned("accepted_legacy_contract", LEGACY_HARNESS, LEGACY_SHA256)
    unexpected_egress = []

    def refuse_transport(*_args, **_kwargs):
        unexpected_egress.append(True)
        raise prior.RehearsalFailure("unexpected_real_transport")

    def assert_suite(suite, count):
        prior.require(suite.countTestCases() == count, "candidate_contract_inventory_changed")
        result = unittest.TestResult()
        suite.run(result)
        if result.failures or result.errors:
            case = (result.failures + result.errors)[0][0]
            method = getattr(case, "_testMethodName", "")
            prior.require(False, method if re.fullmatch(r"test_[a-z_]{1,84}", method) else "candidate_contract_failed")
        prior.require(result.testsRun == count and not result.skipped and not result.expectedFailures
                      and not result.unexpectedSuccesses, "candidate_contracts_not_fully_executed")

    outcomes = {}
    with ExitStack() as stack:
        for target, name in ((httpx.HTTPTransport, "handle_request"), (httpx.AsyncHTTPTransport, "handle_async_request"),
                             (requests.Session, "send"), (urllib_request, "urlopen")):
            stack.enter_context(patch.object(target, name, refuse_transport))
        for flag in ("lead_delivery_enabled", "customer_messages_enabled", "property_inquiry_require_consent"):
            stack.enter_context(patch.object(settings, flag, False))
        for name, class_name, count in CONTRACTS:
            prior.stage = name
            module = importlib.import_module(f"test_{name}")
            prior.require(Path(module.__file__).resolve(strict=True) == tests_root / f"test_{name}.py",
                          "candidate_test_module_required")
            assert_suite(loader.loadTestsFromTestCase(getattr(module, class_name)), count)
            outcomes[name] = count
        prior.stage = "legacy_rollout"
        assert_suite(unittest.TestSuite([legacy.legacy_rollout_case(module)]), 1)
        outcomes["legacy_rollout"] = 1
        prior.stage = "legacy_string_capture"
        contacts = importlib.import_module("test_contact_leads")
        prior.require(Path(contacts.__file__).resolve(strict=True) == tests_root / "test_contact_leads.py",
                      "candidate_contact_contract_required")
        assert_suite(unittest.TestSuite([
            contacts.LeadContactTests("test_capture_endpoint_retains_contact_validation")
        ]), 1)
        for name, class_name, count in BROAD_CONTRACTS:
            prior.stage = name
            module = importlib.import_module(f"test_{name}")
            prior.require(Path(module.__file__).resolve(strict=True) == tests_root / f"test_{name}.py",
                          "candidate_test_module_required")
            assert_suite(loader.loadTestsFromTestCase(getattr(module, class_name)), count)
            outcomes[name] = outcomes.get(name, 0) + count
    prior.require(not unexpected_egress, "real_transport_was_attempted")
    prior.stage = "contract_cleanup"
    with engine.connect() as connection:
        schemas = connection.execute(text(
            "SELECT COUNT(*) FROM pg_namespace "
            "WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg_%'"
        )).scalar_one()
    engine.dispose()
    prior.require(schemas == 0, "candidate_test_schema_cleanup_required")
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
