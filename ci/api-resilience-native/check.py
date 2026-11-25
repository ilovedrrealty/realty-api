"""Only complete native suites plus actual stopped/zero process facts pass."""
import json
from pathlib import Path
import re
import sys

filename, client_exit, native_exit, running, oom = sys.argv[1:]
assert re.fullmatch(r"[0-9]{1,3}", client_exit) and re.fullmatch(r"[0-9]{1,3}", native_exit)
assert running in {"true", "false"} and oom in {"true", "false"}
path = Path(filename)
assert path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 2 * 1024 * 1024
counts, results, handshakes, failed = [], [], [], False
for line in path.read_bytes().splitlines():
    if line.startswith(b"RESILIENCE_NATIVE_COUNTS "):
        assert len(line) <= 512
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"suite", "tests", "failures", "errors", "skipped", "provider_attempts"}
        assert value["suite"] in {"options", "recovery", "delivery", "customer_copy"}
        assert all(type(number) is int and 0 <= number <= 999 for key, number in value.items() if key != "suite")
        counts.append(value)
        print("RESILIENCE_NATIVE_COUNTS " + json.dumps(value, sort_keys=True))
    elif line.startswith(b"RESILIENCE_NATIVE_FAILED_METHOD "):
        assert re.fullmatch(rb"RESILIENCE_NATIVE_FAILED_METHOD (options|recovery|delivery|customer_copy) (test_[a-z_]{1,180}|fixture_setup_or_cleanup)", line)
        print(line.decode("ascii")); failed = True
    elif line.startswith(b"RESILIENCE_NATIVE_FAILURE "):
        assert len(line) <= 512
        value = json.loads(line.split(b" ", 1)[1])
        assert set(value) == {"stage", "error_type", "reason"} and all(type(item) is str
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,100}", item) for item in value.values())
        print("RESILIENCE_NATIVE_FAILURE " + json.dumps(value, sort_keys=True)); failed = True
    elif line.startswith(b"RESILIENCE_BLACKHOLE_RESULT "):
        assert len(line) <= 512 and not handshakes
        value = json.loads(line.split(b" ", 1)[1])
        expected = {"status": "PASS", "case": "owned_startup_handshake_timeout", "connect_timeout_seconds": 10,
            "accepted_connections": 1, "startup_seen": True, "server_thread_joined": True,
            "application_queries": 0, "external_provider_attempts": 0}
        assert set(value) == set(expected) | {"elapsed_ms"} and type(value["elapsed_ms"]) is int
        assert 8000 <= value["elapsed_ms"] <= 15000
        assert all(value.get(k) == v and type(value.get(k)) is type(v) for k, v in expected.items())
        handshakes.append(value)
    elif line.startswith(b"RESILIENCE_NATIVE_RESULT "):
        assert len(line) <= 1536 and not results
        results.append(json.loads(line.split(b" ", 1)[1]))
expected = {"status": "PASS", "tests": 68, "source_tests": 67, "pure_options_tests": 4,
    "owned_recovery_tests": 3, "delivery_regression_tests": 53, "customer_copy_tests": 7,
    "owned_handshake_cases": 1, "schema_tables": 99, "modeled_tables": 98,
    "owner_contacts_unchanged": 82, "private_profiles_unchanged": 46, "accepted_jobs_unchanged": 9,
    "cms_version_unchanged": 7, "floorplans_unchanged": 2,
    "all_rows_sequences_catalog_unchanged": True, "owned_schemas_removed": True,
    "external_provider_attempts": 0, "tcp_query_deadline_claimed": False}
assert client_exit == native_exit == "0" and running == oom == "false" and not failed
assert counts == [{"suite": label, "tests": count, "failures": 0, "errors": 0, "skipped": 0, "provider_attempts": 0}
    for label, count in (("options", 4), ("recovery", 3), ("delivery", 53), ("customer_copy", 7))]
assert results == [expected] and all(type(results[0][key]) is type(value) for key, value in expected.items())
assert len(handshakes) == 1
print("RESILIENCE_BLACKHOLE_RESULT " + json.dumps(handshakes[0], sort_keys=True))
print("RESILIENCE_NATIVE_RESULT " + json.dumps(expected, sort_keys=True))
