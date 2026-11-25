"""Emit finite source-owned receipts only; every original process exit is binding."""
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
presence, geometry = [], []
labels = {f"{language}_{screen}" for language in ("en", "es") for screen in (
    "private_contact", "contact_review", "admin_user_contact", "member_profile", "professional_profile", "public_team")}
for line in lines:
    if line.startswith(b"SHARING_LOOPBACK_STATE "):
        assert mode == "browser" and len(line) <= 256 and len(presence) < 12
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"before", "modeled", "after", "visible"} and all(type(item) is bool for item in value.values())
        assert value["modeled"] == (not value["before"]) and value["after"] and value["visible"]
        presence.append(value)
    if line.startswith(b"SHARING_BROWSER_GEOMETRY "):
        assert mode == "browser" and len(line) <= 256 and len(geometry) < 24
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"label", "width", "document"} and value["label"] in labels
        assert type(value["width"]) is int and value["width"] in {320, 390}
        assert type(value["document"]) is int and 0 < value["document"] <= value["width"] + 1
        geometry.append(value)
    if line.startswith(b"SHARING_BROWSER_FAILURE "):
        assert mode == "browser" and len(line) <= 256 and re.fullmatch(
            rb"SHARING_BROWSER_FAILURE [A-Za-z][A-Za-z0-9_]{0,95} [A-Z][A-Za-z0-9_]{0,95}", line)
        print(line.decode("ascii")); failed = True
    if line.startswith(b"SHARING_FIXTURE_FAILURE "):
        assert mode == "api" and len(line) <= 512
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"stage", "error_type", "reason"} and all(
            type(item) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,95}", item) for item in value.values())
        print("SHARING_FIXTURE_FAILURE " + json.dumps(value, sort_keys=True)); failed = True

prefix = b"SHARING_BROWSER_RESULT " if mode == "browser" else b"SHARING_FIXTURE_RESULT "
rows = []
for line in lines:
    if line.startswith(prefix):
        assert len(line) <= 2048
        rows.append(json.loads(line[len(prefix):]))
assert native == "0" and not failed and len(rows) == 1
value = rows[0]
if mode == "browser":
    expected = {"status": "PASS", "locales": ["en", "es"], "journeys": 20, "geometry": 24,
        "link_checks": 18, "native_calls": 22, "native_share_mode": "instrumented_no_os_delivery",
        "page_errors": 0, "unexpected_mutations": 0, "external_writes": 0, "tracking_requests": 0}
    assert set(value) == {*expected, "blocked_external_requests"} and all(value[key] == item for key, item in expected.items())
    assert all(type(value[key]) is int for key in set(value) - {"status", "locales", "native_share_mode"})
    assert 0 <= value["blocked_external_requests"] <= 999
    assert len(presence) == 12 and len(geometry) == 24 and {(row["label"], row["width"]) for row in geometry} == {
        (label, width) for label in labels for width in (320, 390)}
    for row in presence:
        print("SHARING_LOOPBACK_STATE " + json.dumps(row, sort_keys=True))
    for row in geometry:
        print("SHARING_BROWSER_GEOMETRY " + json.dumps(row, sort_keys=True))
else:
    expected = {"status": "PASS", "schema_tables": 99, "all_rows_sequences_unchanged": True,
        "protected_owner_contacts_unchanged": 82, "private_profiles_unchanged": 46, "contacts_unchanged": 86,
        "sources_unchanged": 87, "published_professionals_unchanged": 2, "users_unchanged": 5,
        "accepted_jobs_unchanged": 9, "cms_version_unchanged": 7, "floorplans_unchanged": 2,
        "actual_access_boundaries": 8, "unexpected_mutations": 0, "external_provider_attempts": 0, "private_files_removed": 1}
    assert value == expected and type(value["all_rows_sequences_unchanged"]) is bool
    assert all(type(value[key]) is int for key in set(value) - {"status", "all_rows_sequences_unchanged"})
print(prefix.decode() + json.dumps(value, sort_keys=True))
