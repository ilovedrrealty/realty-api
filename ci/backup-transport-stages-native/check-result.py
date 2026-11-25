"""Only exact native successes plus actual memory/process facts can pass."""
import json
from pathlib import Path
import re
import sys

def bounded(path, limit):
    path = Path(path)
    assert path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= limit
    return path.read_bytes()


def metric_refused(source, reason, key="not_applicable"):
    assert source in {"preflight", "before", "after"}
    assert reason in {"header_shape", "byte_bounds", "line_shape", "duplicate_key", "unsupported_key", "missing_required_counter", "nonzero_or_missing_oom"}
    assert key in {"low", "high", "max", "oom", "oom_kill", "oom_group_kill", "sock_throttled", "unknown_not_whitelisted", "not_applicable"}
    print("BACKUP_TRANSPORT_STAGES_NATIVE_METRIC_DIAGNOSTIC " + json.dumps({"status": "FAIL", "source": source,
        "reason": reason, "key": key}, sort_keys=True, separators=(",", ":")), flush=True)
    raise AssertionError("actual_cgroup_metric_refused")


def memory(filename, source):
    lines = bounded(filename, 2048).decode("ascii").splitlines()
    if not (len(lines) >= 4 and all(re.fullmatch(r"[0-9]{1,12}", item) for item in lines[:2])):
        metric_refused(source, "header_shape")
    current, peak = map(int, lines[:2])
    if not 0 < current <= peak <= 536870912:
        metric_refused(source, "byte_bounds")
    events = {}
    for line in lines[2:]:
        match = re.fullmatch(r"(low|high|max|oom|oom_kill|oom_group_kill|sock_throttled) ([0-9]{1,12})", line)
        if not match:
            diagnostic = re.fullmatch(r"(low|high|max|oom|oom_kill|oom_group_kill|sock_throttled) ([0-9]{1,12})", line)
            metric_refused(source, "unsupported_key" if diagnostic else "line_shape",
                diagnostic[1] if diagnostic else "unknown_not_whitelisted")
        if match[1] in events:
            metric_refused(source, "duplicate_key", match[1])
        events[match[1]] = int(match[2])
    required = {"low", "high", "max", "oom", "oom_kill", "oom_group_kill"}
    if not required <= set(events):
        metric_refused(source, "missing_required_counter", sorted(required - set(events))[0])
    if not (events["oom"] == 0 and events["oom_kill"] == 0 and events["oom_group_kill"] == 0):
        metric_refused(source, "nonzero_or_missing_oom")
    return current, peak, events


def memory_observation(filename):
    path = Path(filename)
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        return {"state": "unavailable"}
    try:
        lines = bounded(filename, 2048).decode("ascii").splitlines()
        assert len(lines) >= 8 and all(re.fullmatch(r"[0-9]{1,12}", value) for value in lines[:2])
        current, peak = map(int, lines[:2])
        assert 0 < current <= peak
        events = {}
        for line in lines[2:]:
            match = re.fullmatch(r"(low|high|max|oom|oom_kill|oom_group_kill|sock_throttled) ([0-9]{1,12})", line)
            assert match is not None and match[1] not in events
            events[match[1]] = int(match[2])
        assert {"low", "high", "max", "oom", "oom_kill", "oom_group_kill"} <= set(events)
        return {"state": "observed", "current_bytes": current, "peak_bytes": peak,
            "oom": events["oom"], "oom_kill": events["oom_kill"], "oom_group_kill": events["oom_group_kill"],
            "contains_sock_throttled": "sock_throttled" in events}
    except (AssertionError, UnicodeError, ValueError):
        # Diagnostic state only. The original success parser below still refuses
        # absent/malformed/nonzero-OOM/over-limit input and never uses this value.
        return {"state": "invalid_format"}


