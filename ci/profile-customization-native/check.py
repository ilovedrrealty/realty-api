"""Finite owned-native proof only; no partial run or diagnostic can pass."""
import json
from pathlib import Path
import re
import sys

EXPECTED = json.loads(r'''{"status":"PASS","tests":134,"resilience_source_tests":67,"inbox_tests":15,"hosted_safety_tests":2,"pure_options_tests":4,"owned_recovery_tests":3,"delivery_regression_tests":53,"customer_copy_tests":7,"owned_handshake_cases":1,"legacy_award_tests":5,"legacy_negotiation_tests":5,"profile_route_tests":30,"private_image_tests":4,"profile_input_tests":3,"profile_retention_tests":2,"old_tables":99,"schema_tables":106,"model_tables":105,"untouched_data_tables":103,"projected_data_tables":2,"new_nullable_columns":4,"baseline_visual_sentinel_rows":2,"owner_contacts_unchanged":82,"private_profiles_unchanged":46,"accepted_jobs_unchanged":9,"cms_version_unchanged":7,"floorplans_unchanged":2,"external_provider_attempts":0,"previous99_preserved_before_sentinels":true,"raw103_data_unchanged":true,"old2_column_projection_unchanged":true,"all106_sequences_unchanged":true,"old_rows_new_columns_null":true,"exact_version_transition":true,"exact_profile_ddl_delta":true,"new9_model_ddl_projection_equal":true,"private_rls_acl":true,"old_acl_rls_unchanged":true,"new_profile_guard_private_invoker":true,"empty_downgrade_reupgrade_equal":true,"selected_downgrade_refused":true,"unselected_downgrade_refused":true,"full106_final_rows_sequences_catalog_unchanged":true,"all_owned_schemas_removed":true,"server_start_unchanged":true,"tcp_query_deadline_claimed":false}''')
SUITES = json.loads(r'''[["inbox",15],["safety",2],["options",4],["recovery",3],["delivery",53],["customer_copy",7],["legacy_award",5],["legacy_negotiation",5],["profile",30],["private_images",4],["profile_inputs",3],["retention",2]]''')
STAGES = set(json.loads(r'''["source","previous99","baseline106","seed106","upgrade_visuals","profile_model_parity","empty_visual_rollback","inbox15","safety2","resilience67","owned_handshake","legacy10","profile30","private_images4","profile_inputs3","retention2","preservation"]'''))
TABLES = {"inbox_preferences", "inbox_blocks", "inbox_conversations", "inbox_commands",
    "inbox_entries", "inbox_read_states", "inbox_audit", "social_network_profiles", "network_media"}
PREFIX = b"PROFILE_CUSTOMIZATION_NATIVE_"

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
    value = json.loads(line[len(prefix):], object_pairs_hook=unique_pairs)
    require(type(value) is dict, "object_record_required")
    return value

