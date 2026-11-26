"""Strict source-bound browser/owned-API receipts; diagnostics never pass."""
import json
from pathlib import Path
import re
import sys

API_EXPECTED = json.loads(r'''{"status":"PASS","schema_tables":106,"model_tables":105,"retained_native134_run":45630574556,"all_other102_data_tables_sequences_unchanged":true,"exact_profile_delta":true,"exact_media_delta":true,"exact_audit_delta":true,"all106_catalog_unchanged":true,"server_start_unchanged":true,"private_rls_acl":true,"private_webp_integrity":true,"transient_controls_restored":true,"two_version_advances_retained":true,"exact_actor_body_version_media_audit_ledger":true,"inbox_writes":0,"protected_owner_contacts_unchanged":82,"private_profiles_unchanged":46,"contacts_unchanged":85,"sources_unchanged":86,"users_unchanged":6,"listings_unchanged":1,"accepted_jobs_unchanged":9,"cms_version_unchanged":7,"floorplans_unchanged":2,"projects_unchanged":33,"history_unchanged":34,"actual_access_boundaries":16,"actual_logins":12,"ui_mutation_requests":20,"upload_successes":6,"profile_saves":8,"stale_refusals":2,"auth_refusals":4,"uncertain_profile_writes":2,"control_transitions":28,"final_profile_versions":[6,6],"private_media_rows":8,"profile_audit_additions":8,"unexpected_mutations":0,"external_provider_attempts":0,"private_facts_removed":1,"private_webp_files_removed":8,"private_files_removed":9}''')
BROWSER_EXPECTED = {"status": "PASS", "locales": ["en", "es"], "journeys": 24, "geometry": 32,
    "contexts": 14, "presence": 12, "actual_logins": 12, "ui_mutation_requests": 20, "upload_successes": 6,
    "profile_saves": 8, "stale_refusals": 2, "auth_refusals": 4, "uncertain_commits": 2,
    "api_boundary_checks": 16, "control_transitions": 28, "denial_projection_checks": 8, "stale_context_checks": 2,
    "page_errors": 0, "unexpected_mutations": 0, "external_writes": 0, "tracking_requests": 0}
LABELS = ("own_profile", "editor", "preview", "member_directory", "other_profile", "projects", "history", "denied")
JOURNEYS = ("own_profile_privacy", "chosen_photos_preview", "save_persist_and_readonly", "replace_remove_and_retention",
    "dirty_guard_discard", "version_conflict_and_uncertainty", "directory_wall_comment_avatars", "own_projects_paging",
    "stakeholder_intersection", "actual_author_history", "current_denial_clears", "stale_cancel_and_error")
STAGES = {"fixture", "launch", "final", "es_login_window", "webkit_preview", "webkit_preview_geometry_320", "webkit_preview_geometry_390"}
STAGES |= {locale + "_" + name for locale in ("en", "es") for name in (*JOURNEYS, "real_sessions")}
STAGES |= {locale + "_" + label + "_geometry_" + str(width) for locale in ("en", "es") for label in LABELS for width in (320, 390)}
API_STAGES = {"source", "previous99", "baseline106", "upgrade_profile", "seed_profile_facts", "browser", "native_ledger"}

