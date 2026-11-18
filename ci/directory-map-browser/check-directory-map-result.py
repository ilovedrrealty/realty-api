"""Emit only finite synthetic map/browser aggregates, never private rows or logs."""
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
    if line.startswith(b"DIRECTORY_MAP_BROWSER_FAILURE "):
        parts = line.decode().split(" ")
        assert len(parts) == 3 and all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", value) for value in parts)
        print(line.decode()); failed = True
    if line.startswith(b"DIRECTORY_MAP_FIXTURE_FAILURE "):
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"stage", "error_type"}
        assert all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", text) for text in value.values())
        print("DIRECTORY_MAP_FIXTURE_FAILURE " + json.dumps(value)); failed = True
assert native == "0" and not failed
prefix = b"DIRECTORY_MAP_BROWSER_RESULT " if mode == "browser" else b"DIRECTORY_MAP_FIXTURE_RESULT "
rows = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
assert len(rows) == 1
value = rows[0]
if mode == "browser":
    assert value == {"status": "PASS", "checks": ["normal_admin_geography_and_full_scope_counts",
        "exact_specialty_and_service_area_filters", "area_selection_unmapped_clear_and_pagination",
        "existing_csv_preview_cancel_without_import", "member_visibility_overlapping_areas_and_profile",
        "member_multiselect_and_unmapped_records", "stale_reads_and_auth_loss_clear_private_state",
        "bilingual_mobile_desktop_and_actual_role_boundaries"], "geometry_checks": 12,
        "locales": ["en", "es"], "page_errors": 0, "external_writes": 0, "tracking_requests": 0, "final_imports": 0}
    geometry = [json.loads(line.split(b" ", 1)[1]) for line in lines if line.startswith(b"DIRECTORY_MAP_BROWSER_GEOMETRY ")]
    assert len(geometry) == 12
    assert {(item["label"], item["width"]) for item in geometry} == {
        (label, width) for label in ("en_admin", "en_member", "es_admin", "es_member") for width in (320, 390, 1366)}
    assert all(set(item) == {"label", "width", "document"} and type(item["document"]) is int
               and item["document"] <= item["width"] + 1 for item in geometry)
    for item in geometry:
        print("DIRECTORY_MAP_BROWSER_GEOMETRY " + json.dumps(item, sort_keys=True))
else:
    assert value == {"status": "PASS", "contacts": 88, "sources": 88,
        "protected_contacts_unchanged": 82, "protected_sources_unchanged": 82,
        "private_profiles_unchanged": 46, "published_profiles_unchanged": 3, "users": 4,
        "final_imports": 0, "successful_ui_mutations": 0, "actual_nonadmin_denial_roles": 3,
        "leads": 0, "jobs": 0, "worker_rows": 0, "projects": 0, "unexpected_mutations": 0,
        "external_provider_attempts": 0, "private_files_removed": 2}
print(prefix.decode() + json.dumps(value, sort_keys=True))
