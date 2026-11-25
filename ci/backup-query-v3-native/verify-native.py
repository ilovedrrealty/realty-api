"""Owned PostgreSQL semantics/guard/volume proof; no production connection."""
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib.metadata
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from unittest.mock import patch

import psycopg2
from psycopg2 import sql

NEW = Path("/new-tools")
OLD = Path("/tools")
STATE = Path("/state")
PHASES = {"compact", "guards", "stress"}
V3_REL = Path("outputs/property-network-20261101/backup-query-memory-20261125")
FROZEN_REL = Path("outputs/website-growth-20261022/backups")
stage = "isolation"


class Refused(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Refused(reason)


def connect(database):
    require(database == "postgres" or re.fullmatch(r"ildr_v3_[a-z_]+_[a-f0-9]{32}", database), "owned_database_name_required")
    return psycopg2.connect(host="127.0.0.1", port=55449, user="postgres", password="",
                           dbname=database, connect_timeout=4,
                           options="-cstatement_timeout=60000 -clock_timeout=4000")


def rows(database, statement, parameters=()):
    with closing(connect(database)) as db, db.cursor() as cursor:
        cursor.execute(statement, parameters)
        return cursor.fetchall()


def identity(phase):
    require(phase in PHASES and NEW.resolve(strict=True) == NEW
            and os.statvfs(NEW).f_flag & os.ST_RDONLY, "readonly_new_tools_required")
    manifest = json.loads((NEW / "source-manifest.json").read_text())
    require(manifest["schema"] == "backup_v3_native_sources_v1", "source_manifest_required")
    for name, digest in manifest["candidate_files"].items():
        path = NEW / name
        require(path.resolve().is_relative_to(NEW) and path.is_file() and not path.is_symlink()
                and sha256(path.read_bytes()).hexdigest() == digest, "candidate_source_hash_required")
    previous = OLD / "verify-synthetic.py"
    require(sha256(previous.read_bytes()).hexdigest() == manifest["accepted_harness_sha256"], "accepted_native_harness_required")
    spec = importlib.util.spec_from_file_location("accepted_backup_v2_native", previous)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    nonce, workspace = old.identity()
    require(importlib.metadata.version("cryptography") == "50.0.2", "accepted_crypto_version_required")
    candidate = workspace / V3_REL
    candidate.mkdir(parents=True, exist_ok=False)
    for name in ("make-daily-full-public-query-v3.py", "test-query-v3.py"):
        source = NEW / "candidate" / name
        with (candidate / name).open("xb") as target:
            target.write(source.read_bytes())
    names = json.loads((NEW / "fixture-table-names.json").read_text())
    require(len(names) == len(set(names)) == 99 and names == sorted(names)
            and all(re.fullmatch(r"[a-z_][a-z0-9_]*", name) for name in names), "exact_99_table_names_required")
    return old, nonce, workspace, names


def create_fixture(nonce, label, names):
    require(label in {"compact", "stress", "row_bound", "byte_bound", "schema_bound", "tableset_bound"}, "finite_fixture_label_required")
    database = "ildr_v3_" + label + "_" + nonce
    marker = f"ildr-backup-v3:{nonce}:{label}"
    with closing(connect("postgres")) as db:
        db.autocommit = True
        with db.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_database WHERE datname=%s", (database,))
            require(cursor.fetchone() == (0,), "fresh_owned_database_required")
            cursor.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
            cursor.execute(sql.SQL("COMMENT ON DATABASE {} IS %s").format(sql.Identifier(database)), (marker,))
    with closing(connect(database)) as db, db.cursor() as cursor:
        cursor.execute("CREATE SCHEMA extensions; CREATE EXTENSION pgcrypto WITH SCHEMA extensions")
        cursor.execute("CREATE SCHEMA storage; CREATE TABLE storage.buckets(id text PRIMARY KEY,public boolean,metadata jsonb)")
        cursor.execute("""CREATE TABLE storage.objects(bucket_id text,name text,metadata jsonb,
            updated_at timestamptz,created_at timestamptz,PRIMARY KEY(bucket_id,name))""")
        cursor.execute("CREATE TYPE public.backup_fixture_kind AS ENUM ('first','área','Ñandú')")
        cursor.execute("CREATE SEQUENCE public.backup_fixture_sequence START 7 INCREMENT 3")
        cursor.execute("CREATE FUNCTION public.backup_fixture_lower(text) RETURNS text LANGUAGE sql IMMUTABLE AS 'SELECT lower($1)'")
        for index, name in enumerate(names):
            identifier = sql.Identifier(name)
            if name == "alembic_version":
                cursor.execute("CREATE TABLE public.alembic_version(version_num varchar(32) PRIMARY KEY)")
                cursor.execute("INSERT INTO public.alembic_version VALUES ('synthetic_backup_v3')")
            elif name == "professional_profiles":
                cursor.execute("""CREATE TABLE public.professional_profiles(id int PRIMARY KEY,
                    published_data jsonb,published_at timestamptz,listing_status text)""")
                cursor.execute("INSERT INTO public.professional_profiles VALUES (1,NULL,NULL,'hidden')")
            elif name == "listing_floor_plans":
                cursor.execute("""CREATE TABLE public.listing_floor_plans(id int PRIMARY KEY,
                    payload bytea NOT NULL,sha256 text NOT NULL,byte_count int NOT NULL,notes text)""")
                binaries = (bytes(range(256)) + b"\x00\xff\\\"' synthetic binary \n",
                    bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                                  "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"))
                for number, value in enumerate(binaries, 1):
                    cursor.execute("INSERT INTO public.listing_floor_plans VALUES (%s,%s,%s,%s,%s)",
                                   (number, psycopg2.Binary(value), sha256(value).hexdigest(), len(value), "Árbol 🏡\n\"cita\"\\ruta"))
            elif name == "trade_contacts":
                cursor.execute("""CREATE TABLE public.trade_contacts(id uuid PRIMARY KEY,identity_key char(64) UNIQUE NOT NULL,
                    name text NOT NULL,category text NOT NULL,province text,locality text,details jsonb NOT NULL,
                    created_at timestamptz NOT NULL,updated_at timestamptz NOT NULL)""")
                cursor.execute("CREATE INDEX ix_trade_contacts_category_province_name ON public.trade_contacts(category,province,name,id)")
            elif name == "trade_contact_sources":
                # The foreign key is installed after all table definitions exist.
                cursor.execute("""CREATE TABLE public.trade_contact_sources(id uuid PRIMARY KEY,contact_id uuid NOT NULL,
                    source_key char(64) NOT NULL,evidence jsonb NOT NULL,created_at timestamptz NOT NULL,
                    updated_at timestamptz NOT NULL,UNIQUE(contact_id,source_key))""")
            elif name == "activity_log":
                cursor.execute("CREATE TABLE public.activity_log(id integer PRIMARY KEY)")
                cursor.execute("INSERT INTO public.activity_log VALUES (1)")
            else:
                cursor.execute(sql.SQL("CREATE TABLE public.{}(id int PRIMARY KEY,title text,extra jsonb,kind public.backup_fixture_kind)").format(identifier))
                if index % 7:
                    cursor.execute(sql.SQL("INSERT INTO public.{} VALUES (1,%s,%s::jsonb,'área')").format(identifier),
                                   ("Villa José 🏡\n\"quoted\"\\text", json.dumps({"nullable": None, "value": index})))
        cursor.execute("ALTER TABLE public.trade_contact_sources ADD FOREIGN KEY(contact_id) REFERENCES public.trade_contacts(id) ON DELETE CASCADE")
        cursor.execute("ALTER TABLE public.users ALTER COLUMN id SET DEFAULT nextval('public.backup_fixture_sequence')")
        cursor.execute("ALTER TABLE public.users ADD CONSTRAINT backup_positive_id CHECK(id>0)")
        cursor.execute("SELECT nextval('public.backup_fixture_sequence')")
        cursor.execute("INSERT INTO storage.buckets VALUES ('synthetic-public',true,'{}'),('synthetic-private',false,'{}')")
        db.commit()
    seed_trade(database, 3, 4)
    seed_storage(database, 17)
    return database, marker


