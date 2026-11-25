"""Separate JSON-envelope experiment; immutable V5/staged snapshot and recovery interfaces."""
from contextlib import closing, redirect_stderr, redirect_stdout
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import time
from unittest.mock import patch

NEW, V5, STAGED = Path("/new-tools"), Path("/v5-tools"), Path("/staged-tools")
JSON_REL = Path("outputs/property-network-20261101/backup-transport-json-20261125")
STAGED_REL = Path("outputs/property-network-20261101/backup-transport-stages-20261125")
V5_HELPER_SHA = "b2565a1b76eec78df9252f3ab08d4b30bc2ee308c40982a29002459761b1d500"
STAGED_FILES = {"make-daily-compressed-query-staged-v1.py": "47691ecbf43a9ffecf5e9670573d2521281df386fa789d04dfba32de350bb7f5", "test-transport-staged-v1.py": "a7fea81912e5cd3d520363364dffd228e9d0d098d29b4e8c5e6bc7d164e0158b"}
JSON_FILES = {"make-daily-compressed-query-json-v1.py": "a964425d0f5ba6d76ab89ffb7ba78acbbae47df30b9bf6818c528915bf558c1c", "test-transport-json-v1.py": "1fdf9d584b2f6272e1f63ff2729ad7e3366c93fe523a3ad2e013155c0c34249c"}
PHASES = {"compact", "guards", "stress"}
current_phase = "invalid"
native = None


