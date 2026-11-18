"""Emit bounded aggregates, never arbitrary browser/API logs or fixture values."""

import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
PLAN = json.loads((ROOT / "plan.json").read_text())


def rows(path, prefix):
    assert path.is_file() and not path.is_symlink() and path.stat().st_size <= 1048576
    return [json.loads(line[len(prefix) + 1:]) for line in path.read_text("utf-8").splitlines()
            if line.startswith(prefix + " ")]


def main():
    phase, mode, source, native = sys.argv[1:]
    item = next(value for value in PLAN["phases"] if value["phase"] == phase)
    path = Path(source)
    if mode == "browser":
        if phase == "core":
            result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "accepted-core/check-browser-result.py"), str(path)],
                                    timeout=15, check=False)
            assert result.returncode == 0 and native == "0"
            return
        values = rows(path, item["prefix"])
        assert len(values) == 1
        value = values[0]
        if value.get("status") != "PASS":
            # Codes are fixed assertion tokens; no error message, DOM or body.
            diagnostic = {"phase": phase, "status": "FAIL"}
            for key in ("failed_check", "failed_step", "stage", "error", "error_code", "failure", "line", "source_line"):
                field = value.get(key)
                if isinstance(field, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,100}", field):
                    diagnostic[key] = field
                elif key in ("line", "source_line") and type(field) is int and 1 <= field <= 9999:
                    diagnostic[key] = field
            print("PLATFORM_PHASE_BROWSER_FAILURE " + json.dumps(diagnostic), flush=True)
        assert native == "0" and value["status"] == "PASS" and value["checks"] == item["checks"]
        for key in ("unexpected_mutations", "unexpected_browser_mutations", "page_errors"):
            assert value.get(key, 0) == 0
        assert value.get("external_navigation", False) is False
        if phase in ("owner", "intake"):
            assert value["writes"] == (9 if phase == "owner" else 3)
            assert len(value["geometry"]) == (6 if phase == "owner" else 3)
            for dimensions in value["geometry"]:
                assert set(dimensions) == {"name", "lang", "width", "document", "body"}
                assert re.fullmatch(r"[a-z_]{1,60}", dimensions["name"]) and dimensions["lang"] in ("en", "es")
                assert dimensions["width"] in (320, 390)
                assert all(type(dimensions[key]) is int and 0 < dimensions[key] <= dimensions["width"]
                           for key in ("document", "body"))
            print("PLATFORM_PHASE_GEOMETRY " + json.dumps({"phase": phase, "checks": value["geometry"]}, separators=(",", ":")))
        print("PLATFORM_PHASE_BROWSER_RESULT " + json.dumps({"phase": phase, "status": "PASS",
              "checks": item["checks"], "check_count": len(item["checks"]), "native": 0}, separators=(",", ":")))
    elif mode in ("api", "failure"):
        failures = rows(path, "PLATFORM_PHASE_API_FAILURE")
        assert len(failures) <= 1
        for failure in failures:
            assert set(failure) == {"phase", "stage", "error_type", "line"} and failure["phase"] == phase
            assert all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", failure[key]) for key in ("stage", "error_type"))
            assert failure["line"] is None or type(failure["line"]) is int and 1 <= failure["line"] <= 9999
            print("PLATFORM_PHASE_API_FAILURE " + json.dumps(failure), flush=True)
        if mode == "failure":
            raise SystemExit(1)
        expected = {"phase": phase, "status": "PASS", "source": PLAN["backend"], "tree": PLAN["backend_tree"],
                    "head": PLAN["head"], "private_file_absent": True, "live_provider_calls": 0}
        assert native == "0" and not failures and rows(path, "PLATFORM_PHASE_API_RESULT") == [expected]
        # Every accepted fixture asserts its final authoritative DB/storage facts
        # before returning. Require its genuine PASS marker as well as wrapper exit.
        data = path.read_text("utf-8")
        facts = [json.loads(line.split(" ", 1)[1]) for line in data.splitlines()
                 if "_FIXTURE_RESULT " in line and line.split(" ", 1)[0].isupper()]
        if phase == "core":
            assert not facts
            assert rows(path, "COMMUNICATIONS_BROWSER_PROVIDER_COUNTS") == [{"fake_sdk_calls": 3, "live_calls": 0}]
            expected_counts = {"users": 6, "active_users": 6, "pending_users": 0, "leads": 12, "listings": 2,
                               "legacy_inquiries": 1, "delivery_jobs": 91, "customer_jobs": 11, "uncertain_jobs": 1,
                               "authorized_retries": 2, "historical_jobs": 0, "worker_records": 0}
            assert rows(path, "COMMUNICATIONS_BROWSER_DATABASE") == [expected_counts]
            print("COMMUNICATIONS_BROWSER_DATABASE " + json.dumps(expected_counts, separators=(",", ":")))
        else:
            assert len(facts) == 1 and facts[0]["status"] == "PASS"
            assert facts[0].get("external_transport_attempts", 0) == 0
            assert facts[0].get("unexpected_mutations", 0) == 0
            if phase == "intake":
                assert facts[0] == {"status": "PASS", "users": 3, "listings": 1, "opted_in_preferences": 1,
                    "requests": 1, "recipients": 1, "accepted_recipients": 1, "first_quotes": 1,
                    "retained_actions": 3, "successful_ui_mutations": 3, "no_seeded_requests_or_quotes": True,
                    "live_provider_calls": 0, "incidental_leads_or_jobs": 0, "projects_created": 0,
                    "all_accounts_active": True, "login_file_absent": True}
                print("SERVICE_INTAKE_FIXTURE_RESULT " + json.dumps(facts[0], separators=(",", ":")))
        print("PLATFORM_PHASE_API_RESULT " + json.dumps(expected, separators=(",", ":")))
    else:
        raise RuntimeError("Exact parser mode required")


if __name__ == "__main__":
    main()
