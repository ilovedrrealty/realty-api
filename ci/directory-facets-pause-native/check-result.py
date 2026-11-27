"""Strict stdlib-only parser. Never echo unvalidated raw native output."""
import ast
import json
from pathlib import Path
import re
import sys

TOOLS = Path(__file__).resolve().parent
PLAN = json.loads((TOOLS / "PLAN.json").read_bytes())
SUITES = {"professionals", "catalog", "fallback"}
PHASES = {'claim', 'migrate108', 'inventory', 'baseline106', 'paused_start', 'preservation', 'active_start', 'unit_cleanup', 'unit_suite', 'seed108', 'previous99', 'seed106', 'source'}
ERRORS = {"Failure", "AssertionError", "RuntimeError", "ValueError", "TypeError", "KeyError", "TimeoutExpired",
          "OperationalError", "ProgrammingError", "IntegrityError", "other"}
REASONS = {node.value for node in ast.walk(ast.parse((TOOLS / "verify.py").read_bytes()))
           if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.fullmatch(r"[a-z][a-z0-9_]{1,110}_required", node.value)} | {"unexpected_exception"}
EXPECTED_CANONICAL = {'status': 'PASS', 'mode': 'canonical', 'schema_tables': 108, 'model_tables': 107, 'old_tables': 106, 'owner_contacts_unchanged': 82, 'private_profiles_unchanged': 46, 'accepted_jobs_unchanged': 9, 'cms_version_unchanged': 7, 'floorplans_unchanged': 2, 'prepared_signup_jobs': 11, 'prepared_facet_profiles': 2, 'prepared_events': 8, 'prepared_notices': 6, 'all108_rows_catalog_sequences_security_unchanged': True, 'old106_preserved_across_upgrade': True, 'genuine_paused_bootstrap_startup': True, 'paused503_requests': 7, 'paused_auth_origin_json_checks': 8, 'paused_assignment_worker_starts': 0, 'active_assignment_worker_starts': 1, 'prior_workers_started_drained_each': 3, 'synthetic_delivery_master_enabled': False, 'synthetic_customer_flag': True, 'effective_signup_enabled': False, 'active_forward_source_equivalent': True, 'history_and_facets_private': True, 'no_downgrade_reset_or_replay': True, 'server_start_unchanged': True, 'external_provider_attempts': 0, 'mocked_provider_calls': 0}


def require(value):
    if not value:
        raise ValueError("invalid_or_failed_native")


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result)
        result[key] = value
    return result


def exact(actual, expected):
    require(type(actual) is dict and set(actual) == set(expected))
    for key, value in expected.items():
        require(type(actual[key]) is type(value) and actual[key] == value)


def number(value, maximum=200):
    require(type(value) is int and 0 <= value <= maximum)


def validate_record(kind, value, mode, count, methods):
    require(type(value) is dict)
    if kind == "COUNTS":
        require(mode in SUITES and set(value) == {"suite", "expected", "run", "failures", "errors", "skipped"})
        exact({"suite": value["suite"], "expected": value["expected"]}, {"suite": mode, "expected": count})
        for key in ("run", "failures", "errors", "skipped"):
            number(value[key], count + 1)
        require(value["run"] <= count)
    elif kind == "FAILED_METHOD":
        require(mode in SUITES and set(value) == {"method"} and type(value["method"]) is str
                and value["method"] in set(methods) | {"fixture_setup_or_cleanup"})
    elif kind == "FAILURE":
        require(set(value) == {"stage", "error_type", "reason", "sqlstate"})
        require(type(value["stage"]) is str and value["stage"] in PHASES)
        require(type(value["error_type"]) is str and value["error_type"] in ERRORS)
        require(type(value["reason"]) is str and value["reason"] in REASONS)
        require(value["sqlstate"] is None or type(value["sqlstate"]) is str and re.fullmatch(r"[A-Z0-9]{5}", value["sqlstate"]))
    elif kind == "RESULT":
        if mode == "canonical":
            exact(value, EXPECTED_CANONICAL)
        else:
            expected = {"status": "PASS", "mode": mode, "tests": count, "exact_current_source": True,
                "model_created_test_schema": mode != "catalog", "pure_catalog_only": mode == "catalog", "genuine108_claim": False,
                "all_owned_schema_cleanup": True, "public_catalog_sequences_security_unchanged": True,
                "server_start_unchanged": True, "external_provider_attempts": 0, "mocked_provider_calls": 0}
            exact(value, expected)
    else:
        require(False)


def parse(raw, mode, count, methods):
    require(type(raw) is bytes and len(raw) <= 4 * 1024 * 1024 and b"\x00" not in raw)
    require(mode in {"canonical", *SUITES} and type(count) is int and 1 <= count <= 200
            and len(methods) == len(set(methods)) == count)
    records = []
    for line in raw.decode("utf-8", errors="strict").splitlines():
        if not line:
            continue
        require(len(line.encode()) <= 4096)
        match = re.fullmatch(r"DIRECTORY_NATIVE_(COUNTS|FAILED_METHOD|FAILURE|RESULT) (.+)", line)
        require(match is not None)
        value = json.loads(match[2], object_pairs_hook=pairs,
                           parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        validate_record(match[1], value, mode, count, methods)
        records.append((match[1], value))
        require(len(records) <= count + 4)
    for kind in ("COUNTS", "FAILURE", "RESULT"):
        require(sum(k == kind for k, _ in records) <= 1)
    failed = [value["method"] for kind, value in records if kind == "FAILED_METHOD"]
    require(len(failed) == len(set(failed)))
    return records


def accepted(records, mode, count, client, native, running, oom):
    require(type(client) is int and type(native) is int and 0 <= client <= 255 and 0 <= native <= 255)
    require(type(running) is bool and type(oom) is bool)
    if client or native or running or oom or any(kind in {"FAILED_METHOD", "FAILURE"} for kind, _ in records):
        return False
    results = [value for kind, value in records if kind == "RESULT"]
    if len(results) != 1:
        return False
    counts = [value for kind, value in records if kind == "COUNTS"]
    if mode == "canonical":
        return not counts
    return len(counts) == 1 and counts[0] == {"suite": mode, "expected": count, "run": count,
                                           "failures": 0, "errors": 0, "skipped": 0}


def main():
    require(len(sys.argv) == 7 and sys.argv[1] in {"canonical", *SUITES})
    mode, path, client, native, running, oom = sys.argv[1:]
    require(re.fullmatch(r"(?:0|[1-9][0-9]{0,2})", client) and re.fullmatch(r"(?:0|[1-9][0-9]{0,2})", native))
    require(running in {"true", "false"} and oom in {"true", "false"})
    source = Path(path)
    require(source.is_file() and not source.is_symlink() and source.stat().st_size <= 4 * 1024 * 1024)
    suite = PLAN["source_suites"]["professionals" if mode == "canonical" else mode]
    records = parse(source.read_bytes(), mode, suite["count"], suite["methods"])
    good = accepted(records, mode, suite["count"], int(client), int(native), running == "true", oom == "true")
    for kind, value in records:
        if kind != "RESULT" or good:
            print("DIRECTORY_NATIVE_VALIDATED_" + kind + " " + json.dumps(value, sort_keys=True, separators=(",", ":")))
    require(good)
    print("DIRECTORY_NATIVE_CHECK mode=" + mode + " accepted=true")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("DIRECTORY_NATIVE_CHECK_FAILED reason=INVALID_OR_FAILED_NATIVE")
        raise SystemExit(1) from None
