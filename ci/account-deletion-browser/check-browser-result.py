"""Emit bounded source-free diagnostics and accept only the complete real journey."""

import json
from pathlib import Path
import re
import sys


CHECKS = [
    "normal_member_and_admin_settings_entrypoints",
    "typed_email_and_explicit_request_confirmation",
    "durable_request_status_after_reload",
    "admin_review_and_hold_reason_visible_to_member",
    "stale_member_cancel_requires_current_state",
    "current_member_cancel_and_replay_are_durable",
    "stale_admin_review_cannot_override_cancellation",
    "non_admin_direct_queue_and_review_denied",
    "all_four_roles_keep_active_accounts",
    "english_spanish_mobile_labels_fit",
    "admin_own_settings_request_and_cancel",
]
STAGES = {"fixture", "browser_launch", "realtor_entry", "realtor_denial", "mobile_geometry", "browser_teardown"}
GEOMETRIES = {"es_realtor320"}
for language in ("en", "es"):
    STAGES.update(f"{language}_{name}" for name in (
        "member_entry", "request", "reload", "admin_entry", "review", "hold",
        "stale_cancel", "cancel", "stale_review", "denials", "admin_own",
    ))
    GEOMETRIES.update(f"{language}_{name}" for name in (
        "member390", "member320", "queue320", "admin320",
    ))


def main():
    path = Path(sys.argv[1])
    assert path.stat().st_size <= 131072
    lines = path.read_text(encoding="ascii").splitlines()
    stages, geometries, results, failures = [], {}, [], []
    for line in lines:
        if line.startswith("F09_BROWSER_STAGE "):
            name = line.removeprefix("F09_BROWSER_STAGE ")
            assert name in STAGES and name not in stages
            stages.append(name)
            print(line)
        elif line.startswith("F09_BROWSER_GEOMETRY "):
            assert re.fullmatch(r"F09_BROWSER_GEOMETRY (?:en|es)_(?:member390|member320|queue320|admin320|realtor320)(?: -?[0-9]{1,4}){6}", line)
            fields = line.removeprefix("F09_BROWSER_GEOMETRY ").split()
            name = fields[0]
            assert name in GEOMETRIES and name not in geometries
            viewport, document, left, right, scroll, client = map(int, fields[1:])
            expected_width = 390 if name.endswith("390") else 320
            geometries[name] = (
                viewport == expected_width and document <= viewport + 1 and
                left >= -1 and right <= viewport + 1 and scroll <= client + 1
            )
            print(line)
        elif line.startswith("F09_BROWSER_FAILURE "):
            assert re.fullmatch(r"F09_BROWSER_FAILURE [a-z_]{1,40} [A-Z_]{1,60}(?: status=[1-5][0-9]{2})?", line)
            failures.append(line)
            print(line)
        elif line.startswith("F09_BROWSER_RESULT "):
            results.append(json.loads(line.removeprefix("F09_BROWSER_RESULT ")))
    assert len(failures) <= 1 and len(geometries) <= 9
    assert not failures and set(stages) == STAGES and set(geometries) == GEOMETRIES
    assert all(geometries.values())
    assert results == [{"status": "PASS", "checks": CHECKS}]
    print("F09_BROWSER_RESULT " + json.dumps(results[0], separators=(",", ":")))


if __name__ == "__main__":
    main()
