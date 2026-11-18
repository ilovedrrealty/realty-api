"""Print only allowlisted synthetic browser and API aggregates."""
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
    if line.startswith(b"CONTACT_IMPORT_BROWSER_FAILURE "):
        parts = line.decode().split(" ")
        assert len(parts) == 3 and all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", value) for value in parts)
        print(line.decode()); failed = True
    if line.startswith(b"CONTACT_IMPORT_FIXTURE_FAILURE "):
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"stage", "error_type"}
        assert all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", text) for text in value.values())
        print("CONTACT_IMPORT_FIXTURE_FAILURE " + json.dumps(value)); failed = True
assert native == "0" and not failed
prefix = b"CONTACT_IMPORT_BROWSER_RESULT " if mode == "browser" else b"CONTACT_IMPORT_FIXTURE_RESULT "
rows = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
assert len(rows) == 1
value = rows[0]
if mode == "browser":
    assert value == {"status": "PASS", "checks": ["normal_admin_navigation_and_private_choices",
        "legacy_json_review_cancel_without_changes", "csv_column_mapping_and_field_errors",
        "review_back_cancel_and_explicit_csv_confirmation", "repeated_csv_import_retains_identical_source_rows",
        "real_xlsx_visible_worksheet_and_text_values", "composed_area_source_channel_filters",
        "actual_role_boundaries_and_bilingual_mobile_geometry"],
        "geometry_checks": 8, "locales": ["en", "es"], "successful_final_imports": 3,
        "page_errors": 0, "external_writes": 0, "tracking_requests": 0}
    geometry = [json.loads(line.split(b" ", 1)[1]) for line in lines if line.startswith(b"CONTACT_IMPORT_BROWSER_GEOMETRY ")]
    assert len(geometry) == 8
    assert {(item["label"], item["width"]) for item in geometry} == {
        (label, width) for label in ("en_page", "en_dialog", "es_page", "es_dialog") for width in (320, 390)}
    assert all(set(item) == {"label", "width", "document"} and type(item["document"]) is int
               and item["document"] <= item["width"] + 1 for item in geometry)
    for item in geometry:
        print("CONTACT_IMPORT_BROWSER_GEOMETRY " + json.dumps(item, sort_keys=True))
else:
    assert value == {"status": "PASS", "protected_contacts_unchanged": 82, "protected_sources_unchanged": 82,
        "private_profiles_unchanged": 46, "contacts": 84, "sources": 84, "new_contacts": 2, "new_sources": 2,
        "successful_final_imports": 3, "csv_replay_unchanged": True, "actual_nonadmin_denial_roles": 3,
        "users": 4, "leads": 0, "jobs": 0, "worker_rows": 0, "projects": 0,
        "unexpected_mutations": 0, "external_provider_attempts": 0, "private_files_removed": 4}
print(prefix.decode() + json.dumps(value, sort_keys=True))
