"""Frozen13ff receipt rules, capacity-only threshold change and measured margin."""
from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import re
import sys

PHASES = {"compact", "guards", "stress"}
LIMIT = 1073741824
ORIGINAL = Path(__file__).resolve().parent.parent / "backup-transport-json-native/check-result.py"
ORIGINAL_SHA = "45588f26c30d04a4b1bcb7df993bee0b858cdab9fd59b033ab5f658f17d05fe2"


def require(value):
    if not value:
        raise ValueError("capacity_proof_refused")


def emit(kind, value):
    print("BACKUP_CAPACITY_1G_NATIVE_" + kind + " " + json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)


def limits(filename):
    path = Path(filename)
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 128)
    lines = path.read_bytes().decode("ascii").splitlines()
    require(len(lines) == 2 and all(re.fullmatch("[0-9]{1,12}", value) for value in lines))
    return tuple(map(int, lines))


def observe_limits(phase, when, filename):
    try:
        maximum, swap = limits(filename)
        value = {"state": "observed", "memory_limit_bytes": maximum, "swap_limit_bytes": swap}
    except (OSError, ValueError, UnicodeError):
        value = {"state": "unavailable_or_invalid"}
    emit("LIMIT_OBSERVATION", {"phase": phase, "when": when, **value})


def legacy(arguments):
    require(ORIGINAL.is_file() and not ORIGINAL.is_symlink())
    raw = ORIGINAL.read_bytes()
    require(sha256(raw).hexdigest() == ORIGINAL_SHA and raw.count(b"536870912") == 2)
    # Only the PG upper bound and its reported value change; the old client
    # bound, result/process/guard/privacy/receiver rules remain exact.
    adapted = raw.replace(b"536870912", b"1073741824")
    namespace = {"__name__": "__main__", "__file__": str(ORIGINAL)}
    previous, output = sys.argv[:], StringIO()
    good = True
    try:
        sys.argv = [str(ORIGINAL), *arguments]
        with redirect_stdout(output):
            exec(compile(adapted, str(ORIGINAL), "exec"), namespace)
    except SystemExit as result:
        good = result.code in (None, 0)
    except (AssertionError, OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        good = False
    finally:
        sys.argv = previous
    return good, namespace, output.getvalue()


def failure_only(output):
    for line in output.splitlines():
        if line.startswith("BACKUP_TRANSPORT_JSON_NATIVE_METRIC_DIAGNOSTIC "):
            print(line, flush=True)
        elif line.startswith("BACKUP_TRANSPORT_JSON_NATIVE_RESULT "):
            value = json.loads(line.split(" ", 1)[1])
            if value.get("status") == "FAIL":
                print(line, flush=True)


def main():
    args = sys.argv[1:]
    phase, stage = "invalid", "arguments"
    try:
        if len(args) == 4 and args[0] == "--capacity-preflight":
            _, phase, limit_file, memory_file = args
            require(phase in PHASES)
            stage = "limits"
            require(limits(limit_file) == (LIMIT, 0))
            stage = "legacy"
            good, _namespace, output = legacy(["--memory-preflight", memory_file])
            if not good:
                failure_only(output)
            require(good)
            print(output, end="", flush=True)
            emit("LIMIT", {"status": "PASS", "phase": phase, "when": "before",
                "memory_limit_bytes": LIMIT, "swap_limit_bytes": 0})
            return 0
        if len(args) == 11 and args[0] == "--observed-processes":
            phase = args[1]
            require(phase in PHASES)
            stage = "legacy"
            good, _namespace, output = legacy(args[:9])
            if not good:
                failure_only(output)
            require(good)
            print(output, end="", flush=True)
            observe_limits(phase, "before", args[9])
            observe_limits(phase, "after", args[10])
            return 0
        require(len(args) == 10)
        phase = args[0]
        require(phase in PHASES)
        stage = "legacy"
        good, namespace, output = legacy(args[:8])
        if not good:
            failure_only(output)
        require(good)
        stage = "limits"
        require(limits(args[8]) == limits(args[9]) == (LIMIT, 0))
        stage = "headroom"
        peak = namespace["after_peak"]
        require(type(peak) is int and 0 < peak <= LIMIT)
        margin = LIMIT - peak
        report = {"status": "PASS" if margin > 0 else "FAIL", "phase": phase, "limit_bytes": LIMIT,
            "swap_limit_bytes": 0, "before_current_bytes": namespace["before_current"],
            "before_peak_bytes": namespace["before_peak"], "after_current_bytes": namespace["after_current"],
            "whole_phase_peak_bytes": peak, "headroom_bytes": margin,
            "headroom_ppm": margin * 1000000 // LIMIT, "positive_observed_headroom": margin > 0,
            "oom_events": 0, "oom_kill_events": 0, "oom_group_kill_events": 0,
            "retained_fixture_only": True, "production_capture_authority": False}
        if margin == 0:
            emit("HEADROOM", report)
        require(margin > 0)
        print(output, end="", flush=True)
        emit("HEADROOM", report)
        return 0
    except (AssertionError, OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        emit("REFUSED", {"phase": phase if phase in PHASES else "invalid", "stage": stage,
            "reason": "invalid_or_failed_capacity_proof"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
