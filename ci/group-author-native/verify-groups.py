"""Current group28 in a separately marked database and fresh application process."""
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

SOURCE = "6ef69b249c573eedaea962dad530eaed0a612b24"
TREE = "f1ef617e51e0d8a900d9f4bfc15a559bf676276f"
PROFILE = Path(__file__).resolve().parent.parent / "profile-customization-native/verify.py"
PROFILE_SHA = "e31c7120f04467c38205e8cabcf31f7457ffaa8cef538220573a562b2e6ad3cb"
EXPECTED = {"status": "PASS", "tests": 28, "group_input_tests": 4, "social_group_tests": 24,
    "source": SOURCE, "tree": TREE, "schema_tables": 106, "model_tables": 105,
    "head": "e8b5a3d1f907", "separate_database_and_process": True,
    "genuine_previous99_baseline106_current_migration": True,
    "current106_catalog_unchanged": True, "canonical106_rows_catalog_sequences_unchanged": True,
    "canonical_profile_private_guards_unchanged": True, "canonical_server_start_unchanged": True,
    "committed_synthetic_group_rows_present": True, "inbox_tables_empty": True,
    "external_provider_attempts": 0}
STAGE = "source"
ATTEMPTS = []


class Failure(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise Failure(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def refuse(*_args, **_kwargs):
    ATTEMPTS.append(1)
    raise RuntimeError("external_transport_refused")


def load_profile():
    require(PROFILE.is_file() and not PROFILE.is_symlink()
        and os.statvfs(PROFILE).f_flag & os.ST_RDONLY
        and sha256(PROFILE.read_bytes()).hexdigest() == PROFILE_SHA,
        "exact_readonly_profile_helper_required")
    spec = spec_from_file_location("group28_profile_helpers", PROFILE)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.COMMIT, module.TREE = SOURCE, TREE
    return module


def suite(testcase, methods, expected, label):
    capture, result = StringIO(), unittest.TestResult()
    with redirect_stdout(capture), redirect_stderr(capture):
        unittest.defaultTestLoader.loadTestsFromTestCase(testcase).run(result)
    print("GROUP_AUTHOR_NATIVE_COUNTS " + canonical({"suite": label, "tests": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
        "provider_attempts": len(ATTEMPTS)}), flush=True)
    for case, _ in result.failures + result.errors:
        method = getattr(case, "_testMethodName", "fixture_setup_or_cleanup")
        print("GROUP_AUTHOR_NATIVE_FAILED_METHOD " + label + " "
            + (method if method in methods else "fixture_setup_or_cleanup"), flush=True)
    require(len(capture.getvalue().encode()) <= 2 * 1024 * 1024, "bounded_source_output_required")
    require(result.testsRun == expected and result.wasSuccessful() and not result.skipped
        and not result.expectedFailures and not result.unexpectedSuccesses and not ATTEMPTS,
        "all_exact_current_group_cases_required")


def run():
    global STAGE
    logging.disable(logging.CRITICAL)
    import importlib.metadata as metadata
    baseline = json.loads(Path("/opt/inbox-native/baseline-inventory.json").read_text())
    actual = {re.sub(r"[-_.]+", "-", item.metadata["Name"].lower()): item.version
        for item in metadata.distributions()}
    require(actual == {**baseline, "openpyxl": "3.1.5", "defusedxml": "0.7.1", "et-xmlfile": "2.0.0"},
        "unchanged_dependency_inventory_required")
    profile = load_profile()
    inherited = profile.load_pinned(profile.INHERITED, profile.INHERITED_SHA, "group28_inherited_helpers")
    inherited.COMMIT, inherited.TREE = SOURCE, TREE
    base = inherited.helpers()
    application = base.verified_checkout()
    profile.source_at(base, Path("/baseline106"), profile.BASE_COMMIT, profile.BASE_TREE)
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    require(re.fullmatch(r"[a-f0-9]{32}", nonce), "exact_owned_nonce_required")
    database = "ildr_group_avatar_ci_" + nonce
    canonical_db = "ildr_browser_ci_" + nonce
    require(database != canonical_db and "database" not in sys.modules and "main" not in sys.modules
        and "test_social_groups" not in sys.modules, "separate_fresh_application_process_required")
    catalog, _retained = base.frozen_helpers()
    require(catalog.query(database, """SELECT current_database(),host(inet_server_addr()),inet_server_port(),
        current_setting('server_version_num')::int,shobj_description(oid,'pg_database'),
        NOT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public')
        FROM pg_database WHERE datname=current_database()""")
        == [(database, "127.0.0.1", 55449, 170006, "ildr-group-avatar-native-ci:" + nonce, True)],
        "fresh_marked_group_database_required")
    require(catalog.query(canonical_db, "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()")
        == [("ildr-admin-share-ci:" + nonce,)], "canonical_database_marker_required")
    full = tuple(sorted(catalog.OLD_TABLES + inherited.GROUPS + inherited.INBOX))
    require(len(full) == 106 and catalog.table_names(canonical_db) == full,
        "canonical_current106_required")
    profile.version(catalog, canonical_db, profile.HEAD)
    preserved = catalog.snapshot(canonical_db, full)
    schema = catalog.catalog(canonical_db, full)
    auxiliary = profile.auxiliary_metadata(catalog, canonical_db)
    server = catalog.query(canonical_db, "SELECT pg_postmaster_start_time()")
    url = "postgresql+psycopg2://postgres@127.0.0.1:55449/" + database
    base.configure(application, url)
    os.environ["ALLOWED_ORIGINS"] = "http://localhost:5174"
    os.environ["SOCIAL_GROUPS_FIXTURE"] = "yes"
    import httpx, requests, resend
    capture = StringIO()
    with (patch.object(httpx.HTTPTransport, "handle_request", refuse),
          patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse),
          patch.object(requests.Session, "send", refuse), patch.object(urllib_request, "urlopen", refuse),
          patch.object(resend.Emails, "send", refuse), patch.object(smtplib.SMTP, "connect", refuse),
          patch.object(smtplib.SMTP_SSL, "connect", refuse)):
        STAGE = "previous99"
        inherited.previous_start(base, url)
        require(catalog.table_names(database) == tuple(sorted(catalog.OLD_TABLES + inherited.GROUPS)),
            "real_group_previous99_required")
        profile.version(catalog, database, inherited.OLD_HEAD)
        from database import engine, Base
        from config import settings
        from services.lead_delivery import enabled
        require(engine.url.database == database and engine.url.host == "127.0.0.1"
            and engine.url.port == 55449 and not enabled() and not settings.lead_delivery_enabled
            and not settings.customer_messages_enabled and not settings.ghl_enabled
            and not settings.paypal_client_id and not settings.paypal_client_secret
            and not settings.supabase_service_key and not settings.resend_api_key,
            "exact_group_database_and_disabled_provider_workers_required")
        from sqlalchemy import text
        with engine.begin() as connection:
            connection.execute(text("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon,authenticated,service_role"))
        STAGE = "baseline106"
        profile.baseline_upgrade(base, url)
        profile.version(catalog, database, profile.OLD_HEAD)
        STAGE = "current106"
        from alembic import command
        from alembic.config import Config
        with redirect_stdout(capture), redirect_stderr(capture):
            command.upgrade(Config("alembic.ini"), "head")
        profile.version(catalog, database, profile.HEAD)
        require(catalog.table_names(database) == full and len(Base.metadata.tables) == 105
            and set(Base.metadata.tables) == set(full) - {"alembic_version"}, "real_group_current106_model105_required")
        require(catalog.catalog(database, full) == schema, "real_current_group_catalog_parity_required")
        inherited.privacy(catalog, database)
        allowed = {"GroupInputTest", "SocialGroupsTest"}
        input_methods = profile.suite_inventory(application, "test_social_groups.py", "GroupInputTest", 4, allowed)
        group_methods = profile.suite_inventory(application, "test_social_groups.py", "SocialGroupsTest", 24, allowed)
        sys.path.insert(0, str(application / "tests"))
        import test_social_groups as groups
        require(groups.SocialGroupsTest.__unittest_skip__ is False, "actual_group_fixture_enabled_required")
        STAGE = "group_inputs4"
        suite(groups.GroupInputTest, input_methods, 4, "group_inputs")
        STAGE = "social_groups24"
        suite(groups.SocialGroupsTest, group_methods, 24, "social_groups")
        STAGE = "preservation"
        engine.dispose()
        require(catalog.table_names(database) == full and catalog.catalog(database, full) == schema,
            "current_group106_catalog_unchanged_required")
        profile.version(catalog, database, profile.HEAD)
        require(all(catalog.query(database, "SELECT count(*) FROM public." + name) == [(0,)]
            for name in inherited.INBOX), "group_does_not_grant_or_write_inbox_required")
        require(all(catalog.query(database, "SELECT count(*) FROM public." + name)[0][0] > 0
            for name in ("users", "network_groups", "network_group_actions", "social_network_profiles", "network_media")),
            "actual_committed_group_and_chosen_avatar_fixtures_required")
        require(catalog.snapshot(canonical_db, full) == preserved and catalog.catalog(canonical_db, full) == schema
            and profile.auxiliary_metadata(catalog, canonical_db) == auxiliary,
            "canonical106_rows_catalog_all_sequences_unchanged_required")
        profile.version(catalog, canonical_db, profile.HEAD)
        inherited.privacy(catalog, canonical_db)
        require(catalog.query(canonical_db, "SELECT pg_postmaster_start_time()") == server,
            "canonical_postmaster_start_unchanged_required")
        require(not ATTEMPTS and len(capture.getvalue().encode()) <= 2 * 1024 * 1024,
            "zero_provider_bounded_capture_required")
        base.verified_checkout()
    print("GROUP_AUTHOR_NATIVE_RESULT " + canonical(EXPECTED), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        state = getattr(getattr(error, "orig", error), "pgcode", None)
        name = type(error).__name__
        print("GROUP_AUTHOR_NATIVE_FAILURE " + canonical({"stage": STAGE,
            "error_type": name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name) else "UnhandledError",
            "reason": error.args[0] if isinstance(error, Failure) else "external_failure",
            "sqlstate": state if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state) else "unavailable"}), flush=True)
        raise SystemExit(1) from None
