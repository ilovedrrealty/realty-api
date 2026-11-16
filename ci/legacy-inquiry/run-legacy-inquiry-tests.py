"""Verify the deployed property inquiry contract and proposed consent rollout on hosted Postgres."""

import hashlib
import importlib
import json
import logging
import os
import re
import subprocess
import sys
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from uuid import UUID
from urllib import request as urllib_request


DATABASE_NAME = "ildr_platform_test_20261025"
DATABASE_URL = f"postgresql+psycopg2://postgres@127.0.0.1:55449/{DATABASE_NAME}"
RESULT_PREFIX = "LEGACY_INQUIRY_BACKEND_RESULT "
TEST_SUITES = (
    ('test_property_inquiry', 'PropertyInquiryTests', (
        'test_account_identity_is_never_inferred_from_contact_email_and_mine_is_private',
        'test_backend_first_email_compatibility_is_explicit_and_final_cutover_is_strict',
        'test_capture_does_not_bypass_phone_only_or_tracked_contact_permission',
        'test_contact_permission_is_explicit_and_measurement_choice_does_not_block_contact',
        'test_invalid_contacts_or_neither_do_not_store_or_queue',
        'test_invalid_missing_private_and_unknown_property_references_are_not_accepted',
        'test_mine_read_failure_is_private_and_not_presented_as_an_empty_feed',
        'test_owner_change_account_requests_keep_nonpublic_property_scope_and_booking_requires_email',
        'test_phone_email_and_both_keep_property_type_optional_comments_and_one_outbox',
        'test_unchanged_retry_is_one_event_even_after_listing_is_archived',
    )),
    ('test_lead_tracking', 'LeadTrackingTests', (
        'test_additive_migration_preserves_existing_rows_rls_and_nullable_columns',
        'test_admin_only_detail_and_summary_never_return_raw_click_ids',
        'test_aggregate_excludes_explicit_tests_from_intent_and_ad_reference_counts',
        'test_all_contact_intents_preserve_generic_action_and_explicit_internal_tests_are_excluded',
        'test_concurrent_retry_uses_database_unique_key_and_side_effects_once',
        'test_contact_consent_and_contact_validation_precede_database_and_notifications',
        'test_identical_retry_persists_one_lead_and_notifies_once',
        'test_legacy_callers_remain_compatible_and_do_not_guess_measurement_consent',
        'test_measurement_decline_never_blocks_contact_and_discards_click_identifiers',
        'test_phone_only_email_only_and_both_keep_structured_attribution_out_of_message',
        'test_reused_identifier_with_changed_payload_or_actor_returns_no_receipt',
        'test_whitelist_ignores_unknown_fields_sanitizes_paths_and_drops_obvious_pii',
    )),
    ('test_contact_leads', 'LeadContactTests', (
        'test_blank_name_rejected_even_with_valid_phone',
        'test_blank_optional_fields_are_absent',
        'test_capture_endpoint_retains_contact_validation',
        'test_each_interest_accepts_phone_email_and_both',
        'test_each_interest_rejects_neither_or_invalid_supplied_contact',
        'test_email_only_and_both_notifications_keep_real_email',
        'test_ghl_creates_each_identifier_combination_for_each_interest',
        'test_ghl_duplicate_contacts_add_each_interest_without_replacing_tags',
        'test_ghl_duplicate_phone_contact_keeps_rental_tags_and_notes',
        'test_ghl_duplicate_tag_failure_is_recorded_without_claiming_sync',
        'test_guest_booking_rejects_missing_email_before_creating_anything',
        'test_international_country_code_preserved',
        'test_optional_comments_may_be_omitted',
        'test_owner_and_assigned_notifications_accept_phone_only',
        'test_property_contact_is_flexible_while_booking_and_legacy_inquiry_keep_email',
        'test_response_serializes_absent_email_without_fabrication',
    )),
    ('test_contact_leads', 'GHLDeliveryDiagnosticsTests', (
        'test_both_followup_failures_are_recorded_safely',
        'test_duplicate_contact_partial_followup_keeps_existing_link',
        'test_malformed_json_records_safe_error',
        'test_no_note_is_required_when_inquiry_has_no_note',
        'test_note_failure_preserves_link_and_still_attempts_task_once',
        'test_success_marks_complete_after_followups_once',
        'test_success_without_valid_contact_id_records_error',
        'test_tag_failure_keeps_confirmed_contact_without_posting_extras',
        'test_task_failure_does_not_claim_complete_sync',
        'test_transport_failure_is_sanitized_and_never_retried',
    )),
)
stage = "isolation"