def drop_fixture(database, marker):
    with closing(connect("postgres")) as db:
        db.autocommit = True
        with db.cursor() as cursor:
            cursor.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=%s", (database,))
            require(cursor.fetchone() == (marker,), "owned_database_drop_marker_required")
            cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
            cursor.execute("SELECT count(*) FROM pg_database WHERE datname=%s", (database,))
            require(cursor.fetchone() == (0,), "owned_database_absence_required")


def seed_trade(database, contacts, sources, targets=None):
    details = {"name": "Synthetic cooperative", "category": "Cooperativas", "subtype": "Servicios múltiples",
        "province": "Santo Domingo", "locality": None, "phone": None, "email": None, "website": None,
        "source_authority": "Synthetic registry", "source_url": "https://example.com/synthetic-register",
        "verified_on": "2025-11-01", "verification_status": "official_registry",
        "source_details": {"original_registry_row": "Ñandú 🏡\n\"cita\"\\ruta\tJosé", "padding": ""}}
    with closing(connect(database)) as db, db.cursor() as cursor:
        cursor.execute("DELETE FROM public.trade_contact_sources; DELETE FROM public.trade_contacts")
        cursor.execute("""INSERT INTO public.trade_contacts SELECT md5('contact_'||g)::uuid,
            encode(sha256(convert_to('identity_'||g,'UTF8')),'hex'),'Synthetic cooperative '||g,
            'Cooperativas','Santo Domingo',NULL,%s::jsonb||jsonb_build_object('name','Synthetic cooperative '||g),
            '2001-02-03T00:00:00Z','2001-02-03T00:00:00Z' FROM generate_series(1,%s)g""",
            (json.dumps(details, ensure_ascii=False), contacts))
        cursor.execute("""INSERT INTO public.trade_contact_sources SELECT md5('source_'||g)::uuid,
            md5('contact_'||(((g-1)%% %s)+1))::uuid,encode(sha256(convert_to('source_'||g,'UTF8')),'hex'),
            %s::jsonb||jsonb_build_object('name','Synthetic source '||g),
            '2001-02-03T00:00:00Z','2001-02-03T00:00:00Z' FROM generate_series(1,%s)g""",
            (contacts, json.dumps(details, ensure_ascii=False), sources))
        if targets:
            for name, field, count, target in (("trade_contacts", "details", contacts, targets[0]),
                                               ("trade_contact_sources", "evidence", sources, targets[1])):
                cursor.execute(sql.SQL("SELECT sum(octet_length(to_jsonb(t)::text)) FROM public.{} t").format(sql.Identifier(name)))
                missing = target - int(cursor.fetchone()[0])
                require(missing >= 0, "fixture_target_at_least_base_bytes_required")
                padding, remainder = divmod(missing, count)
                require(padding + bool(remainder) <= 1000, "fixture_padding_within_source_note_contract_required")
                cursor.execute(sql.SQL("""WITH numbered AS (SELECT id,row_number() OVER(ORDER BY id) AS n FROM public.{})
                    UPDATE public.{} t SET {}=jsonb_set(t.{},'{{source_details,padding}}',
                    to_jsonb((SELECT left(string_agg(md5(t.id::text||':'||k),'' ORDER BY k),
                        %s+CASE WHEN numbered.n<=%s THEN 1 ELSE 0 END) FROM generate_series(1,32)k)))
                    FROM numbered WHERE numbered.id=t.id""").format(sql.Identifier(name), sql.Identifier(name),
                        sql.Identifier(field), sql.Identifier(field)), (padding, remainder))
                cursor.execute(sql.SQL("SELECT count(*),sum(octet_length(to_jsonb(t)::text)) FROM public.{} t").format(sql.Identifier(name)))
                require(cursor.fetchone() == (count, target), "exact_fixture_byte_total_required")
        db.commit()


