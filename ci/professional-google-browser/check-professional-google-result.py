"""Print only finite synthetic Google-profile aggregates, never contact data."""
import json
from pathlib import Path
import re
import sys

mode, filename, native = sys.argv[1:]
assert mode in {"browser", "api"} and re.fullmatch(r"[0-9]{1,3}", native)
path = Path(filename)
assert path.is_file() and path.stat().st_size <= 2 * 1024 * 1024
lines = path.read_bytes().splitlines()
failed, loopback, diagnostics, driver_failures = False, [], [], []
for line in lines:
    if line.startswith(b"PROFESSIONAL_GOOGLE_DRIVER_FAILURE "):
        assert mode == "browser" and len(line) <= 2048 and not driver_failures
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"phase", "reason", "own_frames"}
        assert all(isinstance(value[key], str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", value[key]) for key in ("phase", "reason"))
        assert isinstance(value["own_frames"], list) and len(value["own_frames"]) <= 8
        for frame in value["own_frames"]:
            assert set(frame) == {"line", "column"} and all(type(number) is int and 1 <= number <= 10000 for number in frame.values())
        driver_failures.append(value)
        failed = True
    if line.startswith(b"PROFESSIONAL_GOOGLE_ADMIN_ENTRY_CENSUS "):
        assert mode == "browser" and len(line) <= 1024 and not diagnostics
        value = json.loads(line.split(b" ", 1)[1])
        flags = {"route_matches", "workspace_matches", "en_language", "es_language", "diagnostic_read_error"}
        counts = {"search_label_en", "search_label_es", "heading_en", "heading_es", "forms", "search_inputs", "page_errors"}
        assert set(value) == flags | counts
        assert all(type(value[key]) is bool for key in flags)
        assert all(type(value[key]) is int and 0 <= value[key] <= 999 for key in counts)
        diagnostics.append(value)
        failed = True
    if line.startswith(b"PROFESSIONAL_GOOGLE_LOOPBACK_STATE "):
        assert mode == "browser" and len(line) <= 256 and len(loopback) < 8
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"before", "modeled", "after", "visible"}
        assert all(type(flag) is bool for flag in value.values())
        assert value["modeled"] is not value["before"]
        loopback.append(value)
    if line.startswith(b"PROFESSIONAL_GOOGLE_BROWSER_FAILURE "):
        parts = line.decode().split(" ")
        assert len(parts) == 3 and all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", item) for item in parts)
        print(line.decode())
        failed = True
    if line.startswith(b"PROFESSIONAL_GOOGLE_FIXTURE_FAILURE "):
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"stage", "error_type"}
        assert all(isinstance(item, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", item) for item in value.values())
        print("PROFESSIONAL_GOOGLE_FIXTURE_FAILURE " + json.dumps(value, sort_keys=True))
        failed = True
for row in loopback:
    print("PROFESSIONAL_GOOGLE_LOOPBACK_STATE " + json.dumps(row, sort_keys=True))
for value in diagnostics:
    print("PROFESSIONAL_GOOGLE_ADMIN_ENTRY_CENSUS " + json.dumps(value, sort_keys=True))
for value in driver_failures:
    print("PROFESSIONAL_GOOGLE_DRIVER_FAILURE " + json.dumps(value, sort_keys=True))
assert native == "0" and not failed
prefix = b"PROFESSIONAL_GOOGLE_BROWSER_RESULT " if mode == "browser" else b"PROFESSIONAL_GOOGLE_FIXTURE_RESULT "
rows = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
assert len(rows) == 1
value = rows[0]
if mode == "browser":
    expected = {"status": "PASS", "checks": ["owner_google_validation_private_save",
        "admin_managed_google_private_edit", "explicit_review_publication_and_audience",
        "manual_embed_load_hide_and_external_fallback", "approved_snapshot_and_source_change_remount",
        "consent_withdrawal_and_legacy_blank_compatibility"], "geometry_checks": 8,
        "locales": ["en", "es"], "page_errors": 0, "unexpected_mutations": 0, "external_writes": 0,
        "business_google_requests_before_click": 0, "live_google_fetches": 0,
        "manual_frame_requests": 2, "manual_profile_requests": 1}
    assert set(value) == set(expected) | {"blocked_auth_script_requests"}
    assert all(value[key] == item for key, item in expected.items())
    assert type(value["blocked_auth_script_requests"]) is int and 0 <= value["blocked_auth_script_requests"] <= 999
    assert loopback and all(row["after"] and row["visible"] for row in loopback)
    geometry = [json.loads(line.split(b" ", 1)[1]) for line in lines
                if line.startswith(b"PROFESSIONAL_GOOGLE_BROWSER_GEOMETRY ")]
    assert len(geometry) == 8 and {(item["label"], item["width"]) for item in geometry} == {
        (label, width) for label in ("en_owner_editor", "es_managed_editor", "en_member_view", "es_member_view")
        for width in (320, 390)}
    assert all(set(item) == {"label", "width", "document"} and type(item["document"]) is int
               and 0 < item["document"] <= item["width"] + 1 for item in geometry)
    for row in geometry:
        print("PROFESSIONAL_GOOGLE_BROWSER_GEOMETRY " + json.dumps(row, sort_keys=True))
else:
    assert value == {"status": "PASS", "schema_tables": 99, "protected_owner_contacts_unchanged": 82,
        "contacts_unchanged": 85, "sources_unchanged": 86, "private_profiles_unchanged": 46,
        "legacy_profile_unchanged": 1, "profiles": 49, "users_unchanged": 4,
        "accepted_jobs_unchanged": 9, "worker_rows_unchanged": 1, "cms_version_unchanged": 7,
        "floorplans_unchanged": 2, "successful_profile_writes": 10, "stale_version_conflicts": 1,
        "actual_revision_rows_matched": 10, "unexpected_mutations": 0,
        "external_provider_attempts": 0, "private_files_removed": 1}
print(prefix.decode() + json.dumps(value, sort_keys=True))