class RehearsalRefused(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise RehearsalRefused(reason)


def verified_checkout():
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_REPOSITORY") == "ilovedrrealty/realty-api",
            "exact_hosted_repository_required")
    require({interface.name for interface in Path("/sys/class/net").iterdir()} == {"lo"},
            "loopback_network_namespace_required")
    commit = os.environ.get("ILDR_LEGACY_INQUIRY_COMMIT", "")
    tree = os.environ.get("ILDR_LEGACY_INQUIRY_TREE", "")
    require((commit, tree) == ("b38d477c0e2e50f87f1f2e4cc1ceb3c707af92c5", "a53b3f829f70717069f5c637c826fb8f92c02f7c"),
            "reviewed_application_required")
    require(all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (commit, tree)), "exact_source_ids_required")
    application = Path(os.environ.get("ILDR_LEGACY_INQUIRY_APPLICATION_ROOT", "/application"))
    require(application.is_absolute() and application.resolve(strict=True) == application and application.is_dir()
            and os.statvfs(application).f_flag & os.ST_RDONLY, "canonical_read_only_checkout_required")

    def git(*arguments):
        return subprocess.check_output(
            ["git", "--no-optional-locks", "-c", f"safe.directory={application}", "-C", str(application), *arguments],
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"},
            stderr=subprocess.DEVNULL, timeout=12,
        ).decode("ascii").strip()

    require(git("rev-parse", "HEAD", "HEAD^{tree}").splitlines() == [commit, tree]
            and not git("status", "--porcelain=v1", "--untracked-files=all")
            and not (application / ".env").exists() and not (application / ".env").is_symlink(),
            "clean_pinned_source_required")
    test_files = {module: application / "tests" / f"{module}.py" for module, _, _ in TEST_SUITES}
    require(all(path.is_file() and path.resolve(strict=True) == path for path in test_files.values()),
            "canonical_test_sources_required")
    return application, test_files, commit, tree


