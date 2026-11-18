"""Accept only the complete bounded synthetic journey, without exposing source data."""

import json
from pathlib import Path
import re
import sys

CHECKS = [
    "actual_public_buyer_seller_renter_entries_en_es",
    "explicit_consent_and_validation_prevent_submission",
    "accepted_receipt_and_next_step_thank_you",
    "property_context_contact_locale_and_idempotent_receipt",
    "actual_signup_verification_locale_and_new_account",
    "admin_summary_queue_detail_accept_customer_email",
    "safe_retry_requires_confirmation_and_cancel_has_no_mutation",
    "uncertain_outcome_and_non_admin_retry_denied",
    "buyer_owner_feeds_are_scoped_and_history_separate",
    "admin_and_owner_manual_share_fields_and_permission",
    "user_initiated_email_whatsapp_composers_canceled_without_send",
    "english_spanish_whole_page_390_and_320_geometry",
    "paused_flags_keep_fallback_and_do_not_replay",
    "zero_external_api_writes_tracking_and_page_errors",
]
STAGES = ["browser_launch"] + [f"{locale}_{name}" for locale in ("en", "es")
                              for name in ("public_forms", "signup", "admin", "feeds")] + ["paused_fallback", "browser_teardown"]
GEOMETRIES = {f"{locale}_{name}_{width}" for locale in ("en", "es")
              for name in ("thankyou", "property", "delivery", "communications", "owner", "buyer") for width in (390, 320)}
SIGNUP_PHASES = {"navigate", "name", "phone", "email", "passwords", "terms", "readiness", "register",
                 "verify_route", "verification_code", "verify_digits", "verify_submit", "new_identity"}
VALIDATION_KEYS = {"name_required", "name_min", "phone_required", "phone_invalid", "email_required", "email_invalid",
                   "password_required", "password_min", "password_lowercase", "password_uppercase", "password_number",
                   "password_special", "confirm_required", "confirm_match", "terms_required"}


def signup_diagnostic(value):
    base = {"phase", "state_available", "failure_kind", "register_requests", "register_statuses", "page_error_count", "page_error_kinds"}
    state = {"page_kind", "name_count", "phone_count", "email_count", "password_count", "terms_count", "name_matches",
             "email_matches", "phone_matches", "passwords_match", "terms_checked", "validation"}
    assert type(value["state_available"]) is bool and set(value) == (base | state if value["state_available"] else base)
    assert value["phase"] in SIGNUP_PHASES and value["failure_kind"] in {"timeout", "ambiguous", "check", "other"}
    assert type(value["register_requests"]) is int and 0 <= value["register_requests"] <= 10
    assert isinstance(value["register_statuses"], list) and len(value["register_statuses"]) <= 4
    assert all(type(item) is int and 100 <= item <= 599 for item in value["register_statuses"])
    assert type(value["page_error_count"]) is int and 0 <= value["page_error_count"] <= 1000
    assert isinstance(value["page_error_kinds"], list) and len(value["page_error_kinds"]) <= 8
    assert all(item in {"TypeError", "ReferenceError", "SecurityError", "Error", "Other"}
               or isinstance(item, str) and re.fullmatch(r"React[0-9]{1,4}", item) for item in value["page_error_kinds"])
    if value["state_available"]:
        assert value["page_kind"] in {"signup", "verify", "other"}
        assert all(type(value[key]) is int and 0 <= value[key] <= 10 for key in
                   ("name_count", "phone_count", "email_count", "password_count", "terms_count"))
        assert all(type(value[key]) is bool for key in
                   ("name_matches", "email_matches", "phone_matches", "passwords_match", "terms_checked"))
        assert isinstance(value["validation"], list) and len(value["validation"]) <= 15 and set(value["validation"]) <= VALIDATION_KEYS
    print("COMMUNICATIONS_BROWSER_SIGNUP_DIAGNOSTIC " + json.dumps(value, sort_keys=True, separators=(",", ":")))


def main():
    path = Path(sys.argv[1])
    assert path.stat().st_size <= 131072
    stages, geometries, results, failures = [], set(), [], []
    for line in path.read_text(encoding="ascii").splitlines():
        if line.startswith("COMMUNICATIONS_BROWSER_STAGE "):
            value = line.removeprefix("COMMUNICATIONS_BROWSER_STAGE ")
            assert value in STAGES and value not in stages
            stages.append(value)
            print(line)
        elif line.startswith("COMMUNICATIONS_BROWSER_GEOMETRY "):
            assert re.fullmatch(r"COMMUNICATIONS_BROWSER_GEOMETRY [a-z_]+(?:390|320) [0-9]{3} [0-9]{3,4}", line)
            name, viewport, document = line.removeprefix("COMMUNICATIONS_BROWSER_GEOMETRY ").split()
            assert name in GEOMETRIES and name not in geometries
            assert int(viewport) == int(name[-3:]) and int(document) <= int(viewport) + 1
            geometries.add(name)
            print(line)
        elif line.startswith("COMMUNICATIONS_BROWSER_FAILURE "):
            assert re.fullmatch(r"COMMUNICATIONS_BROWSER_FAILURE [a-z_]{1,40} [A-Z_]{1,65}", line)
            failures.append(line)
            print(line)
        elif line.startswith("COMMUNICATIONS_BROWSER_SIGNUP_DIAGNOSTIC "):
            signup_diagnostic(json.loads(line.removeprefix("COMMUNICATIONS_BROWSER_SIGNUP_DIAGNOSTIC ")))
        elif line.startswith("COMMUNICATIONS_BROWSER_RESULT "):
            results.append(json.loads(line.removeprefix("COMMUNICATIONS_BROWSER_RESULT ")))
    assert len(failures) <= 1 and len(geometries) <= 24
    assert not failures and stages == STAGES and geometries == GEOMETRIES
    assert results == [{"status": "PASS", "checks": CHECKS, "geometry_checks": 24,
                        "page_errors": 0, "external_writes": 0, "tracking_requests": 0}]
    print("COMMUNICATIONS_BROWSER_RESULT " + json.dumps(results[0], separators=(",", ":")))


if __name__ == "__main__":
    main()
