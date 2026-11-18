"""Print only finite synthetic groups aggregates, never account rows or tokens."""
import json
from pathlib import Path
import re
import sys

mode, filename, native = sys.argv[1:]
assert mode in {"browser", "api"} and re.fullmatch(r"[0-9]{1,3}", native)
path = Path(filename)
assert path.is_file() and path.stat().st_size <= 2 * 1024 * 1024
lines = path.read_bytes().splitlines()
failed = False
diagnostics = []
loopback = []
for line in lines:
    if line.startswith(b"SOCIAL_GROUPS_LOOPBACK_STATE "):
        assert mode == "browser" and len(line) <= 256 and len(loopback) < 8
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"before", "modeled", "after", "visible"} and all(type(flag) is bool for flag in value.values())
        assert value["modeled"] is not value["before"]
        loopback.append(value)
    if line.startswith(b"SOCIAL_GROUPS_ENTRY_DIAGNOSTIC "):
        assert mode == "browser" and len(line) <= 8192 and not diagnostics
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"phase", "last_await", "failed_await", "pending_awaits", "ui_errors", "failure_type", "surface", "ready_state", "census_available", "counts", "page_errors", "page_error_types", "responses"}
        assert value["phase"] in {"setup_fixture", "setup_browser", "setup_owner_session", "setup_member_session",
            "setup_professional_session", "setup_admin_session", "entry_document", "entry_presence", "entry_menu", "entry_network_navigation",
            "entry_network_heading", "entry_groups_navigation", "entry_groups_heading",
            "G01_private_create", "G01_name", "G01_description", "G01_area", "G01_alias", "G01_defaults", "G01_geometry",
            "G01_draft_cancel", "G01_submit", "G01_created_header", "G01_settings_tab", "G01_image_upload", "G01_image_save",
            "G01_header_image", "G01_back_index", "G01_index_geometry", "G01_reopen", "G01_detail_geometry",
            "G02_direct_invitation", "G03_private_photos_comments", "G04_roles_moderation", "G05_fragment_invitation",
            "G06_discovery_requests", "G07_publication_draft_version", "G08_open_join_leave_scope", "G09_withdrawal",
            "G10_history_unblock", "G11_archive_revoke", "G12_uncertain_auth_loss"}
        waits = {"none", "response_json", "write_response", "write_action", "foreground_front", "foreground_presence",
            "open_group_control", "open_group_heading", "tab_control", "header_response", "header_control",
            "geometry_viewport", "geometry_measure", "inbox_filter", "member_code_field", "alias_field",
            "membership_adoption", "role_field", "role_adoption", "feed_response", "feed_control", "post_field",
            "post_adoption", "post_image", "held_ready", "held_release", "settings_field", "settings_adoption",
            "back_control", "back_heading", "back_list_response", "back_mine_control", "create_header", "header_image"}
        assert value["last_await"] in waits and value["failed_await"] in waits
        assert isinstance(value["pending_awaits"], list) and len(value["pending_awaits"]) <= 12
        assert len(set(value["pending_awaits"])) == len(value["pending_awaits"]) and set(value["pending_awaits"]) <= waits - {"none"}
        assert set(value["ui_errors"]) == {"error_access", "error_conflict", "error_unknown", "error_unavailable", "error_fields"}
        assert value["failure_type"] in {"TimeoutError", "TypeError", "ReferenceError", "SyntaxError", "Error", "Failure", "other"}
        assert value["surface"] in {"unavailable", "dashboard_root", "property_network", "dashboard_other", "landing", "blank", "other"}
        assert value["ready_state"] in {"unavailable", "loading", "interactive", "complete"} and type(value["census_available"]) is bool
        assert set(value["counts"]) == {"menu_buttons", "sidebars", "network_navigation", "network_headings", "groups_navigation", "groups_headings",
            "create_forms", "group_headers", "settings_headings", "header_images"}
        assert set(value["page_error_types"]) == {"Error", "TypeError", "ReferenceError", "SyntaxError", "RangeError", "other"}
        assert all(type(count) is int and 0 <= count <= 999 for count in [value["page_errors"], *value["counts"].values(), *value["page_error_types"].values(), *value["ui_errors"].values()])
        assert isinstance(value["responses"], list) and len(value["responses"]) <= 36
        identities = set()
        for response in value["responses"]:
            assert set(response) == {"name", "status", "count"}
            assert response["name"] in {"auth_me", "professional_options", "groups", "dashboard_document", "dashboard_script", "dashboard_stylesheet",
                "group_create", "group_edit", "group_header", "group_media_upload", "group_media_read"}
            assert type(response["status"]) is int and 100 <= response["status"] <= 599
            assert type(response["count"]) is int and 1 <= response["count"] <= 999
            identity = (response["name"], response["status"])
            assert identity not in identities
            identities.add(identity)
        diagnostics.append(value)
    if line.startswith(b"SOCIAL_GROUPS_BROWSER_FAILURE "):
        parts = line.decode().split(" ")
        assert len(parts) == 3 and all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", value) for value in parts)
        print(line.decode())
        failed = True
    if line.startswith(b"SOCIAL_GROUPS_FIXTURE_FAILURE "):
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"stage", "error_type"}
        assert all(isinstance(text, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", text) for text in value.values())
        print("SOCIAL_GROUPS_FIXTURE_FAILURE " + json.dumps(value, sort_keys=True))
        failed = True
if diagnostics:
    assert failed
    print("SOCIAL_GROUPS_ENTRY_DIAGNOSTIC " + json.dumps(diagnostics[0], sort_keys=True))
for value in loopback:
    print("SOCIAL_GROUPS_LOOPBACK_STATE " + json.dumps(value, sort_keys=True))
assert native == "0" and not failed
prefix = b"SOCIAL_GROUPS_BROWSER_RESULT " if mode == "browser" else b"SOCIAL_GROUPS_FIXTURE_RESULT "
rows = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
assert len(rows) == 1
value = rows[0]
if mode == "browser":
    assert loopback and all(row["after"] and row["visible"] for row in loopback)
    assert value == {"status": "PASS", "checks": [
        "normal_menu_private_create_and_draft_cancel",
        "direct_invitation_decline_reinvite_and_alias_acceptance",
        "private_photos_group_image_and_photo_comments",
        "group_roles_and_moderation",
        "manual_fragment_invites_signin_accept_revoke",
        "registered_discovery_request_and_approval",
        "publication_review_and_private_draft_version_binding",
        "open_join_leave_without_property_grants",
        "withdrawal_and_next_request_photo_denial",
        "manager_history_unblock_and_reinvite",
        "archive_and_closed_group_access_revocation",
        "auth_loss_uncertain_status_and_second_invite_isolation"],
        "geometry_checks": 16, "locales": ["en", "es"], "page_errors": 0,
        "external_writes": 0, "tracking_requests": 0, "automatic_accepts": 0, "token_leaks": 0}
    geometry = [json.loads(line.split(b" ", 1)[1]) for line in lines
                if line.startswith(b"SOCIAL_GROUPS_BROWSER_GEOMETRY ")]
    expected = {(label, width) for label in ("en_index", "en_detail", "es_index", "es_detail")
                for width in (320, 390, 1366)} | {
                    ("en_create", 320), ("en_members", 320), ("es_publication", 320), ("invite_shell", 320)}
    assert len(geometry) == 16 and {(item["label"], item["width"]) for item in geometry} == expected
    assert all(set(item) == {"label", "width", "document"} and type(item["document"]) is int
               and 0 < item["document"] <= item["width"] + 1 for item in geometry)
    for item in geometry:
        print("SOCIAL_GROUPS_BROWSER_GEOMETRY " + json.dumps(item, sort_keys=True))
else:
    fixed = {"status": "PASS", "schema_tables": 99, "retained_tables_unchanged": 92,
        "owner_contacts_unchanged": 82, "contacts_unchanged": 85, "sources_unchanged": 86,
        "private_profiles_unchanged": 46, "users_unchanged": 5, "accepted_jobs_unchanged": 9,
        "worker_rows_unchanged": 1, "cms_version_unchanged": 7, "floorplan_rows_unchanged": 2,
        "unrelated_projects_unchanged": 1, "unexpected_mutations": 0,
        "external_provider_attempts": 0, "private_files_removed": 2}
    dynamic = {"unique_action_receipts", "successful_group_requests", "group_row_counts",
               "group_rows_sha256", "synthetic_group_objects"}
    assert set(value) == set(fixed) | dynamic
    assert all(value[key] == expected and type(value[key]) is type(expected) for key, expected in fixed.items())
    assert type(value["unique_action_receipts"]) is int and 1 <= value["unique_action_receipts"] <= 64
    assert type(value["successful_group_requests"]) is int and value["unique_action_receipts"] <= value["successful_group_requests"]
    assert type(value["synthetic_group_objects"]) is int and 1 <= value["synthetic_group_objects"] <= 3
    assert isinstance(value["group_rows_sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", value["group_rows_sha256"])
    counts = value["group_row_counts"]
    assert isinstance(counts, dict) and set(counts) == {"network_groups", "network_group_memberships",
        "network_group_invite_links", "network_group_posts", "network_group_comments", "network_group_media", "network_group_actions"}
    assert all(type(count) is int and 1 <= count <= 64 for count in counts.values())
    assert counts["network_groups"] == 1 and counts["network_group_actions"] == value["unique_action_receipts"]
    assert counts["network_group_media"] == value["synthetic_group_objects"]
print(prefix.decode() + json.dumps(value, sort_keys=True))