def seed_storage(database, count):
    metadata = {"mimetype": "image/webp", "cacheControl": "3600", "fixture": "Ñandú 🏡\n\"cita\"\\ruta", "padding": "m" * 128}
    with closing(connect(database)) as db, db.cursor() as cursor:
        cursor.execute("DELETE FROM storage.objects")
        cursor.execute("""INSERT INTO storage.objects SELECT CASE WHEN g%%2=0 THEN 'synthetic-public' ELSE 'synthetic-private' END,
            'synthetic/ñandú/'||g||'.webp',%s::jsonb||jsonb_build_object('size',1024+g),
            '2001-02-03T00:00:00Z','2001-02-03T00:00:00Z' FROM generate_series(1,%s)g""",
            (json.dumps(metadata, ensure_ascii=False), count))
        db.commit()


def catalog(workspace, database):
    return rows(database, (workspace / FROZEN_REL / "daily-schema-catalog-read-only.sql").read_text())[0][0]


def prepare(old, workspace, database, names, index, catalog_override=None):
    stamp = (datetime(2026, 11, 25, tzinfo=timezone.utc) + timedelta(seconds=index)).strftime("%Y%m%dT%H%M%SZ")
    frozen = workspace / FROZEN_REL
    job = frozen / "daily" / stamp
    job.mkdir(parents=True, exist_ok=False)
    captured = catalog_override or catalog(workspace, database)
    require(captured["table_names"] == names, "expected_catalog_names_required")
    (job / "schema-catalog.json").write_text(json.dumps(captured), encoding="utf8")
    (job / "catalog-read-only.sql").write_bytes((frozen / "daily-schema-catalog-read-only.sql").read_bytes())
    plan = {"stamp_utc": stamp, "status": "AWAITING_SCHEMA_CATALOG", "project_ref": old.PROJECT,
        "repositories": {label: {"repo": "/synthetic/" + label, "ref": "refs/heads/synthetic",
            "commit": str(number) * 40} for number, label in enumerate(("frontend", "backend"), 1)}}
    (job / "backup-plan.json").write_text(json.dumps(plan), encoding="utf8")
    old.native(workspace / V3_REL / "make-daily-full-public-query-v3.py", "--catalog", job / "schema-catalog.json",
               "--output", job / "snapshot-read-only.sql")
    return job, stamp


