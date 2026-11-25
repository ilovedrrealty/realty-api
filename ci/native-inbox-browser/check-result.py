"""Strict owned receipts only; a native failure or privacy failure cannot pass."""
import json
from pathlib import Path
import re
import sys

mode, filename, native = sys.argv[1:]
assert mode in {"browser", "api"} and re.fullmatch(r"[0-9]{1,3}", native)
path = Path(filename)
assert path.is_file() and not path.is_symlink() and path.stat().st_size <= 2 * 1024 * 1024
lines = path.read_bytes().splitlines()
failed = False
presence, geometry, frames, sql_failures, input_census = [], [], [], [], []
labels = {f"{language}_{screen}" for language in ("en", "es") for screen in (
    "mine", "directory", "thread", "manual", "preferences", "blocks", "admin", "legacy")}
for line in lines:
    if line.startswith(b"INBOX_LOOPBACK_STATE "):
        assert mode == "browser" and len(line) <= 256 and len(presence) < 10
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"before", "modeled", "after", "visible"} and all(type(item) is bool for item in value.values())
        assert value["modeled"] == (not value["before"]) and value["after"] and value["visible"]
        presence.append(value)
    if line.startswith(b"INBOX_BROWSER_GEOMETRY "):
        assert mode == "browser" and len(line) <= 256 and len(geometry) < 32
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"label", "width", "document"} and value["label"] in labels
        assert type(value["width"]) is int and value["width"] in {320, 390}
        assert type(value["document"]) is int and 0 < value["document"] <= value["width"] + 1
        geometry.append(value)
    if line.startswith(b"INBOX_BROWSER_FAILURE "):
        assert mode == "browser" and len(line) <= 256 and re.fullmatch(
            rb"INBOX_BROWSER_FAILURE [A-Za-z][A-Za-z0-9_]{0,95} [A-Z][A-Za-z0-9_]{0,95}", line)
        print(line.decode("ascii")); failed = True
    if line.startswith(b"INBOX_BROWSER_FRAMES "):
        assert mode == "browser" and len(line) <= 1024 and not frames
        value = json.loads(line.split(b" ", 1)[1])
        assert type(value) is dict and set(value) == {"frames"} and type(value["frames"]) is list and len(value["frames"]) <= 8
        assert all(type(row) is dict and set(row) == {"line", "column"} and all(
            type(row[key]) is int and 0 < row[key] <= 100000 for key in row) for row in value["frames"])
        frames.append(value)
        print("INBOX_BROWSER_FRAMES " + json.dumps(value, sort_keys=True)); failed = True
    if line.startswith(b"INBOX_BROWSER_INPUT_CENSUS "):
        assert mode == "browser" and len(line) <= 1536 and not input_census
        value = json.loads(line.split(b" ", 1)[1])
        assert type(value) is dict and value.get("state") in {"observed", "unavailable"}
        assert value.get("locale") in {"en", "es"} and value.get("stage") in {
            locale+"_codepoint_"+action for locale in ("en", "es")
            for action in ("ascii_fill", "ascii_disabled", "astral_fill", "astral_trial", "astral_enabled", "clear")}
        assert value["stage"].startswith(value["locale"]+"_")
        common = {"state", "stage", "locale"}
        booleans = {"present", "visible", "disabled", "read_only", "fieldset_disabled", "focused", "post_disabled",
            "checking", "stale", "access_lost", "read_error", "review_expired", "rate_limit"}
        counts = {"panel_count", "textarea_count", "post_button_count"}
        lengths = {"code_units", "code_points", "max_length"}
        if value["state"] == "unavailable":
            assert set(value) == common
        else:
            assert set(value) == common | booleans | counts | lengths
            assert all(type(value[key]) is bool for key in booleans)
            assert all(type(value[key]) is int and 0 <= value[key] <= 8 for key in counts)
            assert all(type(value[key]) is int and -1 <= value[key] <= 4000 for key in lengths)
            assert value["present"] == (value["textarea_count"] == 1)
            if value["present"]:
                assert 0 <= value["code_points"] <= value["code_units"] <= 4000
            else:
                assert all(value[key] == -1 for key in lengths)
        print("INBOX_BROWSER_INPUT_CENSUS " + json.dumps(value, sort_keys=True)); input_census.append(value); failed = True
    if line.startswith(b"INBOX_FIXTURE_SQL_FAILURE "):
        assert mode == "api" and len(line) <= 512 and not sql_failures
        value = json.loads(line.split(b" ", 1)[1])
        stages = {"source", "previous99", "upgrade106", "seed_users", "seed_profiles", "seed_legacy_listing",
            "seed_legacy_project", "seed_legacy_context", "seed_legacy_recipients", "seed_legacy_award_history",
            "seed_legacy_negotiation_history", "seed_legacy_commit", "seed_native_open", "seed_native_buyer_history",
            "seed_native_member_history", "browser", "authoritative_ledger"}
        assert type(value) is dict and set(value) == {"stage", "sqlstate_kind", "constraint_kind"}
        assert value["stage"] in stages and value["sqlstate_kind"] in {
            "not_null", "foreign_key", "unique", "check_or_trigger", "integrity", "retention", "aborted_transaction", "other"}
        assert value["constraint_kind"] in {"none", "other", "user_email", "user_identity", "user_code", "profile_visibility",
            "profile_wall", "project_property", "project_submission", "request_source", "request_project", "request_recipients", "recipient_bounds"}
        print("INBOX_FIXTURE_SQL_FAILURE " + json.dumps(value, sort_keys=True)); sql_failures.append(value); failed = True
    if line.startswith(b"INBOX_FIXTURE_FAILURE "):
        assert mode == "api" and len(line) <= 512
        value = json.loads(line.split(b" ", 1)[1])
        assert type(value) is dict and set(value) == {"stage", "error_type", "reason"} and all(
            type(item) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,95}", item) for item in value.values())
        print("INBOX_FIXTURE_FAILURE " + json.dumps(value, sort_keys=True)); failed = True