if len(sys.argv) == 10 and sys.argv[1] == "--observed-processes":
    phase, native, client, postgres, start, before, after, elapsed = sys.argv[2:]
    assert phase in {"compact", "guards", "stress"} and re.fullmatch(r"[0-9]{1,3}", native) and int(native) <= 255
    assert re.fullmatch(r"[0-9]{1,6}", elapsed) and 0 <= int(elapsed) <= 500000
    client_match = re.fullmatch(r"([0-9]{1,3}) (true|false) (true|false)", client)
    pg_match = re.fullmatch(r"(created|running|paused|restarting|removing|exited|dead) ([0-9]{1,3}) (true|false) (true|false) ([0-9]{1,6})", postgres)
    assert client_match and int(client_match[1]) <= 255 and pg_match and int(pg_match[2]) <= 255
    assert start in {"unchanged", "changed", "unavailable"}
    print("BACKUP_TRANSPORT_STAGES_NATIVE_PROCESS_OBSERVATION " + json.dumps({"phase": phase, "client_launch_exit": int(native),
        "client_exit": int(client_match[1]), "client_running": client_match[2] == "true", "client_oom_killed": client_match[3] == "true",
        "postgres_status": pg_match[1], "postgres_exit": int(pg_match[2]), "postgres_running": pg_match[3] == "true",
        "postgres_oom_killed": pg_match[4] == "true", "postgres_restart_count": int(pg_match[5]),
        "server_start_state": start, "phase_elapsed_ms": int(elapsed)}, sort_keys=True, separators=(",", ":")), flush=True)
    for when, filename in (("before", before), ("after", after)):
        print("BACKUP_TRANSPORT_STAGES_NATIVE_CGROUP_OBSERVATION " + json.dumps({"phase": phase, "when": when,
            **memory_observation(filename)}, sort_keys=True, separators=(",", ":")), flush=True)
    raise SystemExit(0)


if len(sys.argv) == 3 and sys.argv[1] == "--memory-preflight":
    _, _, events = memory(sys.argv[2], "preflight")
    print("BACKUP_TRANSPORT_STAGES_NATIVE_MEMORY_PREFLIGHT " + json.dumps({"status": "PASS", "original_six_keys_present": True,
        "contains_sock_throttled": "sock_throttled" in events}, sort_keys=True, separators=(",", ":")), flush=True)
    raise SystemExit(0)

phase, filename, native, client_state, pg_state, before_file, after_file, elapsed = sys.argv[1:]
assert phase in {"compact", "guards", "stress"}
assert re.fullmatch(r"[0-9]{1,3}", native) and re.fullmatch(r"[0-9]{1,6}", elapsed)
assert 0 <= int(elapsed) <= 480000

prefix = b"BACKUP_TRANSPORT_STAGES_NATIVE_RESULT "
reports = []
for line in bounded(filename, 131072).splitlines():
    if line.startswith(prefix):
        assert len(line) <= 2048
        reports.append(json.loads(line[len(prefix):]))
assert len(reports) == 1
report = reports[0]
if report.get("status") == "FAIL":
    assert set(report) == {"status", "phase", "stage", "error_type", "reason", "sqlstate"} and report["phase"] == phase
    assert all(type(value) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,95}", value) for key, value in report.items() if key != "sqlstate")
    assert type(report["sqlstate"]) is str and re.fullmatch(r"[0-9A-Z]{5}|unavailable", report["sqlstate"])
    print(prefix.decode() + json.dumps(report, sort_keys=True))
    raise SystemExit(1)

common = {"status": "PASS", "phase": phase, "postgres_version_num": 170006, "public_tables": 99,
    "server_start_unchanged": True, "production_data_accessed": False, "external_network_available": False,
    "owner_encryption_or_cloud_executed": False, "frozen_sources_unchanged": True}
