"""Finite privacy-safe result gate; no failed/partial native run can pass."""
import json
from pathlib import Path
import re
import sys

filename, client_exit, native_exit, running, oom = sys.argv[1:]
assert re.fullmatch(r"[0-9]{1,3}", client_exit) and re.fullmatch(r"[0-9]{1,3}", native_exit)
assert running in {"true", "false"} and oom in {"true", "false"}
path = Path(filename)
assert path.is_file() and not path.is_symlink() and path.stat().st_size<=2*1024*1024
counts, results, failed = [], [], False
census_seen = False
for line in path.read_bytes().splitlines():
    if line.startswith(b"INBOX_NATIVE_PARITY_CENSUS "):
        assert len(line)<=1536 and not census_seen
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value)=={"columns", "constraints", "indexes", "samples"}
        assert all(type(value[key]) is int and 0<=value[key]<=999 for key in ("columns", "constraints", "indexes"))
        assert sum(value[key] for key in ("columns", "constraints", "indexes"))>0
        assert type(value["samples"]) is list and 1<=len(value["samples"])<=3
        for sample in value["samples"]:
            assert set(sample)=={"section", "table", "field", "actual_present", "expected_present", "different_positions", "actual_default_kind", "expected_default_kind"}
            assert sample["section"] in {"columns", "constraints", "indexes"}
            assert sample["table"] in {"inbox_preferences", "inbox_blocks", "inbox_conversations", "inbox_commands", "inbox_entries", "inbox_read_states", "inbox_audit"}
            assert type(sample["field"]) is str and re.fullmatch(r"[a-z_][a-z0-9_]{0,100}", sample["field"])
            assert type(sample["actual_present"]) is type(sample["expected_present"]) is bool
            assert sample["actual_present"] or sample["expected_present"]
            assert type(sample["different_positions"]) is list and 1<=len(sample["different_positions"])<=8
            assert all(type(number) is int and 0<=number<=7 for number in sample["different_positions"])
            assert sample["different_positions"]==sorted(set(sample["different_positions"]))
            assert all(sample[key] in {"none", "zero_bare", "zero_bigint_quoted", "now", "other", "not_applicable"} for key in ("actual_default_kind", "expected_default_kind"))
        print("INBOX_NATIVE_PARITY_CENSUS "+json.dumps(value, sort_keys=True))
        census_seen = True; failed = True
    elif line.startswith(b"INBOX_NATIVE_COUNTS "):
        assert len(line)<=512
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value)=={"suite", "tests", "failures", "errors", "skipped", "provider_attempts"}
        assert value["suite"] in {"inbox", "safety", "legacy_award", "legacy_negotiation"}
        assert all(type(number) is int and 0<=number<=999 for key, number in value.items() if key!="suite")
        counts.append(value)
        print("INBOX_NATIVE_COUNTS "+json.dumps(value, sort_keys=True))
    elif line.startswith(b"INBOX_NATIVE_FAILED_METHOD "):
        assert re.fullmatch(rb"INBOX_NATIVE_FAILED_METHOD (inbox|safety|legacy_award|legacy_negotiation) (test_[a-z_]{1,110}|fixture_setup_or_cleanup)", line)
        print(line.decode("ascii")); failed = True
    elif line.startswith(b"INBOX_NATIVE_FAILURE "):
        assert len(line)<=512
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value)=={"stage", "error_type", "reason"} and all(isinstance(item, str)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,100}", item) for item in value.values())
        print("INBOX_NATIVE_FAILURE "+json.dumps(value, sort_keys=True)); failed = True
    elif line.startswith(b"INBOX_NATIVE_RESULT "):
        assert len(line)<=1536 and not results
        results.append(json.loads(line.split(b" ", 1)[1]))
expected = {"status": "PASS", "tests": 27, "inbox_tests": 15, "legacy_award_tests": 5,
    "legacy_negotiation_tests": 5, "hosted_safety_tests": 2, "old_tables": 99, "schema_tables": 106,
    "model_tables": 105, "new_private_tables": 7, "owner_contacts_unchanged": 82,
    "private_profiles_unchanged": 46, "accepted_jobs_unchanged": 9, "cms_version_unchanged": 7,
    "floorplans_unchanged": 2, "old_rows_sequences_catalog_unchanged": True,
    "new_model_ddl_projection_equal": True, "private_rls_acl": True, "empty_downgrade_reupgrade_equal": True,
    "external_provider_attempts": 0}
assert client_exit==native_exit=="0" and running==oom=="false" and not failed
assert counts==[{"suite": label, "tests": count, "failures": 0, "errors": 0, "skipped": 0, "provider_attempts": 0}
    for label, count in (("inbox", 15), ("safety", 2), ("legacy_award", 5), ("legacy_negotiation", 5))]
assert results==[expected] and all(type(results[0][key]) is type(value) for key, value in expected.items())
print("INBOX_NATIVE_RESULT "+json.dumps(expected, sort_keys=True))