def verify_empty_database():
    nonce = os.environ.get("ILDR_LEGACY_INQUIRY_CI_NONCE", "")
    require(re.fullmatch(r"[0-9a-f]{32}", nonce)
            and os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL") == DATABASE_URL,
            "exact_owned_database_configuration_required")
    import psycopg2

    with closing(psycopg2.connect(host="127.0.0.1", port=55449, dbname=DATABASE_NAME, user="postgres",
                                 password="", connect_timeout=4, options="-cstatement_timeout=5000")) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT current_database(), host(inet_server_addr()), inet_server_port(),
                       current_setting('server_version_num')::integer,
                       shobj_description(oid, 'pg_database'),
                       NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                                   WHERE n.nspname = 'public'),
                       NOT EXISTS (SELECT 1 FROM pg_namespace
                                   WHERE nspname NOT IN ('public', 'information_schema') AND nspname !~ '^pg_')
                FROM pg_database WHERE datname = current_database()
            """)
            identity = cursor.fetchone()
    require(identity == (DATABASE_NAME, "127.0.0.1", 55449, 170011, f"ildr-legacy-inquiry-ci:{nonce}", True, True),
            "empty_marked_loopback_postgres_required")


def isolate_application(application):
    os.chdir(application)
    sys.path[:0] = [str(application), str(application / "tests")]
    os.environ.update(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        JWT_SECRET="synthetic-legacy-inquiry-only",
        ENVIRONMENT="development",
        GHL_ENABLED="false",
        GHL_API_KEY="",
        GHL_LOCATION_ID="",
        GHL_WEBHOOK_SECRET="",
        RESEND_API_KEY="",
        NOTIFY_EMAIL="",
        SUPABASE_URL="",
        SUPABASE_SERVICE_KEY="",
        LEAD_DELIVERY_ENABLED="false",
        CUSTOMER_MESSAGES_ENABLED="false",
        PROPERTY_INQUIRY_REQUIRE_CONSENT="false",
        PAYPAL_CLIENT_ID="",
        PAYPAL_CLIENT_SECRET="",
        PAYPAL_WEBHOOK_ID="",
    )


def legacy_rollout_case(property_tests):
    from utils import email

    class LegacyInquiryRolloutTests(property_tests.PropertyInquiryTests):
        def setUp(self):
            # The inherited class setup creates a fresh schema and FastAPI router.
            # Its per-test setup forces the outbox on, so leave all real flags intact.
            self.assertFalse(property_tests.settings.property_inquiry_require_consent)
            self.assertFalse(property_tests.settings.lead_delivery_enabled)
            self.assertFalse(property_tests.settings.customer_messages_enabled)
            self.assertFalse(property_tests.settings.ghl_enabled)
            self.assertFalse(property_tests.lead_delivery.enabled())
            self.assertEqual(self.counts(), (0, 0))

        def test_exact_deployed_payload_before_and_after_strict_consent(self):
            # Exact field set from landing 3cac3d6 apps/landing/src/api/inquiries.ts:18.
            # Its optional phone is omitted when the contact entered only an email.
            exact_old_request = {
                "type": "property_inquiry",
                "property_id": str(self.listings["active"]),
                "name": "Synthetic legacy visitor",
                "email": "legacy-visitor@example.com",
                "message": "Synthetic legacy comments",
            }
            recipients = ["one@example.com", "two@example.com", "three@example.com", "four@example.com"]
            with property_tests.Session(self.engine) as db:
                db.get(property_tests.SiteSettings, 1).data = {"notify_email": ", ".join(recipients)}
                db.commit()

            with patch.object(email, "_send") as mock_transport:
                accepted = self.client.post("/leads", json=exact_old_request)
                self.assertEqual(accepted.status_code, 201)
                self.assertEqual(accepted.headers["cache-control"], "no-store")
                receipt = accepted.json()
                self.assertEqual(receipt["measurement"]["reason"], "legacy_submission")
                self.assertFalse(receipt["measurement"]["eligible"])
                with property_tests.Session(self.engine) as db:
                    lead = db.get(property_tests.Lead, UUID(receipt["id"]))
                    self.assertEqual(lead.type, "property_inquiry")
                    self.assertEqual(lead.property_id, self.listings["active"])
                    self.assertEqual(lead.email, exact_old_request["email"])
                    self.assertEqual(lead.message, exact_old_request["message"])
                    self.assertIsNone(lead.phone)
                    self.assertIsNone(lead.tracking)
                    self.assertIsNone(lead.submission_id)
                    self.assertIsNone(lead.from_user_id)
                self.assertEqual(self.counts(), (1, 0))
                mock_transport.assert_called_once()
                targets, _, body = mock_transport.call_args.args
                self.assertEqual(targets, recipients)
                self.assertIn("Synthetic private title active", body)

                with patch.object(property_tests.settings, "property_inquiry_require_consent", True):
                    denied = self.client.post("/leads", json=exact_old_request)
                    self.assertEqual(denied.status_code, 422)
                    self.assertEqual(denied.headers["cache-control"], "no-store")
                    self.assertEqual(self.counts(), (1, 0))
                    mock_transport.assert_called_once()

                    modern = self.payload(message="Synthetic consent-bearing comments")
                    accepted_modern = self.client.post("/leads", json=modern)
                    self.assertEqual(accepted_modern.status_code, 201)
                    self.assertFalse(accepted_modern.json()["measurement"]["eligible"])

                with property_tests.Session(self.engine) as db:
                    lead = db.get(property_tests.Lead, UUID(accepted_modern.json()["id"]))
                    self.assertEqual(lead.property_id, self.listings["active"])
                    self.assertIs(lead.tracking["contact_consent"], True)
                    self.assertEqual(str(lead.submission_id), modern["submission_id"])
                self.assertEqual(self.counts(), (2, 0))
                self.assertEqual(mock_transport.call_count, 2)
                self.assertEqual(mock_transport.call_args.args[0], recipients)

    return LegacyInquiryRolloutTests("test_exact_deployed_payload_before_and_after_strict_consent")


def run_suite(test_files):
    import httpx
    import requests

    network_attempts = []

    def refuse_transport(*_args, **_kwargs):
        network_attempts.append(True)
        raise RehearsalRefused("real_external_transport_refused")

    with (
        patch.object(httpx.HTTPTransport, "handle_request", refuse_transport),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse_transport),
        patch.object(requests.Session, "send", refuse_transport),
        patch.object(urllib_request, "urlopen", refuse_transport),
    ):
        suite = unittest.TestSuite()
        class_counts = {}
        names = []
        for module_name, class_name, expected_cases in TEST_SUITES:
            module = importlib.import_module(module_name)
            require(Path(module.__file__).resolve(strict=True) == test_files[module_name],
                    "targeted_test_module_required")
            selected = unittest.TestLoader().loadTestsFromTestCase(getattr(module, class_name))
            case_names = [test.id().rsplit(".", 1)[-1] for test in selected]
            require(tuple(case_names) == expected_cases, "exact_targeted_test_inventory_required")
            class_counts[class_name] = len(case_names)
            names.extend(f"{class_name}.{name}" for name in case_names)
            suite.addTests(selected)
        added_case = legacy_rollout_case(importlib.import_module("test_property_inquiry"))
        names.append(f"{type(added_case).__name__}.{added_case._testMethodName}")
        class_counts[type(added_case).__name__] = 1
        suite.addTest(added_case)
        result = unittest.TestResult()
        suite.run(result)

    pass_all = (result.wasSuccessful() and result.testsRun == len(names) and not result.skipped
                and not result.expectedFailures and not result.unexpectedSuccesses and not network_attempts)
    report = {
        "status": "PASS" if pass_all else "FAIL",
        "class_counts": class_counts,
        "test_cases": names,
        "tests_run": result.testsRun,
        "tests_failed": len(result.failures),
        "tests_errored": len(result.errors),
        "tests_skipped": len(result.skipped),
        "tests_expected_failure": len(result.expectedFailures),
        "tests_unexpected_success": len(result.unexpectedSuccesses),
        "external_transport_attempts": len(network_attempts),
    }
    if not pass_all:
        cases = set()
        for case, _ in [*result.failures, *result.errors, *result.skipped, *result.expectedFailures]:
            test = getattr(case, "test_case", case)
            candidate = f"{type(test).__name__}.{getattr(test, '_testMethodName', '')}"
            cases.add(candidate if candidate in names else "suite_setup_or_cleanup")
        report["nonpassing_cases"] = sorted(cases)
    return report


def run():
    global stage
    application, test_files, commit, tree = verified_checkout()
    stage = "database_preflight"
    verify_empty_database()
    stage = "targeted_tests"
    isolate_application(application)
    logging.disable(logging.CRITICAL)
    with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink), redirect_stderr(sink):
        report = run_suite(test_files)
    stage = "database_cleanup_readback"
    verify_empty_database()
    report.update(application_commit=commit, application_tree=tree,
                  test_source_sha256={
                      module: hashlib.sha256(path.read_bytes()).hexdigest() for module, path in test_files.items()
                  }, database_empty_after=True)
    report["test_source_sha256"]["targeted_legacy_rollout"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return report


if __name__ == "__main__":
    try:
        report = run()
    except Exception as error:
        report = {"status": "FAIL", "stage": stage, "exception_type": type(error).__name__}
        if isinstance(error, RehearsalRefused):
            report["reason"] = str(error)
    print(RESULT_PREFIX + json.dumps(report, sort_keys=True, separators=(",", ":")), flush=True)
    raise SystemExit(0 if report["status"] == "PASS" else 1)