assert all(report.get(key) == value and type(report.get(key)) is type(value) for key, value in common.items())
client_keys = {"client_start_bytes", "client_end_bytes", "client_whole_phase_peak_bytes", "client_oom_events", "client_oom_kill_events"}
assert all(type(report.get(key)) is int for key in client_keys)
assert 0 < report["client_start_bytes"] <= report["client_whole_phase_peak_bytes"] <= 1073741824
assert 0 < report["client_end_bytes"] <= report["client_whole_phase_peak_bytes"]
assert report["client_oom_events"] == report["client_oom_kill_events"] == 0
common.update({key: report[key] for key in client_keys})
if phase == "guards":
    expected = {**common, "guard_cases": ["row_bound", "byte_bound", "schema_bound", "tableset_bound"],
        "lazy_payload_ctes_per_case": 99, "lazy_map_metadata_ctes_per_case": 2,
        "wrapped_guard_cases": ["row_bound", "byte_bound", "schema_bound", "tableset_bound"],
        "wrapped_lazy_payload_ctes_per_case": 99, "wrapped_lazy_map_metadata_ctes_per_case": 2,
        "wrapped_transport_fences_per_case": 3}
    assert report == expected and all(type(report[key]) is int for key in ("lazy_payload_ctes_per_case", "lazy_map_metadata_ctes_per_case",
        "wrapped_lazy_payload_ctes_per_case", "wrapped_lazy_map_metadata_ctes_per_case", "wrapped_transport_fences_per_case"))
else:
    timings = {"transport_sql_ms", "plaintext_bytes", "compressed_bytes", "receiver_and_validation_ms", "sealer_boundary_calls"}
    extra = ({"parsed_equivalence_excluding_capture_time_only": True, "compact_bytea_roundtrips": 4, "pure_contracts": 4, "exact_payload_byte_hash_tables": 99, "storage_inventory_byte_hash_cases": 3, "storage_inventory_objects": 17, "staged_wrapper_contracts": 4}
        if phase == "compact" else {"contacts": 5633, "sources": 5640, "contact_row_bytes": 9317650,
            "source_row_bytes": 8743189, "storage_objects": 34315, "storage_inventory_byte_hash_cases": 3, "storage_inventory_objects": 34315})
    extra.update(staged_transport_wrapper=True, transport_fence_counts={"snapshot": 1, "digest": 1, "cipher": 1})
    assert type(report.get("transport_fence_counts")) is dict
    assert all(type(value) is int for value in report["transport_fence_counts"].values())
    assert set(report) == set(common) | timings | set(extra) | ({"storage_metadata_bytes"} if phase == "stress" else set())
    assert all(report[key] == value and type(report[key]) is type(value) for key, value in extra.items())
    assert all(type(report[key]) is int for key in timings)
    assert 0 <= report["transport_sql_ms"] <= 60000
    assert report["transport_sql_ms"] <= report["receiver_and_validation_ms"] <= 480000
    assert 0 < report["plaintext_bytes"] <= 67108864 and 0 < report["compressed_bytes"] <= 33554432
    assert report["sealer_boundary_calls"] == 1
    if phase == "stress":
        assert type(report["storage_metadata_bytes"]) is int and 0 < report["storage_metadata_bytes"] <= 67108864

before_current, before_peak, before_events = memory(before_file, "before")
after_current, after_peak, after_events = memory(after_file, "after")
assert after_peak >= before_peak
assert native == "0" and client_state == "0 false false" and pg_state == "true false 0"
print(prefix.decode() + json.dumps(report, sort_keys=True, separators=(",", ":")))
print("BACKUP_TRANSPORT_STAGES_NATIVE_MEMORY " + json.dumps({"phase": phase, "limit_bytes": 536870912,
    "before_current_bytes": before_current, "before_peak_bytes": before_peak,
    "after_current_bytes": after_current, "whole_phase_peak_bytes": after_peak,
    "oom_events": 0, "oom_kill_events": 0, "native": 0, "client_oom_killed": False,
    "contains_sock_throttled_before": "sock_throttled" in before_events, "contains_sock_throttled_after": "sock_throttled" in after_events,
    "postgres_restart_count": 0, "phase_elapsed_ms": int(elapsed)}, sort_keys=True, separators=(",", ":")))