API_REASONS = frozenset(['actual_api_server_required', 'actual_audit_creation_interval_and_columns_required', 'actual_e8_106_required', 'actual_existing_media_contract_required', 'actual_login_without_limiter_bypass_required', 'actual_png_dimensions_required', 'actual_private_webp_file_bytes_and_hashes_required', 'actual_profile_receipt_matches_exact_attempt_required', 'actual_reencoded_bytes_digest_dimensions_required', 'actual_reference106_upgrade_required', 'all106_catalog_and_separate_e8_version_unchanged_required', 'all_eight_private_media_rows_exact_retained_required', 'all_other102_data_tables_and_all_sequences_unchanged_required', 'all_private_file_keys_match_retained_media_required', 'all_seven_inbox_tables_empty_required', 'all_synthetic_png_pixels_required', 'all_twenty_exact_completed_ui_requests_required', 'block_boundary_requires_selected_members_profile_required', 'bounded_actual_write_response_required', 'bounded_literal_synthetic_png_required', 'bounded_owned_multipart_required', 'bounded_seed_output_required', 'committed_target_before_final_remove_required', 'current_actual_admin_control_required', 'denied_or_conflicted_write_has_no_native_delta_required', 'exact_actor_action_version_audit_required', 'exact_actual_ui_response_status_required', 'exact_boundary_login_control_and_zero_provider_ledgers_required', 'exact_control_before_value_required', 'exact_control_body_required', 'exact_control_ui_interleave_required', 'exact_cookie_actor_and_boundary_path_required', 'exact_declared_seed_inventory_required', 'exact_dependency_inventory_required', 'exact_eight_append_only_profile_audits_required', 'exact_final_targets_and_all_transient_profile_controls_restored_required', 'exact_media_columns_and_actual_creation_interval_required', 'exact_nine_private_files_removed_required', 'exact_one_append_only_profile_audit_required', 'exact_one_new_private_media_row_required', 'exact_ordered_control_required', 'exact_peer_membership_role_required', 'exact_readonly_native_helper_required', 'exact_reference179_required', 'exact_six_successful_uploads_required', 'exact_target_actor_ui_write_required', 'exact_target_profile_version_delta_required', 'exact_ui_action_endpoint_required', 'exact_upload_form_fields_required', 'exact_upload_name_mime_required', 'exact_version_body_selection_and_alias_required', 'final_profile_before_auth_denials_required', 'finite_boundary_name_required', 'fresh_owned_database_required', 'genuine179_d8_required', 'genuine99_previous_required', 'genuinely_anonymous_boundary_required', 'known_actual_boundary_label_required', 'new_media_exact_target_and_purpose_required', 'only_declared_control_field_changed_required', 'only_known_private_file_read_required', 'only_new_owned_webp_object_keys_required', 'only_six_owned_login_bodies_required', 'only_target_profile_changes_required', 'only_twenty_ui_mutation_requests_required', 'original_retained_seed_required', 'owned_nonce_required', 'owned_private_file_inode_required', 'owned_signed_in_admin_control_required', 'peer_visibility_only_en_required', 'private_file_digest_required', 'private_file_inode_and_size_required', 'private_fixture_file_absence_required', 'real_no_store_authorization_status_required', 'real_uploaded_avatar_boundary_required', 'real_write_no_store_required', 'same_six_opted_profile_rows_required', 'save_does_not_delete_media_or_advance_sequences_required', 'selected_current_replacement_required', 'six_real_verified_role_identities_required', 'source_owned_control_action_required', 'unchanged_database_server_start_required', 'unexpected_failure', 'untouched_es_target_version_required', 'upload_does_not_select_or_audit_profile_required'])
BROWSER_REASONS = frozenset(['ACTUAL_ACCOUNT_ROLE_REQUIRED', 'ACTUAL_ASSOCIATED_LABEL_COPY_REQUIRED', 'ACTUAL_AUTHENTICATED_WRITE_REQUIRED', 'ACTUAL_COMMIT_BEFORE_RESPONSE_LOSS_REQUIRED', 'ACTUAL_HISTORY_ACTOR_AND_CORRECTION_REQUIRED', 'ACTUAL_HISTORY_BODY_ORDER_REQUIRED', 'ACTUAL_LOCALIZED_COPY_REQUIRED', 'ACTUAL_ORDERED_CONTROL_REQUIRED', 'ACTUAL_OWN_PROJECT_PROGRESS_REQUIRED', 'ACTUAL_SCOPED_ORDERED_PAGE_REQUIRED', 'ACTUAL_SHARED_HISTORY_TOTAL_REQUIRED', 'ACTUAL_UNCERTAIN_COMMIT_REQUIRED', 'ACTUAL_WEBKIT_GEOMETRY_REQUIRED', 'AUTHENTICATED_BLOB_IMAGES_REQUIRED', 'BOUNDED_PHOTO_SPEC_REQUIRED', 'CANCELED_DRAFT_NAVIGATION_REQUIRED', 'CANONICAL_AUTHORIZED_WORKSPACE_LINK', 'CLIENT_UNMOUNT_ABORTS_OWNED_REQUEST', 'CONTROL_OVERFLOW', 'CORRECTION_RETAINS_ORIGINAL_ID', 'CURRENT_AUTHORIZED_WALL_AUTHOR_REQUIRED', 'CURRENT_DIRECTORY_SELECTED_AVATAR_REQUIRED', 'CURRENT_PRIVATE_BLOBS_REMOVED', 'CURRENT_PRIVATE_PROJECTION_REMOVED', 'DELIBERATE_NETWORK_ALIAS_REQUIRED', 'DENIED_WRITES_DO_NOT_ADVANCE_PROFILE', 'DRAFT_PREVIEW_LABEL_REQUIRED', 'ENOENT', 'EXACT_FINITE_UI_MUTATION_LEDGER_REQUIRED', 'EXACT_OWNED_UNCERTAIN_WRITE_REQUIRED', 'EXACT_PRIVATE_FIXTURE_SCOPE_REQUIRED', 'EXACT_PRIVATE_IMAGE_COUNT_REQUIRED', 'EXACT_READ_GEOMETRY_SESSION_CONTROLS_REQUIRED', 'EXACT_SOURCE_FACTS_REQUIRED', 'EXACT_TWENTYFOUR_JOURNEYS_REQUIRED', 'EXPLICIT_DISCARD_RESTORES_BASELINE', 'EXPLICIT_FINAL_REMOVAL_REQUIRED', 'EXPLICIT_FIRST_SELECTION_SAVE_REQUIRED', 'EXPLICIT_SYNTHETIC_PHOTO_SPEC_REQUIRED', 'FINITE_LABELLED_CONTROL_REQUIRED', 'FINITE_PHASE_REQUIRED', 'FIXED_MEMBER_CODE_REQUIRED', 'FIXED_NAVIGATION_REQUIRED', 'FIXED_READ_REQUIRED', 'GET', 'HEAD', 'IDAT', 'IEND', 'IHDR', 'INITIAL_PRIVATE_PROFILE_REQUIRED', 'LOOPBACK_PRESENCE_REQUIRED', 'NO_INITIAL_PROFILE_CREATE_REQUIRED', 'NO_WORK_READ_BEFORE_OPEN', 'OBSOLETE_READ_CANNOT_REPLACE_CURRENT_PROFILE', 'OPTIONS', 'OTHER_PROFILE_NO_EDITOR', 'OWNED_BOUNDED_FIXTURE_REQUIRED', 'OWNED_CANCELED_REQUEST_REQUIRED', 'OWNED_WEBKIT_EXISTING_COOKIE_REQUIRED', 'PASS', 'POST', 'PRIVATE_BROWSER_ISOLATION_REQUIRED', 'PROCESS_ERROR', 'PROJECT_ACCESS_INDEPENDENT_OF_TARGET_MEMBERSHIP', 'PROJECT_COMMENT_PRESERVES_PROJECT_ACTOR_NAME', 'PURPOSE_REPLACEMENT_AND_COVER_REMOVAL_REQUIRED', 'PUT', 'READONLY_VIEW_NO_EDITOR', 'REAL_BOUNDARY_STATUS_REQUIRED', 'REAL_COOKIE_LOGIN_REQUIRED', 'REAL_DRAFT_CONFIRM_REQUIRED', 'REAL_EMPTY_SCOPED_RETRY_REQUIRED', 'REAL_PURPOSE_UPLOAD_REQUIRED', 'REPLACED_PROFILE_OBJECT_URLS_REVOKED', 'SECONDARY_DENIAL_PRESERVES_AUTHORIZED_WALL', 'SINGLE_COMMITTED_PROFILE_VERSION_REQUIRED', 'SIX_SYNTHETIC_IDENTITIES_REQUIRED', 'SOCIAL_ALIAS_AND_SELECTED_AUTHOR_PHOTO_REQUIRED', 'SOURCE_ORDERED_CONTROL_REQUIRED', 'STAGE_DEADLINE', 'STAKEHOLDER_CAPABILITIES_PRESERVED', 'STALE_SAVE_LOCK_REQUIRED', 'TARGET_AND_VIEWER_INTERSECTION_REQUIRED', 'TIMEOUT', 'UNCERTAIN_SAVE_LOCK_REQUIRED', 'UNCERTAIN_SAVE_RELOADS_COMMITTED_BODY', 'UNIQUE_GEOMETRY_REQUIRED', 'UNIQUE_PROFILE_ALIAS_REQUIRED', 'UNIQUE_SCOPED_LABELLED_CONTROL_REQUIRED', 'UNIQUE_SOURCE_PANEL_REQUIRED', 'VIEWPORT_OVERFLOW', 'WEBKIT_INVENTORY_REQUIRED', 'WEBKIT_UNCHANGED_PREVIEW_NO_SAVE', 'WORK_REMAINS_LAZY_UNTIL_USER_OPENS'])

