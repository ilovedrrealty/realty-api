"""Strict finite source-bound Group browser and authoritative API receipts."""
import json
from pathlib import Path
import re
import sys


EXPECTED_API = {
    "status": "PASS", "schema_tables": 106, "model_tables": 105, "retained_native162_run": 45652164070,
    "all_other102_data_tables_sequences_unchanged": True, "exact_group_content_action_ledger": True,
    "all106_catalog_unchanged": True, "server_start_unchanged": True, "private_rls_acl": True,
    "private_function_acl_and_sequence_definitions_unchanged": True, "private_webp_integrity": True,
    "transient_controls_restored": True, "presentation_flag_outside_digest": True,
    "protected_owner_contacts_unchanged": 82, "private_profiles_unchanged": 46, "contacts_unchanged": 85, "sources_unchanged": 86,
    "users_unchanged": 5, "accepted_jobs_unchanged": 9, "cms_version_unchanged": 7, "floorplans_unchanged": 2,
    "groups_unchanged": 2, "memberships_unchanged": 4, "projects_unchanged": 1, "history_unchanged": 1,
    "private_media_unchanged": 4, "seed_actions_unchanged": 16, "browser_actions": 8, "final_actions": 24,
    "new_posts": 2, "new_comments": 2, "ui_mutation_requests": 8, "actual_access_boundaries": 24, "actual_logins": 10,
    "control_transitions": 16, "read_projection_families": 3, "inbox_writes": 0, "unexpected_mutations": 0, "external_provider_attempts": 0,
    "private_facts_removed": 1, "private_webp_files_removed": 4, "private_files_removed": 5,
}
EXPECTED_BROWSER = {
    "status": "PASS", "locales": ["en", "es"], "journeys": 12, "geometry": 16, "contexts": 12, "presence": 6,
    "actual_logins": 10, "ui_mutation_requests": 8, "post_creates": 2, "comment_creates": 2, "comment_hides": 2, "post_hides": 2,
    "uncertain_commits": 2, "resolver_recoveries": 2, "control_transitions": 16, "api_boundary_checks": 24,
    "opted_in_route_families": 7, "legacy_projection_pairs": 6, "legacy_ui_reads": 4,
    "page_errors": 0, "unexpected_mutations": 0, "external_writes": 0, "tracking_requests": 0,
}
LABELS = {locale + "_" + name for locale in ("en", "es") for name in ("feed", "comments", "picture_denied", "receipt")}
# Exact literal stages and failure reasons from the independently reviewed driver.
PHASES = {
    'en_comment_moderation_and_access_intersection',
    'en_comments_geometry_320',
    'en_comments_geometry_390',
    'en_feed_and_legacy_projection',
    'en_feed_geometry_320',
    'en_feed_geometry_390',
    'en_picture_denied_geometry_320',
    'en_picture_denied_geometry_390',
    'en_real_auth_denial_and_restoration',
    'en_real_create_post_and_comment',
    'en_real_private_and_blocked_author',
    'en_real_sessions',
    'en_receipt_geometry_320',
    'en_receipt_geometry_390',
    'en_uncertain_post_moderation_current_resolver',
    'es_comment_moderation_and_access_intersection',
    'es_comments_geometry_320',
    'es_comments_geometry_390',
    'es_feed_and_legacy_projection',
    'es_feed_geometry_320',
    'es_feed_geometry_390',
    'es_picture_denied_geometry_320',
    'es_picture_denied_geometry_390',
    'es_real_auth_denial_and_restoration',
    'es_real_create_post_and_comment',
    'es_real_private_and_blocked_author',
    'es_real_sessions',
    'es_receipt_geometry_320',
    'es_receipt_geometry_390',
    'es_uncertain_post_moderation_current_resolver',
    'final',
    'fixture',
    'launch',
}
REASONS = {
    'ACTUAL_ACCOUNT_ROLE_REQUIRED',
    'ACTUAL_ASSOCIATED_LABEL_COPY_REQUIRED',
    'ACTUAL_COMMIT_ABORT_BEFORE_RESOLUTION_REQUIRED',
    'ACTUAL_GROUP_NO_STORE_REQUIRED',
    'ACTUAL_LOCALIZED_COPY_REQUIRED',
    'ACTUAL_ORDERED_CONTROL_REQUIRED',
    'ACTUAL_ORIGINAL_ACTION_RECEIPT_REQUIRED',
    'ACTUAL_SEED_COMMENT_REQUIRED',
    'ACTUAL_SEED_POST_REQUIRED',
    'ALL_ROUTE_FAMILIES_AND_COMPATIBILITY_PROOFS_REQUIRED',
    'AUTHOR_PRESENTATION_ONLY_PROJECTION_REQUIRED',
    'BOUNDED_OWNED_FIXTURE_REQUIRED',
    'CONTROL_OVERFLOW',
    'EXACT_AUTHENTICATED_UNCERTAIN_WRITE_REQUIRED',
    'EXACT_EIGHT_UI_COMMANDS_REQUIRED',
    'EXACT_GEOMETRY_SESSION_BOUNDARIES_REQUIRED',
    'EXACT_GROUP_ALIAS_PRESERVED_REQUIRED',
    'EXACT_MODERATION_CONFIRM_REQUIRED',
    'EXACT_OLD_NEW_GROUP_AUTHOR_REQUIRED',
    'EXACT_OWNED_FIXTURE_SCOPE_REQUIRED',
    'EXACT_SOURCE_FACTS_REQUIRED',
    'EXACT_TWELVE_JOURNEYS_REQUIRED',
    'FINITE_LABELLED_CONTROL_REQUIRED',
    'FINITE_PHASE_REQUIRED',
    'FIXED_GROUP_ID_REQUIRED',
    'FIXED_NAVIGATION_REQUIRED',
    'FIXED_READ_REQUIRED',
    'FROZEN_ORIGINAL_MODERATION_REQUIRED',
    'GROUP_ALIAS_WITHOUT_PROFILE_ALIAS_LEAK_REQUIRED',
    'GROUP_MODERATION_REMAINS_AUTHORIZED_REQUIRED',
    'LOOPBACK_PRESENCE_REQUIRED',
    'NEW_CLIENT_OPT_IN_BEFORE_LEGACY_HANDOFF_REQUIRED',
    'ORIGINAL_OPTED_IN_SUBMISSION_REQUIRED',
    'PRIVATE_BROWSER_ISOLATION_REQUIRED',
    'PROCESS_ERROR',
    'REAL_BOUNDARY_STATUS_REQUIRED',
    'REAL_COMMENT_HIDE_VERSION_REQUIRED',
    'REAL_COOKIE_LOGIN_REQUIRED',
    'REAL_GROUP_DENIAL_CLEARS_ALL_PRIVATE_PROJECTION_REQUIRED',
    'REAL_LEGACY_UI_READS_REQUIRED',
    'REAL_NONEMPTY_PROJECTION_PAIR_REQUIRED',
    'RESOLVED_ATTEMPT_CLEARED_REQUIRED',
    'SAME_CURRENT_RESOLVER_WITHOUT_RESEND_REQUIRED',
    'SECONDARY_DENIAL_PRESERVES_GROUP_CONTENT_REQUIRED',
    'SOURCE_ORDERED_CONTROL_REQUIRED',
    'STAGE_DEADLINE',
    'TIMEOUT',
    'UNIQUE_ACTUAL_CONTENT_REQUIRED',
    'UNIQUE_GEOMETRY_REQUIRED',
    'UNIQUE_GROUP_CURRENT_REFRESH_REQUIRED',
    'UNIQUE_GROUP_FEED_REQUIRED',
    'UNIQUE_GROUP_HEADER_REQUIRED',
    'UNIQUE_NORMAL_COMMENT_COMPOSER_REQUIRED',
    'UNIQUE_NORMAL_POST_COMPOSER_REQUIRED',
    'UNIQUE_POST_MODERATION_REQUIRED',
    'UNIQUE_SCOPED_LABELLED_CONTROL_REQUIRED',
    'VIEWPORT_OVERFLOW',
}
API_STAGES = {"source", "previous99", "baseline106", "upgrade_e8", "seed_private_scope", "seed_groups", "browser", "native_ledger"}
API_REASONS = {
    'actual_api_server_required',
    'actual_committed_action_timestamp_required',
    'actual_expected_native_write_status_required',
    'actual_login_without_limiter_bypass_required',
    'actual_profile_choice_without_cover_fallback_required',
    'actual_reference106_upgrade_required',
    'actual_signed_in_admin_control_required',
    'all106_catalog_e8_function_acl_and_all_sequence_definitions_exact_required',
    'all_four_private_webp_bytes_inode_hashes_exact_required',
    'all_ordered_actor_action_status_bindings_required',
    'all_other102_data_and_all_sequence_values_exact_required',
    'all_private_fixture_files_absent_required',
    'all_seven_inbox_tables_unchanged_empty_required',
    'all_twentyfour_exact_real_no_store_boundaries_required',
    'all_ui_content_final_moderated_v2_required',
    'both_locales_actual_default_and_opted_read_projection_required',
    'boundary_requires_actual_avatar_deselection_required',
    'boundary_requires_actual_private_author_required',
    'boundary_requires_actual_unverified_viewer_required',
    'boundary_requires_exact_bilateral_block_required',
    'boundary_requires_members_selected_author_required',
    'bounded_actual_read_projection_required',
    'bounded_actual_write_response_required',
    'bounded_group_read_response_required',
    'bounded_seed_output_required',
    'comment_moderation_before_uncertain_post_required',
    'complete_eight_command_sixteen_control_ledger_required',
    'control_changes_only_declared_field_required',
    'declared_seed_scope_counts_required',
    'deselection_keeps_real_cover_selected_required',
    'exact_actor_body_scope_and_created_content_required',
    'exact_actor_operation_endpoint_required',
    'exact_actual_cookie_actor_and_boundary_required',
    'exact_block_before_removal_required',
    'exact_control_before_value_required',
    'exact_control_write_interleave_required',
    'exact_declared_block_removed_required',
    'exact_dependency_inventory_required',
    'exact_final24_actions_four_posts_four_comments_no_resend_required',
    'exact_five_private_files_removed_required',
    'exact_hidden_state_and_version_only_required',
    'exact_native_comment_body_state_required',
    'exact_native_post_body_state_required',
    'exact_one_immutable_action_per_browser_command_required',
    'exact_ordered_control_required',
    'exact_original_native_action_receipt_required',
    'exact_original_submission_and_body_required',
    'exact_readonly_native_helper_required',
    'exact_reference179_required',
    'exact_seed_actor_operation_inventory_required',
    'exact_sixteen_seed_actions_four_memberships_required',
    'exact_source_control_body_required',
    'exact_submission_body_fields_required',
    'finite_actual_boundary_required',
    'finite_source_control_required',
    'five_actual_account_roles_required',
    'flag_outside_independently_reconstructed_digest_and_versions_required',
    'four_exact_private_webp_rows_and_files_required',
    'fresh_owned_database_required',
    'genuine179_d8_required',
    'genuine99_previous_required',
    'genuinely_anonymous_boundary_required',
    'independent_action_actor_target_body_digest_version_exact_required',
    'known_actual_boundary_label_required',
    'no_duplicate_or_replayed_ui_write_required',
    'no_preexisting_block_to_overwrite_required',
    'no_unobserved_later_group_write_required',
    'normal_group_commands_do_not_advance_sequences_required',
    'one_exact_append_only_content_row_required',
    'one_exact_declared_bilateral_block_required',
    'only_actual_target_moderated_required',
    'only_eight_normal_ui_commands_required',
    'only_expected_group_content_table_changes_required',
    'only_five_owned_login_bodies_required',
    'only_four_new_owned_webp_keys_required',
    'only_known_private_webp_reads_required',
    'original_opted_in_ui_write_required',
    'original_retained_seed_required',
    'owned_nonce_required',
    'owned_private_file_inode_required',
    'owned_signed_in_admin_control_required',
    'private_file_inode_and_size_required',
    'private_webp_digest_required',
    'real_current106_model105_e8_required',
    'real_group_alias_selected_avatar_receipt_required',
    'real_group_read_no_store_required',
    'real_no_store_authorization_status_required',
    'real_old_two_new_three_author_projection_required',
    'real_previous_comment_receipt_required',
    'real_previous_post_receipt_required',
    'real_source_schema_defaults_required',
    'real_write_no_store_required',
    'separate_deliberate_identity_aliases_required',
    'ten_real_cookie_logins_without_bypass_required',
    'unchanged_database_server_start_required',
    'unchanged_viewer_account_role_required',
    'unexpected_failure',
    'zero_provider_unexpected_write_or_guard_failures_required',
}
ERROR_TYPES = {"Failure", "IntegrityError", "OperationalError", "ProgrammingError", "DataError", "TimeoutError", "ValueError", "TypeError", "KeyError", "AssertionError", "RuntimeError", "HTTPException", "ValidationError", "InvalidRequestError", "NoResultFound", "MultipleResultsFound", "ImportError", "ModuleNotFoundError", "AttributeError", "OSError", "FileNotFoundError", "PermissionError", "UnhandledError"}


