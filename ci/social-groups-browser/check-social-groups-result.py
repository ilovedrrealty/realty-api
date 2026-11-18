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
for line in lines:
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
assert native == "0" and not failed
prefix = b"SOCIAL_GROUPS_BROWSER_RESULT " if mode == "browser" else b"SOCIAL_GROUPS_FIXTURE_RESULT "
rows = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
assert len(rows) == 1
value = rows[0]
if mode == "browser":
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
