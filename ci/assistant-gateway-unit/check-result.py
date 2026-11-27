"""Expose only finite validated unit metadata, never assertion/token strings."""
import ast
import json
from pathlib import Path
import re
import sys

TOOLS = Path(__file__).resolve().parent
PLAN = json.loads((TOOLS / "PLAN.json").read_bytes())
REASONS = {n.value for n in ast.walk(ast.parse((TOOLS / "verify.py").read_bytes()))
           if isinstance(n, ast.Constant) and isinstance(n.value, str)
           and re.fullmatch("[a-z][a-z0-9_]{1,80}_required", n.value)} | {"unexpected_exception"}


def need(value):
    if not value:
        raise ValueError("invalid_or_failed_unit_proof")


def pairs(items):
    result = {}
    for key, value in items:
        need(key not in result)
        result[key] = value
    return result


def exact(value, expected):
    need(type(value) is dict and set(value) == set(expected))
    need(all(type(value[k]) is type(v) and value[k] == v for k, v in expected.items()))


def parse(raw):
    need(type(raw) is bytes and len(raw) <= 4194304 and b"\0" not in raw)
    records = []
    for line in raw.decode("utf-8", errors="strict").splitlines():
        if not line:
            continue
        need(len(line.encode()) <= 4096)
        match = re.fullmatch(r"GATEWAY_UNIT_(COUNTS|FAILED_METHOD|FAILURE|RESULT) (.+)", line)
        need(match is not None)
        kind, value = match[1], json.loads(match[2], object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        need(type(value) is dict)
        if kind == "COUNTS":
            need(set(value) == {"tests", "failures", "errors", "skipped"})
            need(all(type(v) is int and 0 <= v <= 12 for v in value.values()))
        elif kind == "FAILED_METHOD":
            need(set(value) == {"method"} and type(value["method"]) is str
                 and value["method"] in set(PLAN["suite"]["methods"]) | {"fixture_setup_or_cleanup"})
        elif kind == "FAILURE":
            need(set(value) == {"stage", "error_type", "reason"})
            need(type(value["stage"]) is str and value["stage"] in {"source", "suite", "preservation"})
            need(type(value["error_type"]) is str and value["error_type"] in
                 {"Failure", "AssertionError", "RuntimeError", "ValueError", "TypeError", "KeyError", "ImportError", "other"})
            need(type(value["reason"]) is str and value["reason"] in REASONS)
        else:
            expected = {"status": "PASS", "tests": 12, "exact_source": True, "httpx": "0.27.0",
                "real_network_attempts": 0, "real_http_attempts": 0, "actual_model_calls": 0,
                "selected_module_no_app_imports": True, "database_used": False, "model_access_claim": False,
                "cost_entitlement_claim": False, "source_unchanged": True}
            need(set(value) == set(expected) | {"mock_sync_calls", "mock_async_calls"})
            exact({k: value[k] for k in expected}, expected)
            need(type(value["mock_sync_calls"]) is int and type(value["mock_async_calls"]) is int
                 and 0 <= value["mock_sync_calls"] <= 128 and 1 <= value["mock_async_calls"] <= 128
                 and value["mock_sync_calls"] + value["mock_async_calls"] <= 128)
        records.append((kind, value));need(len(records) <= 16)
    for kind in ("COUNTS", "RESULT", "FAILURE"):
        need(sum(k == kind for k, _ in records) <= 1)
    failed = [v["method"] for k, v in records if k == "FAILED_METHOD"]
    need(len(failed) == len(set(failed)))
    return records


def main():
    need(len(sys.argv) == 6)
    name, client, native, running, oom = sys.argv[1:]
    need(all(re.fullmatch(r"(?:0|[1-9][0-9]{0,2})", s) for s in (client, native)))
    need(int(client) <= 255 and int(native) <= 255 and running in {"true", "false"} and oom in {"true", "false"})
    path = Path(name);need(path.is_file() and not path.is_symlink() and path.stat().st_size <= 4194304)
    records = parse(path.read_bytes())
    counts = [v for k, v in records if k == "COUNTS"]
    good = client == native == "0" and running == oom == "false" and len(counts) == 1 and counts[0] == {
        "tests": 12, "failures": 0, "errors": 0, "skipped": 0} and sum(k == "RESULT" for k, _ in records) == 1 \
        and not any(k in {"FAILURE", "FAILED_METHOD"} for k, _ in records)
    for kind, value in records:
        if kind != "RESULT" or good:
            print("GATEWAY_UNIT_VALIDATED_" + kind + " " + json.dumps(value, sort_keys=True, separators=(",", ":")))
    need(good)
    print("GATEWAY_UNIT_CHECK accepted=true")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("GATEWAY_UNIT_CHECK_FAILED reason=INVALID_OR_FAILED_UNIT")
        raise SystemExit(1) from None
