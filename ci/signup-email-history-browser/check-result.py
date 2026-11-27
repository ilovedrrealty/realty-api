"""Strict finite receipts. Any diagnostic/failure/native nonzero refuses acceptance."""
import json
from pathlib import Path
import re
import sys

NATIVE = {"status": "PASS", "tests": 21, "exact_current_source": True, "model_created_test_schema": True,
          "genuine106_claim": False, "all_schema_cleanup": True, "public_catalog_unchanged": True,
          "server_start_unchanged": True, "external_provider_attempts": 0}
COUNTS = {"suite": "customer_delivery", "expected": 21, "run": 21, "failures": 0, "errors": 0, "skipped": 0}
API = {"status": "PASS", "schema_tables": 106, "model_tables": 105,
       "all106_rows_sequences_catalog_unchanged": True, "all_function_acl_roles_sequences_unchanged": True,
       "genuine_previous99_to_d8_to_e8": True, "current_head_unchanged": True, "server_start_unchanged": True,
       "private_rls_acl": True, "protected_owner_contacts_unchanged": 82, "private_profiles_unchanged": 46,
       "contacts_unchanged": 85, "sources_unchanged": 86, "users_unchanged": 4, "listings_unchanged": 1,
       "original_accepted_jobs_unchanged": 9, "signup_jobs_unchanged": 11, "all_delivery_jobs_unchanged": 20,
       "cms_version_unchanged": 7, "floorplans_unchanged": 2, "actual_access_boundaries": 16,
       "actual_logins": 8, "ui_mutation_requests": 0, "inbox_writes": 0, "history_payload_selects": 0,
       "actual_ui_pages": [10, 1], "actual_api_page_limit": 2, "private_files_removed": 1,
       "unexpected_mutations": 0, "external_provider_attempts": 0}
BROWSER = {"status": "PASS", "locales": ["en", "es"], "journeys": 12, "geometry": 20, "contexts": 10,
           "presence": 2, "actual_logins": 8, "api_boundary_checks": 16, "ui_page_checks": 4, "api_page_checks": 12,
           "untracked_accounts": 2, "auth_loss_checks": 2, "stale_read_checks": 2,
           "transport_error_recovery_checks": 2, "default_api_limit": 25, "ui_page_size": 10,
           "ui_mutation_requests": 0, "page_errors": 0, "unexpected_mutations": 0, "external_writes": 0,
           "tracking_requests": 0}
GEOMETRY = {locale + "_" + label for locale in ("en", "es") for label in
            ("users", "history_loading", "tracked_history", "untracked_history", "read_error")}