class Refused(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Refused(reason)


def load_v5():
    for root in (NEW, V5, STAGED):
        require(root.resolve(strict=True) == root and os.statvfs(root).f_flag & os.ST_RDONLY,
            "readonly_staged_and_v5_sources_required")
    for name, digest in {**{"candidate/" + name: value for name, value in STAGED_FILES.items()},
        "verify-native.py": "23cdd8359c2c6ebdb712cc1ca89a3b51a2dadd853436e24ebdee429d28cb2cf5"}.items():
        path = STAGED / name
        require(path.is_file() and not path.is_symlink() and sha256(path.read_bytes()).hexdigest() == digest,
            "exact_immutable_staged_sources_required")
    source = V5 / "verify-native.py"
    require(source.is_file() and not source.is_symlink() and sha256(source.read_bytes()).hexdigest() == V5_HELPER_SHA,
        "exact_immutable_v5_fixture_required")
    spec = importlib.util.spec_from_file_location("immutable_v5_staged_transport", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.NEW = V5
    return module


def prepare_wrapper(old, workspace, job, filename="transport-read-only.sql"):
    require(filename in {"transport-read-only.sql", "guard-transport-read-only.sql"}, "finite_transport_filename_required")
    output = job / filename
    old.native(workspace / JSON_REL / "make-daily-compressed-query-json-v1.py", "--input", job / "snapshot-read-only.sql", "--output", output)
    return output


def fence_counts_from_plan(plan, analyze=False):
    nodes = list(native.plan_nodes(plan))
    found = {name: [node for node in nodes if node.get("Subplan Name") == "CTE transport_" + name]
        for name in ("snapshot", "digest", "cipher")}
    require(all(len(values) == 1 for values in found.values()), "three_actual_materialized_transport_fences_required")
    if analyze:
        require(all(values[0].get("Actual Loops") == 1 for values in found.values()), "three_executed_guard_fences_required")
    return {name: len(values) for name, values in found.items()}


def json_decoder_controls(database):
    cases = ({"label": "José 🏡\n\"quoted\"\\route", "n": 0, "flag": False}, [None, True, {"nested": [0, 1.25]}], None)
    proven, observed_jsonb_oid = 0, None
    with closing(native.connect(database)) as db, db.cursor() as cursor:
        for payload in cases:
            encoded, label = json.dumps(payload, ensure_ascii=False), "Árbol \\ literal\ttext"
            cursor.execute("SELECT json_build_object('fixture',%s::jsonb,'nullable',NULL::text,'label',%s::text),"
                "jsonb_build_object('fixture',%s::jsonb,'nullable',NULL::text,'label',%s::text)", (encoded, label, encoded, label))
            require([item.type_code for item in cursor.description] == [114, 3802], "actual_json_and_jsonb_type_oids_required")
            row = cursor.fetchone()
            expected = {"fixture": payload, "nullable": None, "label": label}
            require(type(row[0]) is dict and type(row[1]) is dict and row[0] == row[1] == expected,
                "both_native_json_types_automatically_decode_exact_mapping_required")
            observed_jsonb_oid = cursor.description[1].type_code
            proven += 1
    return {"jsonb_reference_type_oid": observed_jsonb_oid, "json_decoder_control_cases": proven}


def downstream(old, workspace, database, names, job, stamp, inventory_sha256):
    native.stage = "staged_transport_prepare"
    frozen = workspace / native.FROZEN_REL
    transport_path = prepare_wrapper(old, workspace, job)
    plan = native.rows(database, "EXPLAIN (FORMAT JSON) " + transport_path.read_text())[0][0]
    fences = fence_counts_from_plan(plan)
    controls = json_decoder_controls(database) if current_phase == "compact" else {}
    begin = time.monotonic()
    native.stage = "json_transport_select"
    with closing(native.connect(database)) as db, db.cursor() as cursor:
        cursor.execute(transport_path.read_text())
        require(len(cursor.description) == 1 and cursor.description[0].type_code == 114, "actual_full_transport_json_type_oid_required")
        actual_type_oid = cursor.description[0].type_code
        rows = cursor.fetchall()
        require(len(rows) == 1 and len(rows[0]) == 1 and type(rows[0][0]) is dict, "actual_json_transport_automatic_mapping_decoder_required")
        transport = rows[0][0]
    sql_ms = int((time.monotonic() - begin) * 1000)
    require(set(transport) == {"transport", "plaintext_bytes", "plaintext_sha256", "payload_base64"}
        and transport["transport"] == "PGP_COMPRESSED_V1", "original_transport_interface_required")
    (job / "transport-result.json").write_text(json.dumps(transport), encoding="utf8")
    native.stage = "receiver"
    received = json.loads(old.native(frozen / "receive-daily-compressed-snapshot.py", "--stamp", stamp))
    raw = (job / "recovery-snapshot.json").read_bytes()
    require(received["status"] == "PASS" and not received["private_key_accessed"] and not received["owner_passphrase_used"]
        and len(raw) == transport["plaintext_bytes"] and sha256(raw).hexdigest() == transport["plaintext_sha256"],
        "whole_receiver_bytes_sha_required")
    snapshot = json.loads(raw)
    require(snapshot["storage_inventory_state"]["inventory_sha256"] == inventory_sha256,
        "received_storage_inventory_matches_independent_reference_required")
    native.stage = "native_snapshot_validation"
    old.validate_snapshot(database, names, snapshot)
    (frozen / "storage-inventory-baseline.json").write_text(json.dumps({"storage_inventory_state": snapshot["storage_inventory_state"]}), encoding="utf8")
    spec = importlib.util.spec_from_file_location("staged_transport_unchanged_finalizer", frozen / "finalize-daily-full-public-backup.py")
    finalizer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(finalizer)
    calls = []
    def sealer_boundary(command, check):
        expected = [sys.executable, str(frozen / "save-growth-checkpoint.py"), "--phase", "daily", "--daily-stamp", stamp]
        for number, label in enumerate(("frontend", "backend"), 1):
            expected += ["--" + label + "-repo", "/synthetic/" + label, "--" + label + "-ref", "refs/heads/synthetic", "--" + label + "-commit", str(number) * 40]
        for name in ("backup-plan.json", "snapshot-read-only.sql", "transport-read-only.sql", "transport-verification.json", "recovery-snapshot.json", "schema-catalog.json", "catalog-read-only.sql", "snapshot-read-only.preparation.json", "snapshot-validation.json"):
            expected += ["--include", name]
        require(command == expected and check is True, "exact_sealer_dispatch_required")
        calls.append(True)
    native.stage = "finalizer_validation"
    with patch.object(sys, "argv", [str(frozen / "finalize-daily-full-public-backup.py"), "--stamp", stamp]), patch.object(finalizer.subprocess, "run", side_effect=sealer_boundary), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        finalizer.main()
    validation = json.loads((job / "snapshot-validation.json").read_text())
    require(validation["status"] == "PASS" and validation["all_public_tables_included"] and validation["all_profiles_private"]
        and validation["storage_metadata_matches_verified_baseline"] and len(calls) == 1, "full_frozen_finalizer_validation_required")
    native.stage = "raw_cleanup"
    require(all(not (job / name).exists() for name in ("schema-catalog.json", "recovery-snapshot.json", "transport-result.json")),
        "all_three_raw_files_removed_required")
    return {"transport_sql_ms": sql_ms, "plaintext_bytes": len(raw), "compressed_bytes": received["compressed_bytes"],
        "receiver_and_validation_ms": int((time.monotonic() - begin) * 1000), "sealer_boundary_calls": len(calls),
        "staged_transport_wrapper": True, "transport_fence_counts": fences, "json_transport_envelope": True,
        "actual_transport_type_oid": actual_type_oid, "decoded_transport_exact_fields": True,
        "recovered_snapshot_bytes_sha_exact": True, **controls}


def guards(old, nonce, workspace, names):
    proven, wrapped = [], []
    for index, label in enumerate(("row_bound", "byte_bound", "schema_bound", "tableset_bound"), 10):
        native.stage = label
        database, marker = native.create_fixture(nonce, label, names)
        try:
            if label == "row_bound":
                with closing(native.connect(database)) as db, db.cursor() as cursor:
                    cursor.execute("TRUNCATE public.activity_log; INSERT INTO public.activity_log SELECT generate_series(1,1000001)")
                    db.commit()
            elif label == "byte_bound":
                with closing(native.connect(database)) as db, db.cursor() as cursor:
                    cursor.execute("ALTER TABLE public.activity_log ADD COLUMN padding text")
                    cursor.execute("TRUNCATE public.activity_log; INSERT INTO public.activity_log SELECT g,repeat('x',700) FROM generate_series(1,100000)g")
                    db.commit()
            job, stamp = native.prepare(old, workspace, database, names, index)
            if label == "schema_bound":
                with closing(native.connect(database)) as db, db.cursor() as cursor:
                    cursor.execute("ALTER TABLE public.users ADD COLUMN synthetic_schema_drift text")
                    db.commit()
            elif label == "tableset_bound":
                with closing(native.connect(database)) as db, db.cursor() as cursor:
                    cursor.execute("CREATE TABLE public.synthetic_extra_table(id int)")
                    db.commit()
                actual = native.catalog(workspace, database)
                actual["table_names"] = names
                # A deliberate mismatched table inventory isolates the table-set
                # guard while its independently current definition hash matches.
                job, stamp = native.prepare(old, workspace, database, names, index + 100, actual)
            query = (job / "snapshot-read-only.sql").read_text()
            measured = [native.rows(database, native.sql.SQL("SELECT count(*),coalesce(sum(octet_length(to_jsonb(t)::text)),0) FROM public.{} t").format(native.sql.Identifier(name)))[0] for name in names]
            actual_catalog = native.catalog(workspace, database)
            expected_catalog = json.loads((job / "schema-catalog.json").read_text())
            conditions = (all(count <= 1000000 for count, size in measured),
                          sum(int(size) for count, size in measured) <= 67108864,
                          actual_catalog["table_names"] == names,
                          actual_catalog["definition_sha256"] == expected_catalog["definition_sha256"])
            expected_conditions = {"row_bound": (False, True, True, True), "byte_bound": (True, False, True, True),
                "schema_bound": (True, True, True, False), "tableset_bound": (True, True, False, True)}
            require(conditions == expected_conditions[label], "independent_guard_condition_matrix_required")
            result = native.parsed(native.rows(database, query)[0][0])
            require(result["status"] == "EXCEEDS_SAFE_BOUNDS_OR_SCHEMA_CHANGED" and result["private_records_returned"] is False
                    and not ({"public_table_payloads", "public_table_payload_sha256", "full_public_schema_catalog"} & set(result)), "guard_returns_no_payload_required")
            explain = native.rows(database, "EXPLAIN (ANALYZE,FORMAT JSON,TIMING OFF,SUMMARY OFF) " + query)[0][0]
            nodes = list(native.plan_nodes(explain))
            payload_nodes = [node for node in nodes if re.fullmatch(r"CTE ildr_payload_[0-9]{3}", node.get("Subplan Name", ""))]
            lazy = [node for node in nodes if node.get("Subplan Name") in {"CTE ildr_payload_maps", "CTE ildr_metadata"}]
            require(len(payload_nodes) == 99 and len(lazy) == 2 and all(node.get("Actual Loops") == 0 for node in payload_nodes + lazy), "all_payload_branches_never_executed_required")
            # Execute the staged wrapper around the same rejected snapshot.
            # The outer crypto stages may run, but no private payload/map branch may.
            transport = prepare_wrapper(old, workspace, job, "guard-transport-read-only.sql")
            wrapped_plan = native.rows(database, "EXPLAIN (ANALYZE,FORMAT JSON,TIMING OFF,SUMMARY OFF) " + transport.read_text())[0][0]
            wrapped_nodes = list(native.plan_nodes(wrapped_plan))
            payload = [node for node in wrapped_nodes if re.fullmatch(r"CTE ildr_payload_[0-9]{3}", node.get("Subplan Name", ""))]
            maps = [node for node in wrapped_nodes if node.get("Subplan Name") in {"CTE ildr_payload_maps", "CTE ildr_metadata"}]
            require(len(payload) == 99 and len(maps) == 2 and all(node.get("Actual Loops") == 0 for node in payload + maps),
                "wrapped_guard_all_payload_branches_never_executed_required")
            require(fence_counts_from_plan(wrapped_plan, analyze=True) == {"snapshot": 1, "digest": 1, "cipher": 1},
                "wrapped_guard_three_actual_fences_required")
            wrapped.append(label)
            proven.append(label)
        finally:
            native.drop_fixture(database, marker)
    return {"guard_cases": proven, "lazy_payload_ctes_per_case": 99, "lazy_map_metadata_ctes_per_case": 2,
        "wrapped_guard_cases": wrapped, "wrapped_lazy_payload_ctes_per_case": 99,
        "wrapped_lazy_map_metadata_ctes_per_case": 2, "wrapped_transport_fences_per_case": 3}


def run(phase):
    global native, current_phase
    require(phase in PHASES, "finite_native_phase_required")
    current_phase = phase
    native = load_v5()
    original_identity = native.identity
    bound_workspace = None
    def identity(bound_phase):
        nonlocal bound_workspace
        old, nonce, workspace, names = original_identity(bound_phase)
        bound_workspace = workspace
        native.stage = "staged_wrapper_identity"
        destination = workspace / STAGED_REL
        destination.mkdir(parents=True, exist_ok=False)
        for name, digest in STAGED_FILES.items():
            source = STAGED / "candidate" / name
            require(source.is_file() and not source.is_symlink() and sha256(source.read_bytes()).hexdigest() == digest,
                "exact_staged_wrapper_sources_required")
            with (destination / name).open("xb") as stream:
                stream.write(source.read_bytes())
        json_destination = workspace / JSON_REL
        json_destination.mkdir(parents=True, exist_ok=False)
        for name, digest in JSON_FILES.items():
            source = NEW / "candidate" / name
            require(source.is_file() and not source.is_symlink() and sha256(source.read_bytes()).hexdigest() == digest,
                "exact_json_envelope_sources_required")
            with (json_destination / name).open("xb") as stream:
                stream.write(source.read_bytes())
        if bound_phase == "compact":
            old.native(destination / "test-transport-staged-v1.py")
            old.native(json_destination / "test-transport-json-v1.py")
        return old, nonce, workspace, names
    native.identity, native.downstream, native.guards = identity, downstream, guards
    report = native.run(phase)
    # The imported immutable V5 main is never invoked; its strict run() function
    # retains compact/old-new inventory/pressure/old99/source/cgroup/DB guards.
    # Only these explicitly reviewed preparation/downstream/extra-guard hooks vary.
    require(bound_workspace is not None, "staged_workspace_bound_required")
    for name, digest in STAGED_FILES.items():
        require(sha256((bound_workspace / STAGED_REL / name).read_bytes()).hexdigest() == digest,
            "unchanged_staged_wrapper_mirror_required")
    for name, digest in JSON_FILES.items():
        require(sha256((bound_workspace / JSON_REL / name).read_bytes()).hexdigest() == digest,
            "unchanged_json_envelope_mirror_required")
    if phase == "compact":
        report["staged_wrapper_contracts"] = 4
        report["json_wrapper_contracts"] = 4
    return report


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) == 2 else "invalid"
    try:
        result = run(phase)
    except Exception as error:
        if native is not None:
            native.capture_sqlstate(error)
        kind = type(error).__name__
        result = {"status": "FAIL", "phase": phase if phase in PHASES else "invalid",
            "stage": native.stage if native is not None else "isolation",
            "sqlstate": native.first_sqlstate if native is not None else "unavailable",
            "error_type": kind if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", kind) else "UnhandledError",
            "reason": str(error) if isinstance(error, (Refused, native.Refused if native is not None else Refused))
                and re.fullmatch(r"[a-z_0-9]{1,90}", str(error)) else "unexpected_failure"}
    print("BACKUP_TRANSPORT_JSON_NATIVE_RESULT " + json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
    raise SystemExit(0 if result["status"] == "PASS" else 1)
