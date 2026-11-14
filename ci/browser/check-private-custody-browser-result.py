"""Validate the exact custody browser journey and its separate database readback."""

import json
import re
import sys
from pathlib import Path


CHECKS = [
    "current_custodian_scope_and_closed_work",
    "pending_offer_private_and_recipient_declines",
    "sender_cancellation_preserves_custody",
    "bilingual_acceptance_transfers_exact_registry_fields",
    "former_scope_revoked_and_history_not_inherited",
    "only_exact_loopback_custody_writes",
]
BROWSER = b"PRIVATE_CUSTODY_BROWSER_RESULT "
DATABASE = b"PRIVATE_CUSTODY_BROWSER_FIXTURE_RESULT "
FAILURE = b"BROWSER_API_FIXTURE_FAILURE "


def reports(path, prefix):
    assert path.is_file() and path.stat().st_size <= 131072
    lines = [line[len(prefix):] for line in path.read_bytes().splitlines() if line.startswith(prefix)]
    assert len(lines) <= 1 and all(len(line) <= 8192 for line in lines)
    return [json.loads(line) for line in lines]


def safe(value):
    if isinstance(value, dict):
        return len(value) <= 20 and all(re.fullmatch(r"[a-z_]+", key) and safe(item) for key, item in value.items())
    if isinstance(value, list):
        return len(value) <= 20 and all(safe(item) for item in value)
    if isinstance(value, str):
        return re.fullmatch(r"[A-Za-z0-9_ -]{1,110}", value) is not None
    return type(value) in (bool, int) or value is None


def emit(prefix, data):
    assert safe(data)
    print(prefix.decode() + json.dumps(data, sort_keys=True, separators=(",", ":")))


def require_exact(actual, expected):
    assert actual.keys() == expected.keys()
    assert all(type(actual[key]) is type(value) and actual[key] == value for key, value in expected.items())


def main():
    mode = sys.argv[1]
    assert (mode, len(sys.argv)) in {("browser", 3), ("fixture", 4)}
    browser, = reports(Path(sys.argv[2]), BROWSER)
    if mode == "browser":
        emit(BROWSER, browser)
    blocked = browser.get("blocked_external_requests")
    assert type(blocked) is int and 0 <= blocked <= 100
    expected = {
        "status": "PASS", "scope": "isolated_synthetic_private_property_custody", "checks": CHECKS,
        "offer_writes": 3, "action_writes": 3, "unexpected_browser_mutations": 0, "blocked_external_requests": blocked,
    }
    require_exact(browser, expected)
    if mode == "browser":
        return
    api = Path(sys.argv[3])
    failures = reports(api, FAILURE)
    if failures:
        failure, = failures
        assert set(failure) == {"stage", "error_type", "fixture_line"} and failure["stage"] == "verify_private_custody"
        assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", failure["error_type"])
        assert failure["fixture_line"] is None or type(failure["fixture_line"]) is int and 0 < failure["fixture_line"] <= 1000
        emit(FAILURE, failure)
        raise SystemExit(1)
    data, = reports(api, DATABASE)
    require_exact(data, {
        "status": "PASS", "offers": browser["offer_writes"], "actions": browser["offer_writes"] + browser["action_writes"],
        "effective_transfers": 1, "previous_notes_retained_privately": True, "history_data_unchanged": True,
        "old_share_invalidated": True, "unexpected_mutations": 0, "external_transport_attempts": 0,
    })
    emit(DATABASE, data)


if __name__ == "__main__":
    main()
