"""Three owned component observations, never a backup-acceptance claim."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import ast
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
PHASES = {"payload_map_only", "storage_fingerprint_only", "full_snapshot_uncompressed"}
V4_REL = Path("outputs/property-network-20261101/backup-query-memory-20261125")
FROZEN_REL = Path("outputs/website-growth-20261022/backups")
stage = "isolation"
first_sqlstate = "unavailable"


class Refused(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Refused(reason)


def connect(database):
    require(database == "postgres" or re.fullmatch(r"ildr_v4_stage_[a-z_]+_[a-f0-9]{32}", database), "owned_database_name_required")
    return psycopg2.connect(host="127.0.0.1", port=55449, user="postgres", password="",
                           dbname=database, connect_timeout=4,
                           options="-cstatement_timeout=60000 -clock_timeout=4000")


def capture_sqlstate(error):
    global first_sqlstate
    value = getattr(error, "pgcode", None)
    if first_sqlstate == "unavailable" and isinstance(error, psycopg2.Error) and isinstance(value, str) and re.fullmatch(r"[0-9A-Z]{5}", value):
        first_sqlstate = value


def rows(database, statement, parameters=()):
    try:
        with closing(connect(database)) as db, db.cursor() as cursor:
            cursor.execute(statement, parameters)
            return cursor.fetchall()
    except psycopg2.Error as error:
        capture_sqlstate(error)
        raise


def identity(phase):
    require(phase in PHASES and NEW.resolve(strict=True) == NEW
            and os.statvfs(NEW).f_flag & os.ST_RDONLY, "readonly_new_tools_required")
    manifest = json.loads((NEW / "source-manifest.json").read_text())
    require(manifest["schema"] == "backup_v4_stage_diagnostic_sources_v1", "source_manifest_required")
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
    candidate = workspace / V4_REL
    candidate.mkdir(parents=True, exist_ok=False)
    for name in ("make-daily-full-public-query-v3.py", "make-daily-full-public-query-v4.py", "test-query-v4.py"):
        source = NEW / "candidate" / name
        with (candidate / name).open("xb") as target:
            target.write(source.read_bytes())
    names = json.loads((NEW / "fixture-table-names.json").read_text())
    require(len(names) == len(set(names)) == 99 and names == sorted(names)
            and all(re.fullmatch(r"[a-z_][a-z0-9_]*", name) for name in names), "exact_99_table_names_required")
    return old, nonce, workspace, names


def create_fixture(nonce, label, names):
    require(label in PHASES, "finite_fixture_label_required")
    database = "ildr_v4_stage_" + {"payload_map_only":"map", "storage_fingerprint_only":"storage", "full_snapshot_uncompressed":"snapshot"}[label] + "_" + nonce
    marker = f"ildr-backup-v4-stage:{nonce}:{label}"
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
    old.native(workspace / V4_REL / "make-daily-full-public-query-v4.py", "--catalog", job / "schema-catalog.json",
               "--output", job / "snapshot-read-only.sql")
    return job, stamp


def parsed(value):
    return json.loads(value) if isinstance(value, str) else value


def state(workspace, database, names):
    result = {name: rows(database, sql.SQL("SELECT count(*),encode(sha256(convert_to(coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb)::text,'UTF8')),'hex') FROM public.{} t").format(sql.Identifier(name)))[0] for name in names}
    result["catalog"] = catalog(workspace, database)
    result["storage"] = rows(database, "SELECT count(*),encode(sha256(convert_to(jsonb_agg(to_jsonb(t) ORDER BY bucket_id,name)::text,'UTF8')),'hex') FROM storage.objects t")[0]
    return result


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


def component_query(workspace, job, case):
    # The generator itself stays unchanged and source-SHA-pinned. Match its
    # exact final projection once; do not parse/rewrite row values or guards.
    query = (job / "snapshot-read-only.sql").read_text()
    final = ("\nSELECT CASE WHEN octet_length(recovery_snapshot)<=67108864 THEN recovery_snapshot "
        "ELSE jsonb_build_object('status','EXCEEDS_SAFE_BOUNDS_OR_SCHEMA_CHANGED',"
        "'private_records_returned',false)::text END AS recovery_snapshot FROM snapshot_candidate;\n")
    require(query.count(final)==1 and query.endswith(final), "exact_v4_final_projection_required")
    if case == "payload_map_only":
        return query[:-len(final)]+("\nSELECT octet_length(payload_map_text),"
            "encode(sha256(convert_to(payload_map_text,'UTF8')),'hex') FROM ildr_payload_maps;\n")
    if case == "storage_fingerprint_only":
        source = workspace / V4_REL / "make-daily-full-public-query-v4.py"
        tree = ast.parse(source.read_text())
        nodes = [node.value for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id=="storage" for target in node.targets)]
        require(len(nodes)==1 and isinstance(nodes[0], ast.Constant) and isinstance(nodes[0].value, str),
            "exact_source_storage_expression_required")
        return ("WITH storage_diagnostic AS MATERIALIZED (SELECT "+nodes[0].value+
            "::text AS value) SELECT octet_length(value),encode(sha256(convert_to(value,'UTF8')),'hex') FROM storage_diagnostic;\n")
    require(case == "full_snapshot_uncompressed", "finite_component_required")
    # Keep the entire original guarded SELECT. Do not cast its full text back
    # to JSONB or add an expanded aggregate state just to observe length/hash.
    return ("SELECT octet_length(recovery_snapshot),encode(sha256(convert_to(recovery_snapshot,'UTF8')),'hex') "
        "FROM (\n"+query.rstrip().removesuffix(';')+"\n) original_v4_snapshot;\n")


def run(case):
    global stage
    old, nonce, workspace, names = identity(case)
    client_start, initial_peak = client_memory()
    require(rows("postgres", "SELECT current_setting('shared_buffers'),current_setting('work_mem'),current_setting('max_parallel_workers_per_gather'),current_setting('jit')")
        == [("128MB", "4MB", "0", "off")], "declared_postgres_settings_required")
    started = rows("postgres", "SELECT pg_postmaster_start_time()::text")[0][0]
    stage = "stress_fixture"
    database, marker = create_fixture(nonce, case, names)
    job = None
    try:
        seed_trade(database, 5633, 5640, (9317650, 8743189))
        seed_storage(database, 34315)
        require(rows(database, "SELECT count(*),sum(octet_length(to_jsonb(t)::text)) FROM public.trade_contacts t") == [(5633, 9317650)]
            and rows(database, "SELECT count(*),sum(octet_length(to_jsonb(t)::text)) FROM public.trade_contact_sources t") == [(5640, 8743189)]
            and rows(database, "SELECT count(*) FROM storage.objects") == [(34315,)], "exact_pre_component_counts_and_bytes_required")
        before = state(workspace, database, names)
        stage = "component_prepare"
        job, stamp = prepare(old, workspace, database, names, 1)
        query = component_query(workspace, job, case)
        stage = case+"_select"
        begin = time.monotonic()
        selected = rows(database, query)
        elapsed = int((time.monotonic()-begin)*1000)
        require(len(selected)==1 and len(selected[0])==2 and type(selected[0][0]) is int
            and 0<selected[0][0]<=67108864 and isinstance(selected[0][1], str)
            and re.fullmatch(r"[a-f0-9]{64}", selected[0][1]), "finite_component_length_sha_required")
        if case != "storage_fingerprint_only":
            require(selected[0][0]>=9317650+8743189, "synthetic_payload_size_not_small_fallback_required")
        stage = "post_state"
        require(state(workspace, database, names)==before, "all99_rows_catalog_sequences_storage_unchanged_required")
        require(rows("postgres", "SELECT pg_postmaster_start_time()::text")[0][0]==started,
            "unchanged_postmaster_start_required")
        manifest = json.loads((OLD / "source-manifest.json").read_text())
        for name,digest in manifest["files"].items():
            require(sha256((workspace/name).read_bytes()).hexdigest()==digest, "unchanged_frozen_mirror_required")
        for name in ("make-daily-full-public-query-v3.py", "make-daily-full-public-query-v4.py", "test-query-v4.py"):
            require((workspace/V4_REL/name).read_bytes()==(NEW/"candidate"/name).read_bytes(), "unchanged_candidate_mirror_required")
        client_end,client_peak=client_memory()
        require(client_peak>=initial_peak, "monotonic_client_peak_required")
        return {"status":"OBSERVED", "case":case, "backup_accepted":False, "postgres_version_num":170006,
            "public_tables":99, "contacts":5633, "sources":5640, "contact_row_bytes":9317650,
            "source_row_bytes":8743189, "storage_objects":34315, "selected_bytes":selected[0][0],
            "selected_sha256":selected[0][1], "derived_query_sha256":sha256(query.encode("utf-8")).hexdigest(),
            "generated_query_sha256":sha256((job/"snapshot-read-only.sql").read_bytes()).hexdigest(), "component_sql_ms":elapsed, "server_start_unchanged":True,
            "all99_rows_catalog_sequences_storage_unchanged":True, "frozen_sources_unchanged":True,
            "production_data_accessed":False, "external_network_available":False,
            "transport_receiver_finalizer_executed":False, "owner_encryption_or_cloud_executed":False,
            "client_start_bytes":client_start, "client_end_bytes":client_end,
            "client_whole_phase_peak_bytes":client_peak, "client_oom_events":0, "client_oom_kill_events":0}
    finally:
        if job is not None:
            # Only this synthetic owned workspace was ever used; no capture or
            # transport private rows were written. Remove its schema metadata.
            (job/"schema-catalog.json").unlink(missing_ok=True)
            require(not any((job/name).exists() for name in ("schema-catalog.json", "recovery-snapshot.json", "transport-result.json")),
                "owned_raw_metadata_removed_required")
        drop_fixture(database,marker)


if __name__ == "__main__":
    case=sys.argv[1] if len(sys.argv)==2 else "invalid"
    try:
        result=run(case)
    except Exception as error:
        capture_sqlstate(error)
        kind=type(error).__name__
        result={"status":"FAIL", "case":case if case in PHASES else "invalid", "backup_accepted":False,
            "stage":stage, "sqlstate":first_sqlstate,
            "error_type":kind if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}",kind) else "UnhandledError",
            "reason":str(error) if isinstance(error,Refused) and re.fullmatch(r"[a-z_0-9]{1,90}",str(error)) else "unexpected_failure"}
    print("BACKUP_V4_STAGE_DIAGNOSTIC_RESULT "+json.dumps(result,sort_keys=True,separators=(",",":")),flush=True)
    raise SystemExit(0 if result["status"]=="OBSERVED" else 1)