def unique(items):
    result = {}
    for key, value in items:
        assert key not in result
        result[key] = value
    return result


def decode(line, prefix, limit):
    assert line.startswith(prefix) and len(line.encode()) <= limit
    return json.loads(line[len(prefix):], object_pairs_hook=unique,
                      parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))


def integer(value, minimum=0, maximum=2147483647):
    return type(value) is int and minimum <= value <= maximum


def exact(actual, expected):
    assert type(actual) is type(expected)
    if isinstance(expected, dict):
        assert set(actual) == set(expected)
        for key, value in expected.items():
            exact(actual[key], value)
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected):
            exact(left, right)
    else:
        assert actual == expected


def diagnostic(value, stage_key, allowed_stages):
    assert isinstance(value, dict) and set(value) == {stage_key, "error_type", "reason", "sqlstate"}
    assert value[stage_key] in allowed_stages and isinstance(value["error_type"], str)
    assert value["error_type"] in ERROR_TYPES
    assert value["reason"] in API_REASONS
    assert value["sqlstate"] == "unavailable" or isinstance(value["sqlstate"], str) and re.fullmatch(r"[0-9A-Z]{5}", value["sqlstate"])


def run(mode, path, native):
    assert mode in {"browser", "api"} and re.fullmatch(r"[0-9]{1,3}", native)
    data = Path(path).read_bytes()
    assert len(data) <= 2 * 1024 * 1024
    lines = data.decode("utf-8").splitlines()
    result = []; geometry = []; presence = []; failed = False
    one = set()
    for line in lines:
        if not line.startswith("GROUP_AUTHOR_"):
            continue
        marker = line.split(" ", 1)[0]
        if marker in {"GROUP_AUTHOR_BROWSER_FAILURE", "GROUP_AUTHOR_BROWSER_FRAMES", "GROUP_AUTHOR_BROWSER_OBSERVATION",
                      "GROUP_AUTHOR_FIXTURE_GUARD", "GROUP_AUTHOR_FIXTURE_FAILURE"}:
            assert marker not in one
            one.add(marker); failed = True
            if marker == "GROUP_AUTHOR_BROWSER_FAILURE":
                parts = line.split(" ")
                assert mode == "browser" and len(parts) == 3 and parts[1] in PHASES and parts[2] in REASONS
            elif marker == "GROUP_AUTHOR_BROWSER_FRAMES":
                value = decode(line, marker + " ", 1024)
                assert mode == "browser" and set(value) == {"frames"} and isinstance(value["frames"], list) and 1 <= len(value["frames"]) <= 8
                assert all(set(frame) == {"line", "column"} and integer(frame["line"], 1, 100000) and integer(frame["column"], 1, 100000)
                           for frame in value["frames"])
                assert len({(frame["line"], frame["column"]) for frame in value["frames"]}) == len(value["frames"])
            elif marker == "GROUP_AUTHOR_BROWSER_OBSERVATION":
                value = decode(line, marker + " ", 256)
                assert mode == "browser" and set(value) == {"kind", "status"} and value["kind"] in {"read", "write", "control"}
                assert integer(value["status"], 100, 599)
            else:
                assert mode == "api"
                value = decode(line, marker + " ", 512)
                diagnostic(value, "phase" if marker.endswith("GUARD") else "stage",
                           {"read", "control", "mutation"} if marker.endswith("GUARD") else API_STAGES)
            print(line)
        elif marker == "GROUP_AUTHOR_GEOMETRY":
            assert mode == "browser"
            value = decode(line, marker + " ", 256)
            assert set(value) == {"label", "width", "document"} and value["label"] in LABELS and type(value["width"]) is int
            assert value["width"] in {320, 390} and integer(value["document"], value["width"], value["width"] + 1)
            geometry.append((value["label"], value["width"]))
        elif marker == "GROUP_AUTHOR_PRESENCE":
            assert mode == "browser"
            value = decode(line, marker + " ", 256)
            assert set(value) == {"before", "modeled", "after", "visible"} and all(type(item) is bool for item in value.values())
            assert value["modeled"] == (not value["before"]) and value["after"] and value["visible"]
            presence.append(value)
        elif marker == ("GROUP_AUTHOR_BROWSER_RESULT" if mode == "browser" else "GROUP_AUTHOR_FIXTURE_RESULT"):
            result.append(decode(line, marker + " ", 4096 if mode == "api" else 2048))
        else:
            raise AssertionError()
    assert native == "0" and not failed and len(result) == 1
    if mode == "browser":
        assert set(result[0]) == {*EXPECTED_BROWSER, "blocked_external_requests"}
        assert integer(result[0]["blocked_external_requests"], 0, 999)
        exact({key: result[0][key] for key in EXPECTED_BROWSER}, EXPECTED_BROWSER)
        assert len(geometry) == 16 and set(geometry) == {(label, width) for label in LABELS for width in (320, 390)}
        assert len(presence) == 6
    else:
        exact(result[0], EXPECTED_API)
        assert not geometry and not presence
    print(("GROUP_AUTHOR_BROWSER_ACCEPTED " if mode == "browser" else "GROUP_AUTHOR_API_ACCEPTED ") + json.dumps(result[0], sort_keys=True))


if __name__ == "__main__":
    try:
        assert len(sys.argv) == 4
        run(*sys.argv[1:])
    except Exception:
        print("GROUP_AUTHOR_RESULT_REFUSED", flush=True)
        raise SystemExit(1) from None
