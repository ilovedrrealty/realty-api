"""Run private due-work reminders and prior service regressions in a disposable hosted database."""

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
from urllib import request as urllib_request


DATABASE_NAME = "ildr_platform_test_20261025"
DATABASE_URL = f"postgresql+psycopg2://postgres@127.0.0.1:55449/{DATABASE_NAME}"
RESULT_PREFIX = "DUE_WORK_REMINDERS_BACKEND_RESULT "
TEST_SUITES = {
    "test_due_work_reminders": ("PrivateDueWorkReminders", (
        "test_confirmed_visit_reschedule_cancel_and_completion_use_exact_occurrences",
        "test_current_owner_and_exact_professional_opt_in_are_independent",
        "test_migration_keeps_legacy_slots_and_private_storage",
        "test_milestone_occurrence_changes_only_for_date_or_reopening",
        "test_revoked_and_changed_grants_require_fresh_opt_in_and_custody",
        "test_snooze_dismiss_restore_stop_and_receipt_are_actor_scoped",
    )),
    "test_maintenance_reminders": ("PrivateMaintenanceReminders", (
        "test_dominican_due_window_snooze_pause_resume_and_stop",
        "test_exact_current_source_custody_and_default_off",
        "test_migration_keeps_private_storage_and_one_open_occurrence",
        "test_preferences_and_receipts_are_versioned_and_source_scoped",
        "test_recurrence_is_anchored_skips_missed_dates_and_commits_once",
    )),
    "test_service_favorites": ("PrivateServiceFavorites", (
        "test_concurrent_save_deduplicates_and_pages_stably",
        "test_current_alias_code_and_unavailable_placeholders_remain_removable",
        "test_migration_denies_direct_roles_and_self_rows",
        "test_personal_access_and_exact_minimal_provider_projection",
    )),
    "test_service_rebooking": ("PrivateCompletedServiceRebooking", (
        "test_current_listing_and_private_sources_use_current_provider_details",
        "test_only_current_custodian_of_owner_approved_award_can_rebook",
        "test_opt_out_revocation_and_source_changes_close_context_without_writes",
    )),
    "test_service_review": ("PrivateCompletedServiceReview", (
        "test_migration_restricts_raw_writes_direct_roles_and_retention",
        "test_owner_receipt_and_concurrent_submission_are_idempotent",
        "test_requires_owner_approved_completion_and_exact_readers",
        "test_revocation_archive_and_custody_close_access",
        "test_strict_private_projection_and_no_work_history_leak",
    )),
    "test_service_negotiation": ("PrivateServiceNegotiationConversation", (
        "test_idempotent_submission_is_actor_thread_and_phase_scoped",
        "test_migration_preserves_private_immutable_recipient_records",
        "test_only_current_custodian_and_exact_eligible_accepted_provider",
        "test_plain_text_and_deterministic_newest_pagination",
        "test_terminal_and_source_changes_close_without_transcript_transfer",
    )),
    "test_service_conversation": ("PrivateAwardedServiceConversation", (
        "test_exact_participants_lose_transcript_on_revocation_and_custody_change",
        "test_idempotency_receipts_and_concurrent_photo_upload",
        "test_message_shape_and_plain_text_validation",
        "test_migration_blocks_direct_access_invalid_senders_and_record_removal",
        "test_stable_newest_first_pagination_with_equal_timestamps",
    )),
    "test_service_visit": ("PrivateAwardedServiceWork", (
        "test_completion_review_rolls_back_with_history_and_replay_creates_one_private_entry",
        "test_distinct_requests_cannot_confirm_overlapping_provider_visits",
        "test_lazy_access_exact_winner_and_receipt_revocation",
        "test_migration_fresh_and_precreated_tables_keep_private_retained_records",
        "test_validation_other_side_reschedule_cancel_and_current_replay",
    )),
}
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
    commit = os.environ.get("ILDR_DUE_WORK_REMINDERS_COMMIT", "")
    tree = os.environ.get("ILDR_DUE_WORK_REMINDERS_TREE", "")
    require(all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (commit, tree)), "exact_source_ids_required")
    application = Path(os.environ.get("ILDR_DUE_WORK_REMINDERS_APPLICATION_ROOT", "/application"))
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
    test_files = {module: application / "tests" / f"{module}.py" for module in TEST_SUITES}
    require(all(path.is_file() and path.resolve(strict=True) == path for path in test_files.values()),
            "canonical_test_sources_required")
    return application, test_files, commit, tree


def verify_empty_database():
    nonce = os.environ.get("ILDR_DUE_WORK_REMINDERS_CI_NONCE", "")
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
    require(identity == (DATABASE_NAME, "127.0.0.1", 55449, 170011, f"ildr-due-work-reminders-ci:{nonce}", True, True),
            "empty_marked_loopback_postgres_required")


def isolate_application(application):
    os.chdir(application)
    sys.path[:0] = [str(application), str(application / "tests")]
    os.environ.update(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        JWT_SECRET="synthetic-due-work-reminders-only",
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
        PAYPAL_CLIENT_ID="",
        PAYPAL_CLIENT_SECRET="",
        PAYPAL_WEBHOOK_ID="",
    )


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
        for module_name, (class_name, expected_cases) in TEST_SUITES.items():
            module = importlib.import_module(module_name)
            require(Path(module.__file__).resolve(strict=True) == test_files[module_name],
                    "targeted_test_module_required")
            selected = unittest.TestLoader().loadTestsFromTestCase(getattr(module, class_name))
            case_names = [test.id().rsplit(".", 1)[-1] for test in selected]
            require(tuple(case_names) == expected_cases, "exact_targeted_test_inventory_required")
            class_counts[class_name] = len(case_names)
            names.extend(f"{class_name}.{name}" for name in case_names)
            suite.addTests(selected)
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
