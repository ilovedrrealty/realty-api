"""Exact typed results, bounded failures and real owned mutation/asset proof only."""
import json
from pathlib import Path
import re
import sys


def require(value, reason):
    if not value:
        raise AssertionError(reason)


def unique_pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def record(line, prefix, limit):
    require(len(line) <= limit, "bounded_record_required")
    return json.loads(line[len(prefix):], object_pairs_hook=unique_pairs)


def exact(actual, expected):
    require(type(actual) is type(expected), "exact_type_required")
    if type(expected) is dict:
        require(set(actual) == set(expected), "exact_keys_required")
        for key in expected:
            exact(actual[key], expected[key])
    elif type(expected) is list:
        require(len(actual) == len(expected), "exact_list_length_required")
        for left, right in zip(actual, expected):
            exact(left, right)
    else:
        require(actual == expected, "exact_value_required")


def check(mode, filename, native):
    require(mode in {"browser", "api"} and re.fullmatch(r"[0-9]{1,3}", native) and int(native) <= 255, "exact_arguments_required")
    path = Path(filename)
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 2 * 1024 * 1024, "bounded_log_required")
    rows, geometry, presence, frames, overflow = [], [], [], [], []
    failed = False
    labels = {language + "_" + screen for language in ("en", "es") for screen in (
        "property", "assets", "rent", "public_resource", "calculator", "dashboard_resource", "area",
        "buyer_hub", "owner_hub", "realtor_hub", "research")}
    prefix = b"MANUAL_SHARE_BROWSER_RESULT " if mode == "browser" else b"MANUAL_SHARE_FIXTURE_RESULT "
    for line in path.read_bytes().splitlines():
        if line.startswith(prefix):
            rows.append(record(line, prefix, 4096))
        elif line.startswith(b"MANUAL_SHARE_LOOPBACK_STATE "):
            require(mode == "browser" and len(presence) < 10, "bounded_presence_required")
            value = record(line, b"MANUAL_SHARE_LOOPBACK_STATE ", 256)
            require(type(value) is dict and set(value) == {"before", "modeled", "after", "visible"}
                and all(type(item) is bool for item in value.values()), "exact_presence_shape_required")
            require(value["modeled"] == (not value["before"]) and value["after"] and value["visible"], "actual_loopback_presence_required")
            presence.append(value)
        elif line.startswith(b"MANUAL_SHARE_BROWSER_GEOMETRY "):
            require(mode == "browser" and len(geometry) < 44, "bounded_geometry_required")
            value = record(line, b"MANUAL_SHARE_BROWSER_GEOMETRY ", 512)
            require(type(value) is dict and set(value) == {"label", "width", "document"}, "exact_geometry_shape_required")
            require(type(value["label"]) is str and value["label"] in labels and type(value["width"]) is int
                and value["width"] in {320, 390} and type(value["document"]) is int
                and 0 < value["document"] <= value["width"] + 1, "real_mobile_geometry_required")
            geometry.append(value)
        elif line.startswith(b"MANUAL_SHARE_BROWSER_FAILURE "):
            require(mode == "browser" and len(line) <= 256, "bounded_browser_failure_required")
            parts = line.decode("ascii").split()
            require(len(parts) == 3 and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,95}", parts[1])
                and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", parts[2]), "finite_browser_failure_required")
            print(" ".join(parts), flush=True)
            failed = True
        elif line.startswith(b"MANUAL_SHARE_BROWSER_FRAMES "):
            require(mode == "browser" and not frames, "single_failure_frames_required")
            value = record(line, b"MANUAL_SHARE_BROWSER_FRAMES ", 512)
            require(type(value) is dict and set(value) == {"frames"} and type(value["frames"]) is list
                and 1 <= len(value["frames"]) <= 8, "bounded_own_frames_required")
            for item in value["frames"]:
                require(type(item) is dict and set(item) == {"line", "column"} and all(type(part) is int
                    and 1 <= part <= 99999 for part in item.values()), "numeric_own_frames_required")
            require(len({(item["line"], item["column"]) for item in value["frames"]}) == len(value["frames"]), "unique_own_frames_required")
            frames.append(value)
            print("MANUAL_SHARE_BROWSER_FRAMES " + json.dumps(value, sort_keys=True), flush=True)
            failed = True
        elif line.startswith(b"MANUAL_SHARE_OVERFLOW_CENSUS "):
            require(mode == "browser" and not overflow, "single_browser_overflow_census_required")
            value = record(line, b"MANUAL_SHARE_OVERFLOW_CENSUS ", 12288)
            require(type(value) is dict and set(value) == {"label", "width", "inner", "client", "scroll", "scanned", "matches", "rows"}, "exact_overflow_census_keys_required")
            require(type(value["label"]) is str and value["label"] in labels | {"webkit_calculator"}
                and type(value["width"]) is int and value["width"] in {320, 390}, "known_geometry_census_required")
            require(all(type(value[key]) is int and 0 <= value[key] <= 100000 for key in ("inner", "client", "scroll", "scanned", "matches"))
                and 0 <= value["matches"] <= value["scanned"] <= 2500 and 0 < value["inner"] <= 100000, "bounded_overflow_dimensions_required")
            require(type(value["rows"]) is list and len(value["rows"]) == min(value["matches"], 24), "bounded_overflow_rows_required")
            tags = {"MAIN", "DIV", "SECTION", "ARTICLE", "DETAILS", "SUMMARY", "FORM", "INPUT", "TEXTAREA", "SELECT", "BUTTON", "A", "PRE", "IMG", "IFRAME", "H1", "H2", "H3", "P", "SPAN", "LABEL", "SVG", "OTHER"}
            classes = {'min-w-0', 'max-w-full', 'w-full', 'max-w-310', 'grid', 'grid-cols-1', 'grid-cols-2', 'sm:grid-cols-4', 'lg:grid-cols-[1fr_360px]',
                'gap-3', 'gap-10', 'p-4', 'p-5.5', 'px-6', 'flex', 'inline-flex', 'flex-wrap', 'shrink-0', 'whitespace-pre-wrap', 'break-words',
                '[overflow-wrap:anywhere]', 'overflow-hidden', 'overflow-x-auto', 'rounded-xl', 'rounded-2xl', 'text-[18px]', 'font-sans', 'listing-prose'}
            for item in value["rows"]:
                require(type(item) is dict and set(item) == {"tag", "scope", "classes", "width", "left", "right", "client", "scroll"}
                    and type(item["tag"]) is str and item["tag"] in tags and type(item["scope"]) is str and item["scope"] in {"share", "ancestor", "page"}, "exact_overflow_row_shape_required")
                require(type(item["classes"]) is list and len(item["classes"]) <= 8 and all(type(part) is str and part in classes for part in item["classes"])
                    and len(set(item["classes"])) == len(item["classes"]), "source_known_overflow_classes_required")
                require(all(type(item[key]) is int and -100000 <= item[key] <= 100000 for key in ("width", "left", "right", "client", "scroll"))
                    and all(item[key] >= 0 for key in ("width", "client", "scroll")), "integer_only_overflow_row_required")
            overflow.append(value)
            print("MANUAL_SHARE_OVERFLOW_CENSUS " + json.dumps(value, sort_keys=True), flush=True)
            failed = True
        elif line.startswith(b"MANUAL_SHARE_FIXTURE_FAILURE "):
            require(mode == "api", "api_failure_mode_required")
            value = record(line, b"MANUAL_SHARE_FIXTURE_FAILURE ", 512)
            require(type(value) is dict and set(value) == {"stage", "error_type", "reason"}
                and value["stage"] in {"source", "previous99", "upgrade106", "seed_public_facts", "browser", "native_ledger"}
                and all(type(item) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,95}", item) for item in value.values()), "finite_fixture_failure_required")
            print("MANUAL_SHARE_FIXTURE_FAILURE " + json.dumps(value, sort_keys=True), flush=True)
            failed = True
        elif line.startswith(b"MANUAL_SHARE_"):
            raise AssertionError("unknown_or_wrong_mode_manual_sharing_marker")
    require(native == "0" and not failed and len(rows) == 1, "native_or_reported_failure")
    value = rows[0]
    if mode == "browser":
        expected = {"status": "PASS", "locales": ["en", "es"], "journeys": 20, "geometry": 44, "contexts": 10,
            "actual_logins": 8, "api_boundary_checks": 16, "clipboard_writes": 40, "clipboard_mode": "owned_chromium_actual_api",
            "composer_hrefs": 120, "instrumented_native_calls": 6, "native_share_mode": "instrumented_no_os_delivery",
            "png_downloads": 4, "caption_downloads": 4, "guarded_image_failures": 2, "fresh_read_aborts": 2,
            "stale_close_checks": 2, "source_change_checks": 2, "withdrawal_checks": 2, "listing_fetch_options": 24,
            "guarded_photo_fetches": 4, "control_transitions": 8, "page_errors": 0, "unexpected_mutations": 0,
            "external_writes": 0, "tracking_requests": 0}
        require(type(value) is dict and set(value) == {*expected, "blocked_external_requests", "webkit"}, "exact_browser_result_keys_required")
        exact({key: value[key] for key in expected}, expected)
        require(type(value["blocked_external_requests"]) is int and 0 <= value["blocked_external_requests"] <= 999, "bounded_blocked_requests_required")
        require(type(value["webkit"]) is dict and type(value["webkit"].get("available")) is bool, "explicit_webkit_availability_required")
        exact(value["webkit"], {"available": value["webkit"]["available"], "journeys": 1 if value["webkit"]["available"] else 0,
            "geometry": 2 if value["webkit"]["available"] else 0, "png_downloads": 1 if value["webkit"]["available"] else 0, "native_delivery": False})
        require(len(presence) == 10 and len(geometry) == 44 and {(item["label"], item["width"]) for item in geometry}
            == {(label, width) for label in labels for width in (320, 390)}, "complete_unique_mobile_and_presence_proof_required")
        for item in presence:
            print("MANUAL_SHARE_LOOPBACK_STATE " + json.dumps(item, sort_keys=True))
        for item in geometry:
            print("MANUAL_SHARE_BROWSER_GEOMETRY " + json.dumps(item, sort_keys=True))
    else:
        expected = {"status": "PASS", "schema_tables": 106, "legacy_tables": 99, "all106_rows_sequences_catalog_unchanged": True,
            "inbox_writes": 0, "private_rls_acl": True, "protected_owner_contacts_unchanged": 82, "private_profiles_unchanged": 46,
            "contacts_unchanged": 85, "sources_unchanged": 86, "users_unchanged": 4, "listings_unchanged": 4,
            "accepted_jobs_unchanged": 9, "cms_version_unchanged": 7, "floorplans_unchanged": 2,
            "actual_access_boundaries": 8, "actual_logins": 8, "anonymous_fresh_reads": 22, "fresh_read_not_found": 2,
            "actual_view_posts": 4, "view_effects_verified_and_restored": True, "control_transitions": 8,
            "unexpected_mutations": 0, "external_provider_attempts": 0, "private_files_removed": 1}
        exact(value, expected)
    print(prefix.decode() + json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    require(len(sys.argv) == 4, "exact_checker_arguments_required")
    check(*sys.argv[1:])