# Bound to the complete actual driver literal inventory, reviewed before freeze.
BROWSER_PHASES = {
    'en_admin_user_entry',
    'en_auth_loss_and_role_boundaries',
    'en_history_loading_geometry_320',
    'en_history_loading_geometry_390',
    'en_metadata_and_paging_privacy',
    'en_read_error_geometry_320',
    'en_read_error_geometry_390',
    'en_read_error_retry',
    'en_tracked_history_geometry_320',
    'en_tracked_history_geometry_390',
    'en_tracked_history_loading',
    'en_untracked_and_stale_account_switch',
    'en_untracked_history_geometry_320',
    'en_untracked_history_geometry_390',
    'en_users_geometry_320',
    'en_users_geometry_390',
    'es_admin_user_entry',
    'es_auth_loss_and_role_boundaries',
    'es_history_loading_geometry_320',
    'es_history_loading_geometry_390',
    'es_metadata_and_paging_privacy',
    'es_read_error_geometry_320',
    'es_read_error_geometry_390',
    'es_read_error_retry',
    'es_tracked_history_geometry_320',
    'es_tracked_history_geometry_390',
    'es_tracked_history_loading',
    'es_untracked_and_stale_account_switch',
    'es_untracked_history_geometry_320',
    'es_untracked_history_geometry_390',
    'es_users_geometry_320',
    'es_users_geometry_390',
    'final',
    'fixture',
}
BROWSER_REASONS = {
    'ACCEPTANCE_NOT_INBOX_DELIVERY_REQUIRED',
    'ACCOUNT_CHANGE_ABORTS_OLD_READ_REQUIRED',
    'ACTUAL_ACTOR_ROLE_REQUIRED',
    'ACTUAL_ADMIN_UI_LOGIN_REQUIRED',
    'ACTUAL_BOUNDARY_STATUS_REQUIRED',
    'ACTUAL_LOADING_STATE_REQUIRED',
    'ACTUAL_STORED_PAGE_ORDER_REQUIRED',
    'ACTUAL_UNTRACKED_ACCOUNT_REQUIRED',
    'ALL_TWELVE_JOURNEYS_REQUIRED',
    'BROWSER_PRIVATE_NO_STORE_REQUIRED',
    'COMPLETED_LOCALE_SESSIONS_CLOSED_REQUIRED',
    'CONTROL_OVERFLOW',
    'EXACT_COMPLETED_LOCALE_SESSIONS_REQUIRED',
    'EXACT_FIXTURE_SCOPE_REQUIRED',
    'EXACT_PAGE_LENGTH_REQUIRED',
    'EXACT_READ_SESSION_AND_PAGING_COUNTS_REQUIRED',
    'EXACT_RECOVERY_AND_DENIAL_COUNTS_REQUIRED',
    'EXACT_SCOPED_REPORT_REQUIRED',
    'FINITE_PHASE_REQUIRED',
    'FIXED_OWNED_READ_REQUIRED',
    'FOUR_SYNTHETIC_ROLES_REQUIRED',
    'INVALID_INPUT_NOT_ECHOED_REQUIRED',
    'LOCALIZED_COPY_REQUIRED',
    'METADATA_WHITELIST_REQUIRED',
    'NO_HISTORY_READ_BEFORE_OPEN_REQUIRED',
    'NO_PRIVATE_CONTENT_OR_MACHINE_REFERENCE_REQUIRED',
    'OLD_ACCOUNT_CANNOT_REPLACE_CURRENT_HISTORY',
    'OWNED_ACCOUNT_ID_REQUIRED',
    'OWNED_BOUNDED_FIXTURE_REQUIRED',
    'OWNED_LOOPBACK_PRESENCE_REQUIRED',
    'PRIVATE_NO_STORE_REQUIRED',
    'PROCESS_ERROR',
    'READONLY_BROWSER_ISOLATION_REQUIRED',
    'READONLY_NO_SEND_RETRY_REQUIRED',
    'READ_ERROR_IS_NOT_EMPTY_HISTORY_REQUIRED',
    'REAL_API_PAGING_ORDER_REQUIRED',
    'REAL_AUTH_LOSS_CLEARS_HISTORY_REQUIRED',
    'REAL_COOKIE_LOGIN_REQUIRED',
    'REAL_EMPTY_ACCOUNT_REQUIRED',
    'REAL_SECOND_PAGE_REQUIRED',
    'SOURCE_CLOSE_CONTROL_REQUIRED',
    'STAGE_DEADLINE',
    'TIMEOUT',
    'UNIQUE_ACCOUNT_HISTORY_REQUIRED',
    'UNIQUE_GEOMETRY_REQUIRED',
    'UNIQUE_OWNED_USER_CARD_REQUIRED',
    'UNIQUE_USER_PANEL_REQUIRED',
    'VALID_PRIVATE_STORED_FACTS_REQUIRED',
    'VIEWPORT_OVERFLOW',
    'VISIBLE_DATE_ISOLATION_REQUIRED',
    'VISIBLE_PAGE_LENGTH_REQUIRED',
    'VISIBLE_STATUS_ISOLATION_REQUIRED',
}
FIXTURE_STAGES = {"source", "native_inventory", "native_customer21", "native_cleanup", "previous99", "baseline106",
                  "current_e8", "seed_signup_jobs", "browser", "final_preservation", "read", "mutation"}