def parsed(value):
    return json.loads(value) if isinstance(value, str) else value


def state(workspace, database, names):
    result = {name: rows(database, sql.SQL("SELECT count(*),encode(sha256(convert_to(coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb)::text,'UTF8')),'hex') FROM public.{} t").format(sql.Identifier(name)))[0] for name in names}
    result["catalog"] = catalog(workspace, database)
    result["storage"] = rows(database, "SELECT count(*),encode(sha256(convert_to(jsonb_agg(to_jsonb(t) ORDER BY bucket_id,name)::text,'UTF8')),'hex') FROM storage.objects t")[0]
    return result


def downstream(old, workspace, database, names, job, stamp):
    frozen = workspace / FROZEN_REL
    old.native(frozen / "make-daily-compressed-query.py", "--input", job / "snapshot-read-only.sql", "--output", job / "transport-read-only.sql")
    begin = time.monotonic()
    transport = rows(database, (job / "transport-read-only.sql").read_text())[0][0]
    sql_ms = int((time.monotonic() - begin) * 1000)
    require(set(transport) == {"transport", "plaintext_bytes", "plaintext_sha256", "payload_base64"}
            and transport["transport"] == "PGP_COMPRESSED_V1", "original_transport_interface_required")
    (job / "transport-result.json").write_text(json.dumps(transport), encoding="utf8")
    received = json.loads(old.native(frozen / "receive-daily-compressed-snapshot.py", "--stamp", stamp))
    raw = (job / "recovery-snapshot.json").read_bytes()
    require(received["status"] == "PASS" and not received["private_key_accessed"] and not received["owner_passphrase_used"]
            and len(raw) == transport["plaintext_bytes"] and sha256(raw).hexdigest() == transport["plaintext_sha256"], "whole_receiver_bytes_sha_required")
    snapshot = json.loads(raw)
    old.validate_snapshot(database, names, snapshot)
    (frozen / "storage-inventory-baseline.json").write_text(json.dumps({"storage_inventory_state": snapshot["storage_inventory_state"]}), encoding="utf8")
    spec = importlib.util.spec_from_file_location("native_v3_unchanged_finalizer", frozen / "finalize-daily-full-public-backup.py")
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
    with patch.object(sys, "argv", [str(frozen / "finalize-daily-full-public-backup.py"), "--stamp", stamp]), \
            patch.object(finalizer.subprocess, "run", side_effect=sealer_boundary), \
            redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        finalizer.main()
    validation = json.loads((job / "snapshot-validation.json").read_text())
    require(validation["status"] == "PASS" and validation["all_public_tables_included"] and validation["all_profiles_private"]
            and validation["storage_metadata_matches_verified_baseline"] and len(calls) == 1, "full_frozen_finalizer_validation_required")
    require(all(not (job / name).exists() for name in ("schema-catalog.json", "recovery-snapshot.json", "transport-result.json")), "all_three_raw_files_removed_required")
    return {"transport_sql_ms": sql_ms, "plaintext_bytes": len(raw), "compressed_bytes": received["compressed_bytes"],
            "receiver_and_validation_ms": int((time.monotonic() - begin) * 1000), "sealer_boundary_calls": len(calls)}


