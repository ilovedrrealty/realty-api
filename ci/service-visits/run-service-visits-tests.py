"""Run the exact awarded-service visit suite in a disposable hosted database."""

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
RESULT_PREFIX = "SERVICE_VISITS_BACKEND_RESULT "
TEST_MODULE = "test_service_visit"
TEST_CLASS = "PrivateAwardedServiceWork"
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
    commit = os.environ.get("ILDR_SERVICE_VISITS_COMMIT", "")
    tree = os.environ.get("ILDR_SERVICE_VISITS_TREE", "")
    require(all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (commit, tree)), "exact_source_ids_required")
    application = Path(os.environ.get("ILDR_SERVICE_VISITS_APPLICATION_ROOT", "/application"))
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
    test_file = application / "tests" / f"{TEST_MODULE}.py"
    require(test_file.is_file() and test_file.resolve(strict=True) == test_file, "canonical_test_source_required")
    return application, test_file, commit, tree


def verify_empty_database():
    nonce = os.environ.get("ILDR_SERVICE_VISITS_CI_NONCE", "")
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
    require(identity == (DATABASE_NAME, "127.0.0.1", 55449, 170011, f"ildr-service-visits-ci:{nonce}", True, True),
            "empty_marked_loopback_postgres_required")


def isolate_application(application, test_file):
    os.chdir(application)
    sys.path[:0] = [str(application), str(test_file.parent)]
    os.environ.update(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        JWT_SECRET="synthetic-service-visits-only",
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


def run_suite(test_file):
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
        module = importlib.import_module(TEST_MODULE)
        require(Path(module.__file__).resolve(strict=True) == test_file, "targeted_test_module_required")
        test_class = getattr(module, TEST_CLASS)
        suite = unittest.TestLoader().loadTestsFromTestCase(test_class)
        names = [test.id().rsplit(".", 1)[-1] for test in suite]
        require(4 <= len(names) <= 40 and len(set(names)) == len(names)
                and all(re.fullmatch(r"test_[a-z0-9_]{1,120}", name) for name in names),
                "targeted_test_inventory_required")
        result = unittest.TestResult()
        suite.run(result)

    pass_all = (result.wasSuccessful() and result.testsRun == len(names) and not result.skipped
                and not result.expectedFailures and not result.unexpectedSuccesses and not network_attempts)
    report = {
        "status": "PASS" if pass_all else "FAIL",
        "test_class": TEST_CLASS,
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
            test_name = getattr(getattr(case, "test_case", case), "_testMethodName", None)
            cases.add(test_name if test_name in names else "suite_setup_or_cleanup")
        report["nonpassing_cases"] = sorted(cases)
    return report


def run():
    global stage
    application, test_file, commit, tree = verified_checkout()
    stage = "database_preflight"
    verify_empty_database()
    stage = "targeted_tests"
    isolate_application(application, test_file)
    logging.disable(logging.CRITICAL)
    with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink), redirect_stderr(sink):
        report = run_suite(test_file)
    stage = "database_cleanup_readback"
    verify_empty_database()
    report.update(application_commit=commit, application_tree=tree,
                  test_source_sha256=hashlib.sha256(test_file.read_bytes()).hexdigest(), database_empty_after=True)
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