FIXTURE_REASONS = {
    'actual_anonymous_request_required',
    'actual_api_server_required',
    'actual_baseline106_d8_required',
    'actual_baseline_d8_upgrade_required',
    'actual_current105_model_table_set_required',
    'actual_current_e8_106_required',
    'actual_login_without_limiter_bypass_required',
    'actual_metadata_fields_order_paging_required',
    'actual_native_schema_cleanup_required',
    'actual_payload_free_history_selects_required',
    'actual_private_history_status_required',
    'actual_stored_timestamp_uuid_tie_order_required',
    'all106_rows_sequences_catalog_security_unchanged_required',
    'all7_inbox_empty_required',
    'all_current_native21_cases_required',
    'all_sixteen_actual_boundary_reads_required',
    'at_most_two_logins_each_actor_required',
    'boundary_uses_unchanged_default25_required',
    'bounded_actual_metadata_response_required',
    'bounded_history_paging_required',
    'bounded_history_query_keys_required',
    'bounded_native_output_required',
    'bounded_seed_output_required',
    'current_head_and_server_start_unchanged_required',
    'eleven_canonical_distinct_steps_required',
    'exact_baseline179_required',
    'exact_boundary_cookie_actor_path_required',
    'exact_dependency_inventory_required',
    'exact_eight_real_logins_required',
    'exact_native21_inventory_required',
    'exact_one_history_response_required',
    'exact_one_private_facts_file_removed_required',
    'exact_original_retained_seed_required',
    'exact_readonly_native_helper_required',
    'explicit_native_or_browser_mode_required',
    'four_original_verified_role_identities_required',
    'fresh_owned_database_required',
    'genuine_previous99_required',
    'known_boundary_label_required',
    'native_cases_must_not_skip',
    'native_public_catalog_sequences_security_unchanged_required',
    'native_server_start_unchanged_required',
    'no_existing_signup_jobs_required',
    'no_guard_mutation_or_provider_attempt_required',
    'no_payload_recipient_provider_id_projection_required',
    'only_four_owned_login_bodies_required',
    'only_owned_account_history_targets_required',
    'original99_old_columns_rows_sequences_preserved_required',
    'owned_nonce_required',
    'owned_private_file_inode_required',
    'private_facts_absence_required',
    'private_payload_select_forbidden',
    'real_ui10_1_and_api2_pages_both_locales_required',
    'unused_owned_unknown_user_id_required',
}
NATIVE_METHODS = {
    "test_verified_signup_owner_and_customer_jobs_commit_atomically_without_direct_send",
    "test_each_disabled_flag_preserves_direct_owner_fallback_and_creates_no_jobs",
    "test_google_only_first_new_verified_account_queues_and_existing_link_does_not",
    "test_queue_failure_rolls_back_new_user_and_pending_promotion",
    "test_original_new_user_marker_refuses_committed_accounts_and_unverified_new_users",
    "test_bounded_locale_rejects_unknown_values_before_account_or_provider_work",
    "test_pending_registration_resend_wrong_code_and_unverified_google_do_not_enqueue",
    "test_customer_inquiry_requires_consent_email_real_event_and_excludes_booking",
    "test_public_listing_link_requires_actual_public_record_not_supplied_snapshot",
    "test_customer_dispatch_rechecks_lead_address_consent_internal_status_and_listing",
    "test_signup_dispatch_rechecks_active_verified_account_current_email_and_feature_flag",
    "test_owner_destination_is_rechecked_and_signup_keys_are_distinct_from_leads",
    "test_dependency_claim_matches_signup_identity_not_null_lead_ids",
    "test_customer_failure_is_held_and_only_one_explicit_manual_retry_can_dispatch",
    "test_legacy_lead_admin_views_exclude_signups_and_generic_metadata_has_no_payload_or_contact",
    "test_account_signup_history_is_readonly_id_bound_private_and_deterministically_paged",
    "test_account_signup_history_empty_unknown_and_non_admin_reads_do_not_enqueue_or_transmit",
    "test_database_xor_event_and_signup_step_uniqueness_constraints",
    "test_generic_email_retry_rejects_crm_job_while_legacy_retry_stays_available",
    "test_final_live_migration_preserves_existing_jobs_and_does_not_enqueue_anything",
    "test_donor_signup_migration_preserves_legacy_jobs_and_does_not_enqueue_anything"
}
ERROR_TYPES = {"Failure", "RuntimeError", "ValueError", "AssertionError", "OperationalError", "IntegrityError",
               "ProgrammingError", "StatementError", "InterfaceError", "DataError", "TimeoutError", "OSError",
               "FileNotFoundError", "PermissionError", "ImportError", "ModuleNotFoundError", "KeyError", "TypeError",
               "AttributeError", "JSONDecodeError", "RehearsalFailure", "CalledProcessError", "TimeoutExpired",
               "HTTPException", "UnhandledError"}


