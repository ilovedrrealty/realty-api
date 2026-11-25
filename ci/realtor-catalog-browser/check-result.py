"""Emit strict finite owned receipts; nonzero native exits always remain failures."""
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
    "home", "catalog", "realtor_account", "arlyn_account", "member_profile", "admin", "buyer_denied", "owner_denied")}
for line in lines:
    if line.startswith(b"CATALOG_LOOPBACK_STATE "):
        assert mode == "browser" and len(line) <= 256 and len(presence) < 10
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"before", "modeled", "after", "visible"} and all(type(item) is bool for item in value.values())
        assert value["modeled"] == (not value["before"]) and value["after"] and value["visible"]
        presence.append(value)
    if line.startswith(b"CATALOG_BROWSER_GEOMETRY "):
        assert mode == "browser" and len(line) <= 256 and len(geometry) < 32
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"label", "width", "document"} and value["label"] in labels
        assert type(value["width"]) is int and value["width"] in {320, 390}
        assert type(value["document"]) is int and 0 < value["document"] <= value["width"] + 1
        geometry.append(value)
    if line.startswith(b"CATALOG_BROWSER_FAILURE "):
        assert mode == "browser" and len(line) <= 256 and re.fullmatch(
            rb"CATALOG_BROWSER_FAILURE [A-Za-z][A-Za-z0-9_]{0,95} [A-Z][A-Za-z0-9_]{0,95}", line)
        print(line.decode("ascii")); failed = True
    if line.startswith(b"CATALOG_FIXTURE_FAILURE "):
        assert mode == "api" and len(line) <= 512
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"stage", "error_type", "reason"} and all(
            type(item) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,95}", item) for item in value.values())
        print("CATALOG_FIXTURE_FAILURE " + json.dumps(value, sort_keys=True)); failed = True

prefix = b"CATALOG_BROWSER_RESULT " if mode == "browser" else b"CATALOG_FIXTURE_RESULT "
rows = []
for line in lines:
    if line.startswith(prefix):
        assert len(line) <= 2048
        rows.append(json.loads(line[len(prefix):]))
assert native == "0" and not failed and len(rows) == 1
value = rows[0]
if mode == "browser":
    expected = {"status": "PASS", "locales": ["en", "es"], "journeys": 24, "geometry": 32,
        "api_boundary_checks": 16, "portal_link_checks": 8, "portal_absence_checks": 10,
        "canonical_detail_checks": 2, "dirty_guard_checks": 2, "transport_abort_checks": 2,
        "page_errors": 0, "unexpected_mutations": 0, "external_writes": 0, "tracking_requests": 0}
    assert set(value) == {*expected, "blocked_external_requests"} and all(value[key] == item for key, item in expected.items())
    assert all(type(value[key]) is int for key in set(value) - {"status", "locales"})
    assert 0 <= value["blocked_external_requests"] <= 999
    assert len(presence) == 10 and len(geometry) == 32 and {(row["label"], row["width"]) for row in geometry} == {
        (label, width) for label in labels for width in (320, 390)}
    for row in presence:
        print("CATALOG_LOOPBACK_STATE " + json.dumps(row, sort_keys=True))
    for row in geometry:
        print("CATALOG_BROWSER_GEOMETRY " + json.dumps(row, sort_keys=True))
else:
    expected = {"status": "PASS", "schema_tables": 99, "all_rows_sequences_unchanged": True,
        "protected_owner_contacts_unchanged": 82, "private_profiles_unchanged": 46, "contacts_unchanged": 85,
        "sources_unchanged": 86, "users_unchanged": 6, "listings_unchanged": 31, "catalog_inventory": 27,
        "accepted_jobs_unchanged": 9, "cms_version_unchanged": 7, "floorplans_unchanged": 2,
        "actual_access_boundaries": 8, "actual_logins": 10, "unexpected_mutations": 0, "external_provider_attempts": 0, "private_files_removed": 1}
    assert value == expected and type(value["all_rows_sequences_unchanged"]) is bool
    assert all(type(value[key]) is int for key in set(value) - {"status", "all_rows_sequences_unchanged"})
print(prefix.decode() + json.dumps(value, sort_keys=True))