def main(arguments):
    require(len(arguments) == 5, "exact_arguments_required")
    filename, client_exit, native_exit, running, oom = arguments
    require(all(re.fullmatch(r"[0-9]{1,3}", code) and int(code) <= 255 for code in (client_exit, native_exit)), "finite_process_exit_required")
    require(running in {"true", "false"} and oom in {"true", "false"}, "finite_process_state_required")
    path = Path(filename)
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 2 * 1024 * 1024, "bounded_log_required")
    counts, results, handshakes, failed, census_seen = [], [], [], False, False
    for line in path.read_bytes().splitlines():
        if line.startswith(PREFIX + b"PARITY_CENSUS "):
            require(not census_seen, "single_parity_census_required")
            value = record(line, PREFIX + b"PARITY_CENSUS ", 1536)
            require(set(value) == {"columns", "constraints", "indexes", "samples"}, "exact_census_keys_required")
            require(all(type(value[key]) is int and 0 <= value[key] <= 999 for key in ("columns", "constraints", "indexes")), "finite_parity_counts_required")
            require(sum(value[key] for key in ("columns", "constraints", "indexes")) > 0, "actual_parity_difference_required")
            require(type(value["samples"]) is list and 1 <= len(value["samples"]) <= 3, "bounded_parity_samples_required")
            for sample in value["samples"]:
                require(type(sample) is dict and set(sample) == {"section", "table", "field", "actual_present", "expected_present",
                    "different_positions", "actual_default_kind", "expected_default_kind"}, "exact_parity_sample_required")
                require(type(sample["section"]) is str and sample["section"] in {"columns", "constraints", "indexes"}
                    and type(sample["table"]) is str and sample["table"] in TABLES
                    and type(sample["field"]) is str and re.fullmatch(r"[a-z_][a-z0-9_]{0,100}", sample["field"]), "source_owned_parity_identifiers_required")
                require(type(sample["actual_present"]) is type(sample["expected_present"]) is bool
                    and (sample["actual_present"] or sample["expected_present"]), "finite_parity_presence_required")
                values = sample["different_positions"]
                require(type(values) is list and 1 <= len(values) <= 8 and all(type(n) is int and 0 <= n <= 7 for n in values)
                    and values == sorted(set(values)), "finite_parity_positions_required")
                require(all(type(sample[key]) is str and sample[key] in {"none", "zero_bare", "zero_bigint_quoted", "now", "other", "not_applicable"}
                    for key in ("actual_default_kind", "expected_default_kind")), "finite_default_kind_required")
            print("PROFILE_CUSTOMIZATION_NATIVE_PARITY_CENSUS " + json.dumps(value, sort_keys=True), flush=True)
            census_seen = True
            failed = True
        elif line.startswith(PREFIX + b"COUNTS "):
            value = record(line, PREFIX + b"COUNTS ", 512)
            require(set(value) == {"suite", "tests", "failures", "errors", "skipped", "provider_attempts"}
                and type(value["suite"]) is str and value["suite"] in {label for label, _ in SUITES}, "exact_suite_record_required")
            require(all(type(number) is int and 0 <= number <= 999 for key, number in value.items() if key != "suite"), "finite_suite_counts_required")
            require(len(counts) < len(SUITES), "bounded_suite_records_required")
            counts.append(value)
            print("PROFILE_CUSTOMIZATION_NATIVE_COUNTS " + json.dumps(value, sort_keys=True), flush=True)
        elif line.startswith(PREFIX + b"FAILED_METHOD "):
            require(len(line) <= 320, "bounded_failed_method_required")
            text = line.decode("ascii")
            match = re.fullmatch(r"PROFILE_CUSTOMIZATION_NATIVE_FAILED_METHOD ([a-z_]+) (test_[a-z0-9_]{1,180}|fixture_setup_or_cleanup)", text)
            require(match and match.group(1) in {label for label, _ in SUITES}, "finite_failed_method_required")
            print(text, flush=True)
            failed = True
        elif line.startswith(PREFIX + b"FAILURE "):
            value = record(line, PREFIX + b"FAILURE ", 640)
            require(set(value) == {"stage", "error_type", "reason", "sqlstate"}
                and type(value["stage"]) is str and value["stage"] in STAGES
                and all(type(value[key]) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,100}", value[key]) for key in ("error_type", "reason"))
                and type(value["sqlstate"]) is str and (value["sqlstate"] == "unavailable" or re.fullmatch(r"[0-9A-Z]{5}", value["sqlstate"])),
                "finite_failure_metadata_required")
            print("PROFILE_CUSTOMIZATION_NATIVE_FAILURE " + json.dumps(value, sort_keys=True), flush=True)
            failed = True
        elif line.startswith(b"RESILIENCE_BLACKHOLE_RESULT "):
            require(not handshakes, "single_handshake_required")
            value = record(line, b"RESILIENCE_BLACKHOLE_RESULT ", 512)
            exact = {"status": "PASS", "case": "owned_startup_handshake_timeout", "connect_timeout_seconds": 10,
                "accepted_connections": 1, "startup_seen": True, "server_thread_joined": True,
                "application_queries": 0, "external_provider_attempts": 0}
            require(set(value) == set(exact) | {"elapsed_ms"} and type(value["elapsed_ms"]) is int
                and 8000 <= value["elapsed_ms"] <= 15000, "actual_handshake_window_required")
            require(all(type(value[key]) is type(part) and value[key] == part for key, part in exact.items()), "exact_handshake_proof_required")
            handshakes.append(value)
        elif line.startswith(PREFIX + b"RESULT "):
            require(not results, "single_result_required")
            results.append(record(line, PREFIX + b"RESULT ", 1536))
        elif line.startswith(PREFIX) or line.startswith(b"INBOX_RESILIENCE_NATIVE_"):
            raise AssertionError("unknown_or_wrong_scope_native_marker")
    require(client_exit == native_exit == "0" and running == oom == "false" and not failed, "actual_native_process_success_required")
    require(counts == [{"suite": label, "tests": count, "failures": 0, "errors": 0, "skipped": 0, "provider_attempts": 0}
        for label, count in SUITES], "all_exact134_cases_required")
    require(results == [EXPECTED] and all(type(results[0][key]) is type(value) for key, value in EXPECTED.items()), "exact_result_required")
    require(len(handshakes) == 1, "actual_owned_handshake_required")
    print("RESILIENCE_BLACKHOLE_RESULT " + json.dumps(handshakes[0], sort_keys=True))
    print("PROFILE_CUSTOMIZATION_NATIVE_RESULT " + json.dumps(EXPECTED, sort_keys=True))

if __name__ == "__main__":
    main(sys.argv[1:])