def plan_nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from plan_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from plan_nodes(child)


def client_memory():
    root = Path("/sys/fs/cgroup")
    current = (root / "memory.current").read_text().strip()
    peak = (root / "memory.peak").read_text().strip()
    require(re.fullmatch(r"[0-9]{1,12}", current) and re.fullmatch(r"[0-9]{1,12}", peak), "actual_client_cgroup_metrics_required")
    events = {}
    for line in (root / "memory.events").read_text().splitlines():
        match = re.fullmatch(r"(low|high|max|oom|oom_kill|oom_group_kill|sock_throttled) ([0-9]{1,12})", line)
        require(match is not None and match[1] not in events, "strict_documented_client_event_keys_required")
        events[match[1]] = int(match[2])
    require({"low", "high", "max", "oom", "oom_kill", "oom_group_kill"} <= set(events),
            "all_six_client_event_keys_required")
    require(events["oom"] == events["oom_kill"] == events["oom_group_kill"] == 0
            and 0 < int(current) <= int(peak) <= 1073741824, "bounded_zero_oom_client_required")
    return int(current), int(peak)


def guards(old, nonce, workspace, names):
    global stage
    proven = []
    for index, label in enumerate(("row_bound", "byte_bound", "schema_bound", "tableset_bound"), 10):
        stage = label
        database, marker = create_fixture(nonce, label, names)
        try:
            if label == "row_bound":
                with closing(connect(database)) as db, db.cursor() as cursor:
                    cursor.execute("TRUNCATE public.activity_log; INSERT INTO public.activity_log SELECT generate_series(1,1000001)")
                    db.commit()
            elif label == "byte_bound":
                with closing(connect(database)) as db, db.cursor() as cursor:
                    cursor.execute("ALTER TABLE public.activity_log ADD COLUMN padding text")
                    cursor.execute("TRUNCATE public.activity_log; INSERT INTO public.activity_log SELECT g,repeat('x',700) FROM generate_series(1,100000)g")
                    db.commit()
            job, stamp = prepare(old, workspace, database, names, index)
            if label == "schema_bound":
                with closing(connect(database)) as db, db.cursor() as cursor:
                    cursor.execute("ALTER TABLE public.users ADD COLUMN synthetic_schema_drift text")
                    db.commit()
            elif label == "tableset_bound":
                with closing(connect(database)) as db, db.cursor() as cursor:
                    cursor.execute("CREATE TABLE public.synthetic_extra_table(id int)")
                    db.commit()
                actual = catalog(workspace, database)
                actual["table_names"] = names
                # A deliberate mismatched table inventory isolates the table-set
                # guard while its independently current definition hash matches.
                job, stamp = prepare(old, workspace, database, names, index + 100, actual)
            query = (job / "snapshot-read-only.sql").read_text()
            measured = [rows(database, sql.SQL("SELECT count(*),coalesce(sum(octet_length(to_jsonb(t)::text)),0) FROM public.{} t").format(sql.Identifier(name)))[0] for name in names]
            actual_catalog = catalog(workspace, database)
            expected_catalog = json.loads((job / "schema-catalog.json").read_text())
            conditions = (all(count <= 1000000 for count, size in measured),
                          sum(int(size) for count, size in measured) <= 67108864,
                          actual_catalog["table_names"] == names,
                          actual_catalog["definition_sha256"] == expected_catalog["definition_sha256"])
            expected_conditions = {"row_bound": (False, True, True, True), "byte_bound": (True, False, True, True),
                "schema_bound": (True, True, True, False), "tableset_bound": (True, True, False, True)}
            require(conditions == expected_conditions[label], "independent_guard_condition_matrix_required")
            result = parsed(rows(database, query)[0][0])
            require(result["status"] == "EXCEEDS_SAFE_BOUNDS_OR_SCHEMA_CHANGED" and result["private_records_returned"] is False
                    and not ({"public_table_payloads", "public_table_payload_sha256", "full_public_schema_catalog"} & set(result)), "guard_returns_no_payload_required")
            explain = rows(database, "EXPLAIN (ANALYZE,FORMAT JSON,TIMING OFF,SUMMARY OFF) " + query)[0][0]
            nodes = list(plan_nodes(explain))
            payload_nodes = [node for node in nodes if re.fullmatch(r"CTE ildr_payload_[0-9]{3}", node.get("Subplan Name", ""))]
            lazy = [node for node in nodes if node.get("Subplan Name") in {"CTE ildr_payload_maps", "CTE ildr_metadata"}]
            require(len(payload_nodes) == 99 and len(lazy) == 2 and all(node.get("Actual Loops") == 0 for node in payload_nodes + lazy), "all_payload_branches_never_executed_required")
            proven.append(label)
        finally:
            drop_fixture(database, marker)
    return {"guard_cases": proven, "lazy_payload_ctes_per_case": 99, "lazy_map_metadata_ctes_per_case": 2}