def exact(actual, expected):
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return actual.keys() == expected.keys() and all(exact(actual[key], value) for key, value in expected.items())
    if type(expected) is list:
        return len(actual) == len(expected) and all(exact(a, b) for a, b in zip(actual, expected))
    return actual == expected


def pairs(values):
    result = {}
    for key, value in values:
        assert key not in result
        result[key] = value
    return result


def object_json(value, limit):
    assert len(value.encode("utf-8")) <= limit
    result = json.loads(value, object_pairs_hook=pairs, parse_constant=lambda _value: (_ for _ in ()).throw(AssertionError()))
    assert type(result) is dict
    return result


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def fixture_failure(value):
    assert value.keys() == {"stage", "error_type", "reason", "sqlstate"}
    assert value["stage"] in FIXTURE_STAGES and value["error_type"] in ERROR_TYPES
    assert value["reason"] in FIXTURE_REASONS | {"unexpected_failure"}
    assert value["sqlstate"] == "unavailable" or type(value["sqlstate"]) is str and re.fullmatch(r"[0-9A-Z]{5}", value["sqlstate"])


def control_census(value):
    assert value.keys() == {"positive_count", "outside_count", "rows"}
    assert integer(value["positive_count"], 0, 1000000)
    assert integer(value["outside_count"], 0, value["positive_count"])
    assert type(value["rows"]) is list and len(value["rows"]) == min(8, value["outside_count"])
    assert value["positive_count"] > 200 or value["outside_count"] > 0
    prior = -1
    for row in value["rows"]:
        assert type(row) is dict and row.keys() == {"ordinal", "tagCode", "left", "right", "width"}
        assert integer(row["ordinal"], 0, value["positive_count"] - 1) and row["ordinal"] > prior
        prior = row["ordinal"]
        assert integer(row["tagCode"], 0, 3)
        assert integer(row["left"], -100000, 100000) and integer(row["right"], -100000, 100000)
        assert integer(row["width"], 0, 100000) and row["left"] <= row["right"]


