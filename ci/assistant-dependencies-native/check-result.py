"""Only finite validated native receipts may leave the private hosted log."""
import ast
import json
from pathlib import Path
import re
import sys

TOOLS = Path(__file__).resolve().parent
PLAN = json.loads((TOOLS / "PLAN.json").read_bytes())
PHASES = {"source", "claim", "inventory", "suite", "cleanup"}
ERRORS = {"Failure", "AssertionError", "RuntimeError", "ValueError", "TypeError", "KeyError",
          "TimeoutExpired", "OperationalError", "ProgrammingError", "IntegrityError", "other"}
REASONS = {node.value for node in ast.walk(ast.parse((TOOLS / "verify.py").read_bytes()))
           if isinstance(node, ast.Constant) and isinstance(node.value, str)
           and re.fullmatch("[a-z][a-z0-9_]{1,110}_required", node.value)} | {"unexpected_exception"}


def require(value):
    if not value:
        raise ValueError("invalid_or_failed_projection_proof")


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result)
        result[key] = value
    return result


def exact(value, expected):
    require(type(value) is dict and set(value) == set(expected))
    require(all(type(value[key]) is type(item) and value[key] == item for key, item in expected.items()))


def expected_result(mode, count):
    return {"status": "PASS", "mode": mode, "tests": count, "exact_current_source": True,
        "pure_facts_cases": count if mode == "facts" else 0,
        "wiring_unit_cases": count if mode == "wiring" else 0,
        "owned_pg_read_cases": count if mode == "reads" else 0,
        "pure_help_cases": count if mode == "help" else 0,
        "selected_module_no_app_imports": mode in {"facts", "help"},
        "model_created_test_schema": mode == "reads", "actual_registered_models": 107 if mode in {"wiring", "reads"} else 0,
        "owned_db_bookkeeping": True, "genuine_migration_claim": False, "http_wire_claim": False,
        "model_or_auth_adapter_claim": False, "all_owned_schema_cleanup": True,
        "public_catalog_sequences_security_unchanged": True, "server_start_unchanged": True,
        "external_provider_attempts": 0, "mocked_provider_calls": 0}


def parse(raw, mode):
    require(type(raw) is bytes and len(raw) <= 4 * 1024 * 1024 and b"\x00" not in raw and mode in PLAN["suites"])
    spec = PLAN["suites"][mode]
    count, methods = spec["count"], spec["methods"]
    require(type(count) is int and 1 <= count <= 50 and len(methods) == len(set(methods)) == count)
    records = []
    for line in raw.decode("utf-8", errors="strict").splitlines():
        if not line:
            continue
        require(len(line.encode()) <= 4096)
        match = re.fullmatch(r"ASSISTANT_DEPENDENCIES_NATIVE_(COUNTS|FAILED_METHOD|FAILURE|RESULT) (.+)", line)
        require(match is not None)
        kind, value = match[1], json.loads(match[2], object_pairs_hook=pairs,
            parse_constant=lambda _item: (_ for _ in ()).throw(ValueError()))
        require(type(value) is dict)
        if kind == "COUNTS":
            require(set(value) == {"suite", "expected", "run", "failures", "errors", "skipped"})
            exact({"suite": value["suite"], "expected": value["expected"]}, {"suite": mode, "expected": count})
            for key in ("run", "failures", "errors", "skipped"):
                require(type(value[key]) is int and 0 <= value[key] <= count + 1)
            require(value["run"] <= count)
        elif kind == "FAILED_METHOD":
            require(set(value) == {"method"} and type(value["method"]) is str
                    and value["method"] in set(methods) | {"fixture_setup_or_cleanup"})
        elif kind == "FAILURE":
            require(set(value) == {"stage", "error_type", "reason", "sqlstate"})
            require(type(value["stage"]) is str and value["stage"] in PHASES)
            require(type(value["error_type"]) is str and value["error_type"] in ERRORS)
            require(type(value["reason"]) is str and value["reason"] in REASONS)
            require(value["sqlstate"] is None or type(value["sqlstate"]) is str and re.fullmatch("[A-Z0-9]{5}", value["sqlstate"]))
        else:
            exact(value, expected_result(mode, count))
        records.append((kind, value))
        require(len(records) <= count + 4)
    for kind in ("COUNTS", "RESULT", "FAILURE"):
        require(sum(item == kind for item, _value in records) <= 1)
    failures = [value["method"] for kind, value in records if kind == "FAILED_METHOD"]
    require(len(failures) == len(set(failures)))
    return records


def accepted(records, mode, client, native, running, oom):
    require(type(client) is int and type(native) is int and 0 <= client <= 255 and 0 <= native <= 255)
    require(type(running) is bool and type(oom) is bool)
    if client or native or running or oom or any(kind in {"FAILURE", "FAILED_METHOD"} for kind, _value in records):
        return False
    count = PLAN["suites"][mode]["count"]
    results = [value for kind, value in records if kind == "RESULT"]
    counts = [value for kind, value in records if kind == "COUNTS"]
    return len(results) == len(counts) == 1 and counts[0] == {
        "suite": mode, "expected": count, "run": count, "failures": 0, "errors": 0, "skipped": 0}


def main():
    require(len(sys.argv) == 7 and sys.argv[1] in PLAN["suites"])
    mode, filename, client, native, running, oom = sys.argv[1:]
    require(all(re.fullmatch(r"(?:0|[1-9][0-9]{0,2})", item) for item in (client, native)))
    require(running in {"true", "false"} and oom in {"true", "false"})
    path = Path(filename)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 4 * 1024 * 1024)
    records = parse(path.read_bytes(), mode)
    good = accepted(records, mode, int(client), int(native), running == "true", oom == "true")
    for kind, value in records:
        if kind != "RESULT" or good:
            print("ASSISTANT_DEPENDENCIES_NATIVE_VALIDATED_" + kind + " " + json.dumps(value, sort_keys=True, separators=(",", ":")))
    require(good)
    print("ASSISTANT_DEPENDENCIES_NATIVE_CHECK mode=" + mode + " accepted=true")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("ASSISTANT_DEPENDENCIES_NATIVE_CHECK_FAILED reason=INVALID_OR_FAILED_NATIVE")
        raise SystemExit(1) from None