def run(phase):
    global stage
    old, nonce, workspace, names = identity(phase)
    client_start, initial_peak = client_memory()
    require(rows("postgres", "SELECT current_setting('shared_buffers'),current_setting('work_mem'),current_setting('max_parallel_workers_per_gather'),current_setting('jit')")
            == [("128MB", "4MB", "0", "off")], "declared_postgres_settings_required")
    started = rows("postgres", "SELECT pg_postmaster_start_time()::text")[0][0]
    if phase == "guards":
        details = guards(old, nonce, workspace, names)
    else:
        if phase == "compact":
            stage = "compact_pure_contracts"
            old.native(workspace / V3_REL / "test-query-v3.py")
        stage = phase + "_fixture"
        database, marker = create_fixture(nonce, phase, names)
        try:
            if phase == "stress":
                seed_trade(database, 5633, 5640, (9317650, 8743189))
                seed_storage(database, 34315)
            before = state(workspace, database, names)
            stage = phase + "_prepare"
            if phase == "compact":
                old_job, _ = old.prepare(workspace, database, names, 99, 2)
                require(rows(database, "SELECT nextval('public.backup_fixture_sequence')")[0][0] == 10, "mutable_sequence_control_required")
                before = state(workspace, database, names)
            job, stamp = prepare(old, workspace, database, names, 1)
            if phase == "compact":
                stage = "compact_exact_semantics"
                v2 = parsed(rows(database, (old_job / "snapshot-read-only.sql").read_text())[0][0])
                v3 = parsed(rows(database, (job / "snapshot-read-only.sql").read_text())[0][0])
                old.validate_snapshot(database, names, v2)
                old.validate_snapshot(database, names, v3)
                times = (v2.pop("captured_at_utc"), v3.pop("captured_at_utc"))
                require(all(isinstance(value, str) and datetime.fromisoformat(value).tzinfo is not None for value in times)
                        and v2 == v3, "parsed_equivalence_except_capture_time_required")
                require(state(workspace, database, names) == before, "v2_v3_queries_leave_all_state_unchanged_required")
            stage = phase + "_whole_transport_receiver_finalizer"
            details = downstream(old, workspace, database, names, job, stamp)
            require(state(workspace, database, names) == before, "all_99_rows_catalog_sequences_storage_unchanged_required")
            if phase == "stress":
                require(rows(database, "SELECT count(*),sum(octet_length(to_jsonb(t)::text)) FROM public.trade_contacts t") == [(5633, 9317650)]
                        and rows(database, "SELECT count(*),sum(octet_length(to_jsonb(t)::text)) FROM public.trade_contact_sources t") == [(5640, 8743189)]
                        and rows(database, "SELECT count(*) FROM storage.objects") == [(34315,)], "exact_stress_counts_and_bytes_required")
                details.update(contacts=5633, sources=5640, contact_row_bytes=9317650, source_row_bytes=8743189, storage_objects=34315,
                    storage_metadata_bytes=int(rows(database, "SELECT sum(octet_length(metadata::text)) FROM storage.objects")[0][0]))
            else:
                details.update(parsed_equivalence_excluding_capture_time_only=True, compact_bytea_roundtrips=4, pure_contracts=4)
        finally:
            drop_fixture(database, marker)
    require(rows("postgres", "SELECT pg_postmaster_start_time()::text")[0][0] == started, "unchanged_postmaster_start_required")
    source_manifest = json.loads((OLD / "source-manifest.json").read_text())
    for name, digest in source_manifest["files"].items():
        require(sha256((workspace / name).read_bytes()).hexdigest() == digest, "unchanged_frozen_mirror_required")
    for name in ("make-daily-full-public-query-v3.py", "test-query-v3.py"):
        require((workspace / V3_REL / name).read_bytes() == (NEW / "candidate" / name).read_bytes(), "unchanged_candidate_mirror_required")
    client_end, client_peak = client_memory()
    require(client_peak >= initial_peak, "monotonic_client_peak_required")
    return {"status": "PASS", "phase": phase, "postgres_version_num": 170006, "public_tables": 99,
        "server_start_unchanged": True, "production_data_accessed": False, "external_network_available": False,
        "owner_encryption_or_cloud_executed": False, "frozen_sources_unchanged": True,
        "client_start_bytes": client_start, "client_end_bytes": client_end, "client_whole_phase_peak_bytes": client_peak,
        "client_oom_events": 0, "client_oom_kill_events": 0, **details}


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) == 2 else "invalid"
    try:
        result = run(phase)
    except Exception as error:
        kind = type(error).__name__
        result = {"status": "FAIL", "phase": phase if phase in PHASES else "invalid", "stage": stage,
            "error_type": kind if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", kind) else "UnhandledError",
            "reason": str(error) if isinstance(error, Refused) and re.fullmatch(r"[a-z_0-9]{1,90}", str(error)) else "unexpected_failure"}
    print("BACKUP_V3_NATIVE_RESULT " + json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
    raise SystemExit(0 if result["status"] == "PASS" else 1)