def require(value, reason):
    if not value:
        raise AssertionError(reason)


def exact(value, expected):
    if type(value) is not type(expected):
        return False
    if type(value) is dict:
        return set(value) == set(expected) and all(exact(value[key], item) for key, item in expected.items())
    if type(value) is list:
        return len(value) == len(expected) and all(exact(a, b) for a, b in zip(value, expected))
    return value == expected


def unique_pairs(items):
    output = {}
    for key, value in items:
        require(key not in output, "duplicate_json_key")
        output[key] = value
    return output


def record(line, prefix, limit):
    require(len(line) <= limit, "bounded_record_required")
    value = json.loads(line[len(prefix):], object_pairs_hook=unique_pairs)
    require(type(value) is dict, "object_record_required")
    return value


def failure(value, field, choices):
    require(set(value) == {field, "error_type", "reason", "sqlstate"} and type(value[field]) is str and value[field] in choices,
            "exact_source_failure_keys_and_stage_required")
    require(type(value["error_type"]) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", value["error_type"])
            and type(value["reason"]) is str and value["reason"] in API_REASONS, "finite_failure_type_reason_required")
    require(type(value["sqlstate"]) is str and (value["sqlstate"] == "unavailable" or re.fullmatch(r"[0-9A-Z]{5}", value["sqlstate"])),
            "finite_sqlstate_required")