prefix = b"INBOX_BROWSER_RESULT " if mode == "browser" else b"INBOX_FIXTURE_RESULT "
rows = []
for line in lines:
    if line.startswith(prefix):
        assert len(line) <= 4096
        rows.append(json.loads(line[len(prefix):]))
assert native == "0" and not failed and len(rows) == 1
value = rows[0]


def exact(actual, expected):
    assert type(actual) is type(expected)
    if type(expected) is dict:
        assert set(actual) == set(expected)
        for key in expected:
            exact(actual[key], expected[key])
    elif type(expected) is list:
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected):
            exact(left, right)
    else:
        assert actual == expected


if mode == "browser":
    expected = {"status": "PASS", "locales": ["en", "es"], "journeys": 24, "geometry": 32,
        "api_boundary_checks": 16, "successful_commands": 32, "denied_entry_posts": 4,
        "control_transitions": 12, "contexts": 16, "actual_logins": 14,
        "page_errors": 0, "unexpected_mutations": 0, "external_writes": 0, "tracking_requests": 0}
    assert type(value) is dict and set(value) == {*expected, "blocked_external_requests"}
    exact({key: value[key] for key in expected}, expected)
    assert type(value["blocked_external_requests"]) is int and 0 <= value["blocked_external_requests"] <= 999
    assert len(presence) == 10 and len(geometry) == 32 and {(row["label"], row["width"]) for row in geometry} == {
        (label, width) for label in labels for width in (320, 390)}
    for row in presence:
        print("INBOX_LOOPBACK_STATE " + json.dumps(row, sort_keys=True))
    for row in geometry:
        print("INBOX_BROWSER_GEOMETRY " + json.dumps(row, sort_keys=True))
else:
    expected = {"status": "PASS", "schema_tables": 106, "legacy_tables": 99,
        "all_legacy_rows_sequences_catalog_unchanged": True, "inbox_ledger_exact": True, "private_rls_acl": True,
        "protected_owner_contacts_unchanged": 82, "private_profiles_unchanged": 46,
        "contacts_unchanged": 85, "sources_unchanged": 86, "users_unchanged": 7,
        "accepted_jobs_unchanged": 9, "cms_version_unchanged": 7, "floorplans_unchanged": 2,
        "actual_access_boundaries": 8, "actual_logins": 14, "seed_commands": 38, "browser_commands": 32,
        "browser_command_actions": {"conversation_opened": 4, "entry_created": 10, "read_marked": 2,
            "visibility_saved": 4, "block_saved": 4, "review_opened": 8},
        "denied_entry_posts": 4, "control_transitions": 12,
        "inbox_rows": {"preferences": 1, "blocks": 0, "conversations": 3, "entries": 46,
            "read_states": 1, "commands": 70, "audit": 70},
        "preference_version": 4, "unexpected_mutations": 0, "external_provider_attempts": 0, "private_files_removed": 1}
    exact(value, expected)
print(prefix.decode() + json.dumps(value, sort_keys=True))
