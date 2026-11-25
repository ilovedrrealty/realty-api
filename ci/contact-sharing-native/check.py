"""Emit only bounded source-owned counts; preserve every native failure."""
import json
from pathlib import Path
import re
import sys

filename, client_exit, native_exit, running, oom = sys.argv[1:]
require_int = lambda value: re.fullmatch(r"[0-9]{1,3}", value)
assert require_int(client_exit) and require_int(native_exit) and running in {"true", "false"} and oom in {"true", "false"}
path = Path(filename)
assert path.is_file() and not path.is_symlink() and path.stat().st_size <= 2 * 1024 * 1024
counts, results, failed = [], [], False
for line in path.read_bytes().splitlines():
    if line.startswith(b"CONTACT_NATIVE_COUNTS "):
        assert len(line) <= 512 and not counts
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"tests", "failures", "errors", "skipped", "provider_attempts"}
        assert all(type(number) is int and 0 <= number <= 999 for number in value.values())
        counts.append(value)
        print("CONTACT_NATIVE_COUNTS " + json.dumps(value, sort_keys=True))
    elif line.startswith(b"CONTACT_NATIVE_FAILED_METHOD "):
        assert re.fullmatch(rb"CONTACT_NATIVE_FAILED_METHOD test_[a-z_]{1,100}", line)
        print(line.decode("ascii")); failed = True
    elif line.startswith(b"CONTACT_NATIVE_FAILURE "):
        assert len(line) <= 512
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"stage", "error_type", "reason"} and all(isinstance(item, str)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,100}", item) for item in value.values())
        print("CONTACT_NATIVE_FAILURE " + json.dumps(value, sort_keys=True)); failed = True
    elif line.startswith(b"CONTACT_NATIVE_RESULT "):
        assert len(line) <= 1024 and not results
        results.append(json.loads(line.split(b" ", 1)[1]))
expected = {"status": "PASS", "tests": 10, "schema_tables": 99, "retained_contacts": 85,
    "retained_sources": 86, "owner_contacts_unchanged": 82, "private_profiles_unchanged": 46,
    "accepted_jobs_unchanged": 9, "cms_version_unchanged": 7, "floorplans_unchanged": 2,
    "new_test_contacts": 66, "new_test_sources": 68, "other_table_rows_and_sequences_unchanged": True,
    "catalog_unchanged": True, "external_provider_attempts": 0}
assert client_exit == native_exit == "0" and running == oom == "false" and not failed
assert counts == [{"tests": 10, "failures": 0, "errors": 0, "skipped": 0, "provider_attempts": 0}]
assert results == [expected] and all(type(results[0][key]) is type(value) for key, value in expected.items())
print("CONTACT_NATIVE_RESULT " + json.dumps(expected, sort_keys=True))
