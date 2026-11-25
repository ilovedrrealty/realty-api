"""One owned V5 uncompressed length/hash observation; never backup acceptance."""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time

CASE = "full_snapshot_uncompressed"
CANDIDATE = Path("/candidate-tools")
HELPER_SHA = "b2565a1b76eec78df9252f3ab08d4b30bc2ee308c40982a29002459761b1d500"
STAGE = "isolation"
native = None


class Refused(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Refused(reason)


def load_native():
    require(CANDIDATE.resolve(strict=True) == CANDIDATE and os.statvfs(CANDIDATE).f_flag & os.ST_RDONLY,
        "readonly_exact_v5_tools_required")
    path = CANDIDATE / "verify-native.py"
    require(path.is_file() and not path.is_symlink() and sha256(path.read_bytes()).hexdigest() == HELPER_SHA,
        "exact_54fa_native_helper_required")
    spec = importlib.util.spec_from_file_location("frozen_v5_fixture_only", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.NEW = CANDIDATE
    return module


def component_query(job):
    query = (job / "snapshot-read-only.sql").read_text()
    final = ("\nSELECT CASE WHEN octet_length(recovery_snapshot)<=67108864 THEN recovery_snapshot "
        "ELSE jsonb_build_object('status','EXCEEDS_SAFE_BOUNDS_OR_SCHEMA_CHANGED',"
        "'private_records_returned',false)::text END AS recovery_snapshot FROM snapshot_candidate;\n")
    require(query.count(final) == 1 and query.endswith(final), "exact_v5_final_projection_required")
    # Reuse the reviewed 0b3 whole-query wrapper, binding immutable V5 instead.
    # Keep every input/schema/table/output guard and select only text length/SHA.
    return ("SELECT octet_length(recovery_snapshot),encode(sha256(convert_to(recovery_snapshot,'UTF8')),'hex') "
        "FROM (\n" + query.rstrip().removesuffix(';') + "\n) original_v5_snapshot;\n")


def run(case):
    global native, STAGE
    require(case == CASE, "only_full_snapshot_uncompressed_required")
    native = load_native()
    old, nonce, workspace, names = native.identity("stress")
    client_start, initial_peak = native.client_memory()
    require(native.rows("postgres", "SELECT current_setting('shared_buffers'),current_setting('work_mem'),current_setting('max_parallel_workers_per_gather'),current_setting('jit')")
        == [("128MB", "4MB", "0", "off")], "declared_postgres_settings_required")
    started = native.rows("postgres", "SELECT pg_postmaster_start_time()::text")[0][0]
    STAGE = "stress_fixture"
    database, marker = native.create_fixture(nonce, "stress", names)
    job = None
    try:
        native.seed_trade(database, 5633, 5640, (9317650, 8743189))
        native.seed_storage(database, 34315)
        require(native.rows(database, "SELECT count(*),sum(octet_length(to_jsonb(t)::text)) FROM public.trade_contacts t") == [(5633, 9317650)]
            and native.rows(database, "SELECT count(*),sum(octet_length(to_jsonb(t)::text)) FROM public.trade_contact_sources t") == [(5640, 8743189)]
            and native.rows(database, "SELECT count(*) FROM storage.objects") == [(34315,)], "exact_pre_component_counts_and_bytes_required")
        before = native.state(workspace, database, names)
        STAGE = "component_prepare"
        job, stamp = native.prepare(old, workspace, database, names, 1)
        query = component_query(job)
        STAGE = "full_snapshot_uncompressed_select"
        begin = time.monotonic()
        selected = native.rows(database, query)
        elapsed = int((time.monotonic() - begin) * 1000)
        require(len(selected) == 1 and len(selected[0]) == 2 and type(selected[0][0]) is int
            and 9317650 + 8743189 <= selected[0][0] <= 67108864 and isinstance(selected[0][1], str)
            and re.fullmatch(r"[a-f0-9]{64}", selected[0][1]), "finite_component_length_sha_not_fallback_required")
        STAGE = "post_state"
        require(native.state(workspace, database, names) == before, "all99_rows_catalog_sequences_storage_unchanged_required")
        require(native.rows("postgres", "SELECT pg_postmaster_start_time()::text")[0][0] == started,
            "unchanged_postmaster_start_required")
        manifest = json.loads((native.OLD / "source-manifest.json").read_text())
        for name, digest in manifest["files"].items():
            require(sha256((workspace / name).read_bytes()).hexdigest() == digest, "unchanged_frozen_mirror_required")
        for name in ("make-daily-full-public-query-v3.py", "make-daily-full-public-query-v4.py", "test-query-v4.py", "make-daily-full-public-query-v5.py", "test-query-v5.py"):
            require((workspace / native.V5_REL / name).read_bytes() == (CANDIDATE / "candidate" / name).read_bytes(),
                "unchanged_candidate_mirror_required")
        client_end, client_peak = native.client_memory()
        require(client_peak >= initial_peak, "monotonic_client_peak_required")
        return {"status": "OBSERVED", "case": case, "backup_accepted": False, "postgres_version_num": 170006,
            "public_tables": 99, "contacts": 5633, "sources": 5640, "contact_row_bytes": 9317650,
            "source_row_bytes": 8743189, "storage_objects": 34315, "selected_bytes": selected[0][0],
            "selected_sha256": selected[0][1], "derived_query_sha256": sha256(query.encode("utf8")).hexdigest(),
            "generated_query_sha256": sha256((job / "snapshot-read-only.sql").read_bytes()).hexdigest(),
            "component_sql_ms": elapsed, "server_start_unchanged": True,
            "all99_rows_catalog_sequences_storage_unchanged": True, "frozen_sources_unchanged": True,
            "production_data_accessed": False, "external_network_available": False,
            "transport_receiver_finalizer_executed": False, "owner_encryption_or_cloud_executed": False,
            "client_start_bytes": client_start, "client_end_bytes": client_end,
            "client_whole_phase_peak_bytes": client_peak, "client_oom_events": 0, "client_oom_kill_events": 0}
    finally:
        if job is not None:
            (job / "schema-catalog.json").unlink(missing_ok=True)
            require(not any((job / name).exists() for name in ("schema-catalog.json", "recovery-snapshot.json", "transport-result.json")),
                "owned_raw_metadata_removed_required")
        native.drop_fixture(database, marker)


if __name__ == "__main__":
    case = sys.argv[1] if len(sys.argv) == 2 else "invalid"
    try:
        result = run(case)
    except Exception as error:
        if native is not None:
            native.capture_sqlstate(error)
        kind = type(error).__name__
        result = {"status": "FAIL", "case": case if case == CASE else "invalid", "backup_accepted": False,
            "stage": STAGE, "sqlstate": native.first_sqlstate if native is not None else "unavailable",
            "error_type": kind if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", kind) else "UnhandledError",
            "reason": str(error) if isinstance(error, (Refused, native.Refused if native is not None else Refused))
                and re.fullmatch(r"[a-z_0-9]{1,90}", str(error)) else "unexpected_failure"}
    print("BACKUP_V5_UNCOMPRESSED_DIAGNOSTIC_RESULT " + json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
    raise SystemExit(0 if result["status"] == "OBSERVED" else 1)