def check(mode, contents, native_exit):
    assert mode in {"native", "api", "browser"} and type(native_exit) is int and native_exit == 0
    assert type(contents) is str and len(contents.encode("utf-8")) <= 2 * 1024 * 1024
    seen, geometry, presence = set(), {}, set()
    for line in contents.splitlines():
        if not line.startswith("SIGNUP_HISTORY_"):
            assert "SIGNUP_HISTORY_" not in line and not re.search(r"(?:PROFILE_|INBOX_NATIVE_|GROUP_AUTHOR_).*(?:RESULT|FAILURE|GUARD|COUNTS)", line)
            continue
        prefix, separator, raw = line.partition(" ")
        assert separator and prefix not in {"SIGNUP_HISTORY_BROWSER_ACCEPTANCE", "SIGNUP_HISTORY_FIXTURE_ACCEPTANCE"}
        if prefix in {"SIGNUP_HISTORY_FIXTURE_FAILURE", "SIGNUP_HISTORY_FIXTURE_GUARD"}:
            assert prefix not in seen
            seen.add(prefix)
            fixture_failure(object_json(raw, 512))
            raise AssertionError()
        if prefix == "SIGNUP_HISTORY_NATIVE_FAILED_METHOD":
            # A failed method line never supplies acceptance, and only a source
            # method inventory can be printed by the worker. No raw traceback.
            assert raw in NATIVE_METHODS | {"fixture_setup_or_cleanup"}
            raise AssertionError()
        if prefix == "SIGNUP_HISTORY_BROWSER_FAILURE":
            assert prefix not in seen
            seen.add(prefix)
            fields = raw.split(" ")
            assert len(fields) == 2 and fields[0] in BROWSER_PHASES and fields[1] in BROWSER_REASONS
            raise AssertionError()
        if prefix == "SIGNUP_HISTORY_BROWSER_CONTROL_CENSUS":
            assert mode == "browser" and prefix not in seen
            seen.add(prefix)
            control_census(object_json(raw, 2048))
            raise AssertionError()
        if prefix == "SIGNUP_HISTORY_BROWSER_OBSERVATION":
            assert prefix not in seen
            seen.add(prefix)
            value = object_json(raw, 256)
            assert value.keys() == {"kind", "status"} and value["kind"] in {"read", "login"} and integer(value["status"], 100, 599)
            raise AssertionError()
        if prefix == "SIGNUP_HISTORY_BROWSER_FRAMES":
            assert prefix not in seen
            seen.add(prefix)
            value = object_json(raw, 512)
            assert value.keys() == {"frames"} and type(value["frames"]) is list and len(value["frames"]) <= 8
            assert all(type(row) is dict and row.keys() == {"line", "column"} and integer(row["line"], 1, 100000)
                       and integer(row["column"], 1, 100000) for row in value["frames"])
            raise AssertionError()
        if prefix == "SIGNUP_HISTORY_BROWSER_GEOMETRY":
            assert mode == "browser"
            value = object_json(raw, 512)
            assert value.keys() == {"label", "width", "document"} and value["label"] in GEOMETRY
            assert integer(value["width"], 320, 390) and value["width"] in {320, 390}
            assert integer(value["document"], 0, value["width"] + 1)
            key = value["label"], value["width"]
            assert key not in geometry
            geometry[key] = value["document"]
            continue
        if prefix == "SIGNUP_HISTORY_BROWSER_PRESENCE":
            assert mode == "browser"
            value = object_json(raw, 512)
            assert value.keys() == {"locale", "before", "modeled", "after", "visible"} and value["locale"] in {"en", "es"}
            assert type(value["before"]) is bool and type(value["modeled"]) is bool
            assert value["modeled"] is (not value["before"]) and value["after"] is True and value["visible"] is True
            assert value["locale"] not in presence
            presence.add(value["locale"])
            continue
        expected = {"native": {"SIGNUP_HISTORY_NATIVE_COUNTS": COUNTS, "SIGNUP_HISTORY_NATIVE_RESULT": NATIVE},
                    "api": {"SIGNUP_HISTORY_FIXTURE_RESULT": API},
                    "browser": {"SIGNUP_HISTORY_BROWSER_RESULT": BROWSER}}[mode]
        assert prefix in expected and prefix not in seen
        seen.add(prefix)
        value = object_json(raw, 4096)
        if prefix == "SIGNUP_HISTORY_BROWSER_RESULT":
            assert "blocked_external_requests" in value and integer(value["blocked_external_requests"], 0, 999)
            value = {key: item for key, item in value.items() if key != "blocked_external_requests"}
        assert exact(value, expected[prefix])
    required = {"native": {"SIGNUP_HISTORY_NATIVE_COUNTS", "SIGNUP_HISTORY_NATIVE_RESULT"},
                "api": {"SIGNUP_HISTORY_FIXTURE_RESULT"}, "browser": {"SIGNUP_HISTORY_BROWSER_RESULT"}}[mode]
    assert seen == required
    if mode == "browser":
        assert set(geometry) == {(label, width) for label in GEOMETRY for width in (320, 390)} and presence == {"en", "es"}
    else:
        assert not geometry and not presence
    return True