def geometry(value, labels):
    require(set(value) == {"label", "width", "document"} and type(value["label"]) is str and value["label"] in labels,
            "exact_geometry_keys_label_required")
    require(type(value["width"]) is type(value["document"]) is int and value["width"] in {320, 390}
            and 100 <= value["document"] <= value["width"] + 1, "actual_finite_geometry_fit_required")
    return value["label"], value["width"]


def main(arguments):
    require(len(arguments) == 3, "exact_arguments_required")
    mode, filename, native = arguments
    require(mode in {"browser", "api"} and re.fullmatch(r"[0-9]{1,3}", native) and int(native) <= 255, "finite_mode_exit_required")
    path = Path(filename)
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 2 * 1024 * 1024, "bounded_real_log_required")
    result = []
    geometries, webkit, states = [], [], []
    diagnostics, failed = set(), False
    for line in path.read_bytes().splitlines():
        if mode == "browser" and line.startswith(b"PROFILE_LOOPBACK_STATE "):
            value = record(line, b"PROFILE_LOOPBACK_STATE ", 256)
            require(set(value) == {"before", "modeled", "after", "visible"} and all(type(item) is bool for item in value.values())
                    and value["modeled"] is (not value["before"]) and value["after"] and value["visible"], "actual_visible_loopback_state_required")
            require(len(states) < 12, "bounded_unique_page_presence_required")
            states.append(value)
        elif mode == "browser" and line.startswith(b"PROFILE_BROWSER_GEOMETRY "):
            value = record(line, b"PROFILE_BROWSER_GEOMETRY ", 256)
            key = geometry(value, {locale + "_" + name for locale in ("en", "es") for name in LABELS})
            require(key not in geometries and len(geometries) < 32, "unique_main_geometry_required")
            geometries.append(key)
        elif mode == "browser" and line.startswith(b"PROFILE_WEBKIT_GEOMETRY "):
            value = record(line, b"PROFILE_WEBKIT_GEOMETRY ", 256)
            key = geometry(value, {"webkit_preview"})
            require(key not in webkit and len(webkit) < 2, "unique_optional_geometry_required")
            webkit.append(key)
        elif mode == "browser" and line.startswith(b"PROFILE_WEBKIT_NAVIGATION_CENSUS "):
            require("webkit_navigation" not in diagnostics, "one_webkit_navigation_census_required")
            value = record(line, b"PROFILE_WEBKIT_NAVIGATION_CENSUS ", 1024)
            counts = {"document_requests", "document_responses", "document_failures"}
            booleans = {"counts_capped", "expected_request", "expected_response", "expected_failure", "domcontentloaded", "load",
                        "page_closed", "online_known", "online", "owned_dashboard"}
            require(set(value) == counts | booleans | {"kind", "status", "transport_failure", "snapshot", "ready_state", "visibility", "location_kind"},
                    "exact_webkit_navigation_census_keys_required")
            require(type(value["kind"]) is str and value["kind"] == "webkit_navigation"
                    and all(type(value[key]) is int and 0 <= value[key] <= 20 for key in counts)
                    and all(type(value[key]) is bool for key in booleans), "finite_webkit_navigation_census_fields_required")
            require(type(value["status"]) is int and (value["status"] == 0 or 100 <= value["status"] <= 599),
                    "actual_webkit_document_status_required")
            enums = {"transport_failure": {"none", "aborted", "connection_refused", "connection_reset", "connection_closed", "dns", "timed_out", "tls", "blocked", "other"},
                     "snapshot": {"ok", "timeout", "unavailable"}, "ready_state": {"loading", "interactive", "complete", "unavailable"},
                     "visibility": {"visible", "hidden", "unavailable"},
                     "location_kind": {"about_blank", "expected_dashboard", "landing_login", "other_owned", "external", "unavailable"}}
            require(all(type(value[key]) is str and value[key] in choices for key, choices in enums.items()),
                    "finite_webkit_navigation_census_enums_required")
            require(value["online_known"] or not value["online"], "typed_webkit_online_fallback_required")
            print("PROFILE_WEBKIT_NAVIGATION_CENSUS " + json.dumps(value, sort_keys=True), flush=True)
            diagnostics.add("webkit_navigation")
            failed = True
        elif mode == "browser" and line.startswith(b"PROFILE_BROWSER_SAVE_CENSUS "):
            require("save_census" not in diagnostics, "one_save_notice_census_required")
            value = record(line, b"PROFILE_BROWSER_SAVE_CENSUS ", 1024)
            counts = {"saved", "status", "editor", "alias", "forms", "fieldsets", "disabled_fieldsets", "save_buttons",
                      "disabled_save_buttons", "error_unknown", "error_access", "error_fields", "error_conflict", "reload_note"}
            booleans = {"counts_capped", "request_valid", "can_edit_boolean", "can_edit", "receipt_version_valid",
                        "version_advances", "avatar_matches", "cover_matches"}
            require(set(value) == counts | booleans | {"kind", "stage", "put_status", "request_version", "receipt_version"},
                    "exact_save_census_keys_required")
            require(type(value["kind"]) is str and value["kind"] == "save_notice" and type(value["stage"]) is str
                    and value["stage"] in STAGES, "finite_save_census_scope_required")
            require(all(type(value[key]) is int and 0 <= value[key] <= 20 for key in counts)
                    and all(type(value[key]) is bool for key in booleans), "finite_save_census_fields_required")
            require(type(value["put_status"]) is int and value["put_status"] == 200
                    and all(type(value[key]) is int and 0 <= value[key] <= 2147483647
                            for key in ("request_version", "receipt_version")), "finite_save_census_receipt_required")
            require((value["request_valid"] or value["request_version"] == 0)
                    and (value["receipt_version_valid"] or value["receipt_version"] == 0)
                    and (value["can_edit_boolean"] or not value["can_edit"]), "typed_save_census_fallback_required")
            print("PROFILE_BROWSER_SAVE_CENSUS " + json.dumps(value, sort_keys=True), flush=True)
            diagnostics.add("save_census")
            failed = True
        elif mode == "browser" and line.startswith(b"PROFILE_BROWSER_FAILURE "):
            require("failure" not in diagnostics and len(line) <= 256, "one_bounded_browser_failure_required")
            match = re.fullmatch(rb"PROFILE_BROWSER_FAILURE ([A-Za-z][A-Za-z0-9_]{0,95}) ([A-Z][A-Z0-9_]{0,95})", line)
            require(match and match[1].decode() in STAGES and match[2].decode() in BROWSER_REASONS, "finite_source_browser_failure_required")
            print(line.decode(), flush=True)
            diagnostics.add("failure")
            failed = True
        elif mode == "browser" and line.startswith(b"PROFILE_BROWSER_FRAMES "):
            require("frames" not in diagnostics, "one_own_frames_record_required")
            value = record(line, b"PROFILE_BROWSER_FRAMES ", 512)
            require(set(value) == {"frames"} and type(value["frames"]) is list and 1 <= len(value["frames"]) <= 8,
                    "bounded_own_frames_required")
            for item in value["frames"]:
                require(type(item) is dict and set(item) == {"line", "column"} and all(type(number) is int and 1 <= number <= 99999 for number in item.values()),
                        "only_numeric_own_source_frames_required")
            require(len({(item["line"], item["column"]) for item in value["frames"]}) == len(value["frames"]), "unique_own_frames_required")
            print("PROFILE_BROWSER_FRAMES " + json.dumps(value, sort_keys=True), flush=True)
            diagnostics.add("frames")
            failed = True
        elif mode == "browser" and line.startswith(b"PROFILE_BROWSER_OBSERVATION "):
            require("observation" not in diagnostics, "one_public_status_observation_required")
            value = record(line, b"PROFILE_BROWSER_OBSERVATION ", 256)
            require(set(value) == {"kind", "status"} and type(value["kind"]) is str and value["kind"] in {"upload", "save", "read", "control"}
                    and type(value["status"]) is int and 100 <= value["status"] <= 599, "finite_actual_status_observation_required")
            print("PROFILE_BROWSER_OBSERVATION " + json.dumps(value, sort_keys=True), flush=True)
            diagnostics.add("observation")
            failed = True
        elif mode == "api" and line.startswith(b"PROFILE_FIXTURE_GUARD "):
            require("guard" not in diagnostics, "one_source_guard_failure_required")
            value = record(line, b"PROFILE_FIXTURE_GUARD ", 512)
            failure(value, "phase", {"read", "control", "mutation"})
            print("PROFILE_FIXTURE_GUARD " + json.dumps(value, sort_keys=True), flush=True)
            diagnostics.add("guard")
            failed = True
        elif mode == "api" and line.startswith(b"PROFILE_FIXTURE_FAILURE "):
            require("failure" not in diagnostics, "one_fixture_failure_required")
            value = record(line, b"PROFILE_FIXTURE_FAILURE ", 512)
            failure(value, "stage", API_STAGES)
            print("PROFILE_FIXTURE_FAILURE " + json.dumps(value, sort_keys=True), flush=True)
            diagnostics.add("failure")
            failed = True
        elif line.startswith(b"PROFILE_BROWSER_RESULT " if mode == "browser" else b"PROFILE_FIXTURE_RESULT "):
            require(not result, "one_final_result_required")
            result.append(record(line, b"PROFILE_BROWSER_RESULT " if mode == "browser" else b"PROFILE_FIXTURE_RESULT ", 4096))
        elif line.startswith(b"PROFILE_"):
            raise AssertionError("unknown_or_wrong_scope_profile_marker")
    require(native == "0" and not failed and len(result) == 1, "actual_complete_process_success_required")
    if mode == "api":
        require(exact(result[0], API_EXPECTED), "exact_authoritative_api_ledger_required")
    else:
        value = result[0]
        require(set(value) == set(BROWSER_EXPECTED) | {"blocked_external_requests", "webkit"}
                and exact({key: value[key] for key in BROWSER_EXPECTED}, BROWSER_EXPECTED), "exact_browser_metrics_required")
        require(type(value["blocked_external_requests"]) is int and 0 <= value["blocked_external_requests"] <= 999,
                "finite_external_refusal_count_required")
        required_geometries = {(locale + "_" + name, width) for locale in ("en", "es") for name in LABELS for width in (320, 390)}
        require(set(geometries) == required_geometries and len(states) == 12, "all32_geometry_and12_presence_required")
        absent = {"available": False, "journeys": 0, "geometry": 0, "private_images": 0, "native_delivery": False}
        present = {"available": True, "journeys": 1, "geometry": 2, "private_images": 2, "native_delivery": False}
        require((exact(value["webkit"], absent) and not webkit) or (exact(value["webkit"], present)
                and set(webkit) == {("webkit_preview", width) for width in (320, 390)}), "honest_optional_engine_actual_scope_required")
    print(("PROFILE_BROWSER_RESULT " if mode == "browser" else "PROFILE_FIXTURE_RESULT ") + json.dumps(result[0], sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1:])