def emit_safe(mode, contents):
    """Show only validated finite facts, including on a nonzero native exit.

    This is observation, not acceptance. check() still independently rejects
    every failure, duplicate, missing count, bad value and nonzero exit.
    """
    seen = set()
    for line in contents.splitlines():
        prefix, separator, raw = line.partition(" ")
        if not separator or prefix in seen:
            continue
        try:
            if prefix == "SIGNUP_HISTORY_BROWSER_GEOMETRY" and mode == "browser":
                value = object_json(raw, 512)
                assert value.keys() == {"label", "width", "document"} and value["label"] in GEOMETRY
                assert integer(value["width"], 320, 390) and value["width"] in {320, 390}
                assert integer(value["document"], 0, value["width"] + 1)
                key = prefix, value["label"], value["width"]
                if key in seen:
                    continue
                seen.add(key)
                print(prefix + " " + json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)
                continue
            elif prefix == "SIGNUP_HISTORY_BROWSER_PRESENCE" and mode == "browser":
                value = object_json(raw, 512)
                assert value.keys() == {"locale", "before", "modeled", "after", "visible"} and value["locale"] in {"en", "es"}
                assert type(value["before"]) is bool and type(value["modeled"]) is bool
                assert value["modeled"] is (not value["before"]) and value["after"] is True and value["visible"] is True
                key = prefix, value["locale"]
                if key in seen:
                    continue
                seen.add(key)
                print(prefix + " " + json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)
                continue
            elif prefix == "SIGNUP_HISTORY_NATIVE_COUNTS" and mode == "native":
                value = object_json(raw, 512)
                assert value.keys() == COUNTS.keys() and value["suite"] == "customer_delivery" and type(value["expected"]) is int and value["expected"] == 21
                assert all(integer(value[key], 0, 21) for key in ("run", "failures", "errors", "skipped"))
            elif prefix in {"SIGNUP_HISTORY_FIXTURE_FAILURE", "SIGNUP_HISTORY_FIXTURE_GUARD"}:
                value = object_json(raw, 512)
                fixture_failure(value)
            elif prefix == "SIGNUP_HISTORY_BROWSER_CONTROL_CENSUS" and mode == "browser":
                value = object_json(raw, 2048)
                control_census(value)
            elif prefix == "SIGNUP_HISTORY_BROWSER_OBSERVATION" and mode == "browser":
                value = object_json(raw, 256)
                assert value.keys() == {"kind", "status"} and value["kind"] in {"read", "login"} and integer(value["status"], 100, 599)
            elif prefix == "SIGNUP_HISTORY_BROWSER_FRAMES" and mode == "browser":
                value = object_json(raw, 512)
                assert value.keys() == {"frames"} and type(value["frames"]) is list and len(value["frames"]) <= 8
                assert all(type(row) is dict and row.keys() == {"line", "column"} and integer(row["line"], 1, 100000)
                           and integer(row["column"], 1, 100000) for row in value["frames"])
            elif prefix == "SIGNUP_HISTORY_BROWSER_FAILURE" and mode == "browser":
                fields = raw.split(" ")
                assert len(fields) == 2 and fields[0] in BROWSER_PHASES and fields[1] in BROWSER_REASONS
                seen.add(prefix)
                print(prefix + " " + " ".join(fields), flush=True)
                continue
            elif prefix == "SIGNUP_HISTORY_NATIVE_FAILED_METHOD" and mode == "native":
                assert raw in NATIVE_METHODS | {"fixture_setup_or_cleanup"}
                seen.add(prefix)
                print(prefix + " " + raw, flush=True)
                continue
            elif prefix in {"SIGNUP_HISTORY_NATIVE_RESULT", "SIGNUP_HISTORY_FIXTURE_RESULT", "SIGNUP_HISTORY_BROWSER_RESULT"}:
                expected = {"native": ("SIGNUP_HISTORY_NATIVE_RESULT", NATIVE), "api": ("SIGNUP_HISTORY_FIXTURE_RESULT", API),
                            "browser": ("SIGNUP_HISTORY_BROWSER_RESULT", BROWSER)}[mode]
                assert prefix == expected[0]
                value = object_json(raw, 4096)
                copy = dict(value)
                if mode == "browser":
                    assert integer(copy.pop("blocked_external_requests"), 0, 999)
                assert exact(copy, expected[1])
            else:
                continue
            seen.add(prefix)
            print(prefix + " " + json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)
        except Exception:
            continue


if __name__ == "__main__":
    try:
        assert len(sys.argv) == 4
        path = Path(sys.argv[2])
        assert path.is_file() and not path.is_symlink() and path.stat().st_size <= 2 * 1024 * 1024
        raw_exit = sys.argv[3]
        assert re.fullmatch(r"0|[1-9][0-9]{0,2}", raw_exit)
        contents = path.read_text(encoding="utf-8")
        emit_safe(sys.argv[1], contents)
        check(sys.argv[1], contents, int(raw_exit))
        print("SIGNUP_HISTORY_CHECK PASS", flush=True)
    except Exception:
        print("SIGNUP_HISTORY_CHECK REFUSED", flush=True)
        raise SystemExit(1) from None
