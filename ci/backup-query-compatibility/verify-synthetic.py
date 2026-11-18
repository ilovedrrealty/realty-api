"""Exercise exact backup query helpers against disposable synthetic PostgreSQL."""

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
import subprocess
import sys
from unittest.mock import patch

import psycopg2
from psycopg2 import sql

TOOLS = Path("/tools")
STATE = Path("/state")
PROJECT = "pznmfxejanwxtsopxxrp"
REQUIRED = (
    "alembic_version", "users", "leads", "listings", "listing_edits",
    "listing_events", "professional_profiles", "professional_profile_revisions",
    "website_settings", "website_settings_revisions", "listing_floor_plans",
)
FROZEN_REL = Path("outputs/website-growth-20261022/backups")
V2_REL = Path("outputs/property-network-20261101/backup-query-compatibility-20261118/make-daily-full-public-query-v2.py")
EXPECTED_ARITY_MESSAGE = "cannot pass more than 100 arguments to a function"
stage = "isolation"


class Refused(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def connect(database):
    return psycopg2.connect(
        host="127.0.0.1", port=55449, user="postgres", password="",
        dbname=database, connect_timeout=4, options="-cstatement_timeout=30000",
    )


def rows(database, statement, parameters=()):
    with closing(connect(database)) as db, db.cursor() as cursor:
        cursor.execute(statement, parameters)
        return cursor.fetchall()


def native(script, *arguments):
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(script), *map(str, arguments)],
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
    )
    require(len(result.stdout) + len(result.stderr) < 131072, "helper_output_bound")
    require(result.returncode == 0, "helper_failed")
    return result.stdout


def identity():
    require(sys.platform == "linux" and sys.version_info[:2] == (3, 11), "linux_python_required")
    require(os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_REPOSITORY") == "ilovedrrealty/realty-api", "hosted_required")
    require({p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}, "loopback_only_required")
    require(TOOLS.resolve(strict=True) == TOOLS and os.statvfs(TOOLS).f_flag & os.ST_RDONLY,
            "readonly_tools_required")
    require(STATE.resolve(strict=True) == STATE and not STATE.is_symlink(), "owned_state_required")
    nonce = os.environ.get("ILDR_BACKUP_COMPAT_NONCE", "")
    require(re.fullmatch(r"[a-f0-9]{32}", nonce), "nonce_required")
    require(importlib.metadata.version("PGPy") == "0.6.0"
            and importlib.metadata.version("pyasn1") == "0.6.1", "transport_versions_required")
    manifest = json.loads((TOOLS / "source-manifest.json").read_text())
    require(manifest["version"] == 2 and len(manifest["files"]) == 6, "source_inventory_required")
    workspace = TOOLS / "workspace"
    actual = {p.relative_to(workspace).as_posix() for p in workspace.rglob("*") if p.is_file()}
    require(actual == set(manifest["files"]), "exact_sources_required")
    for name, expected in manifest["files"].items():
        path = workspace / name
        require(not path.is_symlink() and re.fullmatch(r"[a-f0-9]{64}", expected)
                and sha256(path.read_bytes()).hexdigest() == expected, "source_hash_required")
    require(rows("postgres", """SELECT current_setting('server_version_num')::int,
        host(inet_server_addr()),inet_server_port(),
        shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()""")
        == [(170006, "127.0.0.1", 55449, "ildr-backup-compat:" + nonce)], "owned_postgres_required")
    target = STATE / ("workspace_" + nonce)
    require(not target.exists(), "fresh_workspace_required")
    shutil.copytree(workspace, target)
    # The writable mirror contains only the six hash-verified public code files.
    # Frozen originals remain mounted read-only and are never changed.
    for name, expected in manifest["files"].items():
        require(sha256((target / name).read_bytes()).hexdigest() == expected, "mirror_hash_required")
    return nonce, target


def create_fixture(nonce, count):
    database = f"ildr_backup_v2_{count}_{nonce}"
    require(count in (25, 50, 51, 92), "table_count_allowlist")
    with closing(connect("postgres")) as db:
        db.autocommit = True
        with db.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_database WHERE datname=%s", (database,))
            require(cursor.fetchone() == (0,), "fresh_database_required")
            cursor.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
            cursor.execute(sql.SQL("COMMENT ON DATABASE {} IS %s").format(sql.Identifier(database)),
                           (f"ildr-backup-compat:{nonce}:{count}",))
    names = sorted((*REQUIRED, *(f"synthetic_{i:03d}" for i in range(count - len(REQUIRED)))))
    special = bytes(range(256)) + b"\x00\xff\\\"' synthetic binary \n"
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                        "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082")
    with closing(connect(database)) as db, db.cursor() as cursor:
        cursor.execute("CREATE SCHEMA extensions; CREATE EXTENSION pgcrypto WITH SCHEMA extensions")
        cursor.execute("CREATE SCHEMA storage; CREATE TABLE storage.buckets(id text PRIMARY KEY)")
        cursor.execute("""CREATE TABLE storage.objects(bucket_id text,name text,metadata jsonb,
                       updated_at timestamptz,created_at timestamptz)""")
        for index, name in enumerate(names):
            identifier = sql.Identifier(name)
            if name == "alembic_version":
                cursor.execute("CREATE TABLE public.alembic_version(version_num varchar(32) PRIMARY KEY)")
                cursor.execute("INSERT INTO public.alembic_version VALUES ('synthetic_backup_v2')")
            elif name == "professional_profiles":
                cursor.execute("""CREATE TABLE public.professional_profiles(id int PRIMARY KEY,
                    published_data jsonb,published_at timestamptz,listing_status text)""")
                cursor.execute("INSERT INTO public.professional_profiles VALUES (1,NULL,NULL,'hidden')")
            elif name == "listing_floor_plans":
                cursor.execute("""CREATE TABLE public.listing_floor_plans(id int PRIMARY KEY,
                    payload bytea NOT NULL,sha256 text NOT NULL,byte_count int NOT NULL,notes text)""")
                for number, binary in enumerate((special, png), 1):
                    cursor.execute("INSERT INTO public.listing_floor_plans VALUES (%s,%s,%s,%s,%s)",
                                   (number, psycopg2.Binary(binary), sha256(binary).hexdigest(), len(binary), "Árbol 🏡"))
            else:
                cursor.execute(sql.SQL("CREATE TABLE public.{}(id int PRIMARY KEY,title text,extra jsonb)").format(identifier))
                if index % 7:
                    cursor.execute(sql.SQL("INSERT INTO public.{} VALUES (1,%s,%s::jsonb)").format(identifier),
                                   ("Villa José 🏡\n\"quoted\"\\text", json.dumps({"nullable": None, "value": index})))
        db.commit()
    return database, names


def prepare(workspace, database, names, count, version):
    frozen = workspace / FROZEN_REL
    stamp = (datetime(2026, 11, 18, tzinfo=timezone.utc)
             + timedelta(seconds=count + (0 if version == 2 else 200))).strftime("%Y%m%dT%H%M%SZ")
    job = frozen / "daily" / stamp
    job.mkdir(parents=True, exist_ok=False)
    catalog_sql = (frozen / "daily-schema-catalog-read-only.sql").read_text()
    catalog = rows(database, catalog_sql)[0][0]
    require(catalog["project_ref"] == PROJECT and catalog["table_names"] == names, "catalog_names_required")
    (job / "schema-catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    (job / "catalog-read-only.sql").write_text(catalog_sql, encoding="utf-8")
    plan = {
        "stamp_utc": stamp, "status": "AWAITING_SCHEMA_CATALOG", "project_ref": PROJECT,
        "repositories": {
            "frontend": {"repo": "/synthetic/frontend", "ref": "refs/heads/synthetic", "commit": "1" * 40},
            "backend": {"repo": "/synthetic/backend", "ref": "refs/heads/synthetic", "commit": "2" * 40},
        },
    }
    (job / "backup-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    generator = workspace / V2_REL if version == 2 else frozen / "make-daily-full-public-query.py"
    native(generator, "--catalog", job / "schema-catalog.json", "--output", job / "snapshot-read-only.sql")
    return job, stamp


def validate_snapshot(database, names, result):
    require(result["status"] == "COMPLETE_PUBLIC_DATA_SNAPSHOT", "complete_snapshot_required")
    payloads, counts, hashes = (result[key] for key in
                               ("public_table_payloads", "public_table_record_counts", "public_table_payload_sha256"))
    require(set(payloads) == set(counts) == set(hashes) == set(names), "exact_maps_required")
    for name in names:
        raw = payloads[name].encode("utf-8")
        require(sha256(raw).hexdigest() == hashes[name]
                and len(json.loads(raw)) == counts[name], "table_count_hash_required")
        expected = rows(database, sql.SQL("""SELECT count(*),encode(sha256(convert_to(
            coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb)::text,'UTF8')),'hex')
            FROM public.{} t""").format(sql.Identifier(name)))[0]
        require(expected == (counts[name], hashes[name]), "source_rows_match_required")
    decoded = json.loads(payloads["listing_floor_plans"])
    require(len(decoded) == 2, "binary_row_count_required")
    for row in decoded:
        restored = rows(database, """SELECT octet_length(t.payload),encode(sha256(t.payload),'hex')
            FROM jsonb_populate_record(NULL::public.listing_floor_plans,%s::jsonb) t""",
                        (json.dumps(row),))[0]
        require(restored == (row["byte_count"], row["sha256"]), "bytea_roundtrip_required")
    require(result["storage_inventory_state"]["object_bytes_included"] is False, "media_scope_required")


def downstream(workspace, database, job, stamp):
    frozen = workspace / FROZEN_REL
    native(frozen / "make-daily-compressed-query.py", "--input", job / "snapshot-read-only.sql",
           "--output", job / "transport-read-only.sql")
    transport = rows(database, (job / "transport-read-only.sql").read_text())[0][0]
    require(set(transport) == {"transport", "plaintext_bytes", "plaintext_sha256", "payload_base64"}
            and transport["transport"] == "PGP_COMPRESSED_V1", "transport_interface_required")
    (job / "transport-result.json").write_text(json.dumps(transport), encoding="utf-8")
    received = json.loads(native(frozen / "receive-daily-compressed-snapshot.py", "--stamp", stamp))
    require(received["status"] == "PASS" and not received["private_key_accessed"]
            and not received["owner_passphrase_used"], "frozen_receiver_required")
    raw = (job / "recovery-snapshot.json").read_bytes()
    require(len(raw) == transport["plaintext_bytes"] and sha256(raw).hexdigest() == transport["plaintext_sha256"],
            "transport_complete_bytes_required")
    spec = importlib.util.spec_from_file_location("frozen_finalizer", frozen / "finalize-daily-full-public-backup.py")
    finalizer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(finalizer)
    calls = []

    def refuse_seal(command, check):
        # Exercise the actual frozen validator and finally cleanup, stopping at
        # the existing sealer boundary. No public/private key or cloud is used.
        expected = [sys.executable, str(frozen / "save-growth-checkpoint.py"),
                    "--phase", "daily", "--daily-stamp", stamp,
                    "--frontend-repo", "/synthetic/frontend", "--frontend-ref", "refs/heads/synthetic",
                    "--frontend-commit", "1" * 40, "--backend-repo", "/synthetic/backend",
                    "--backend-ref", "refs/heads/synthetic", "--backend-commit", "2" * 40]
        for name in ("backup-plan.json", "snapshot-read-only.sql", "transport-read-only.sql",
                     "transport-verification.json", "recovery-snapshot.json", "schema-catalog.json",
                     "catalog-read-only.sql", "snapshot-read-only.preparation.json", "snapshot-validation.json"):
            expected += ["--include", name]
        require(check is True and command == expected, "exact_sealer_interface_required")
        calls.append(True)

    with patch.object(sys, "argv", [str(frozen / "finalize-daily-full-public-backup.py"), "--stamp", stamp]), \
            patch.object(finalizer.subprocess, "run", side_effect=refuse_seal), \
            redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        finalizer.main()
    validation = json.loads((job / "snapshot-validation.json").read_text())
    require(validation["status"] == "PASS" and validation["all_public_tables_included"]
            and validation["all_profiles_private"] and len(calls) == 1, "frozen_finalizer_required")
    require(all(not (job / name).exists() for name in
                ("recovery-snapshot.json", "schema-catalog.json", "transport-result.json")),
            "synthetic_plaintext_cleanup_required")


def run():
    global stage
    nonce, workspace = identity()
    completed = []
    arity_proved = False
    last = None
    for count in (25, 50, 51, 92):
        stage = "fixture_" + str(count)
        database, names = create_fixture(nonce, count)
        stage = "v2_" + str(count)
        job, stamp = prepare(workspace, database, names, count, 2)
        result = rows(database, (job / "snapshot-read-only.sql").read_text())[0][0]
        validate_snapshot(database, names, result)
        if count in (25, 50, 51):
            stage = "old_" + str(count)
            old_job, _ = prepare(workspace, database, names, count, 1)
            try:
                old_result = rows(database, (old_job / "snapshot-read-only.sql").read_text())[0][0]
            except psycopg2.Error as error:
                require(count == 51 and error.pgcode == "54023"
                        and error.diag.message_primary == EXPECTED_ARITY_MESSAGE, "exact_arity_failure_required")
                arity_proved = True
            else:
                require(count in (25, 50), "old_51_must_fail")
                validate_snapshot(database, names, old_result)
                for key in ("public_table_payloads", "public_table_record_counts", "public_table_payload_sha256"):
                    require(old_result[key] == result[key], "old_v2_semantics_required")
        completed.append(count)
        last = database, names, job, stamp
    require(arity_proved, "old_51_failure_required")
    stage = "frozen_downstream"
    database, names, job, stamp = last
    downstream(workspace, database, job, stamp)
    stage = "schema_drift"
    with closing(connect(database)) as db, db.cursor() as cursor:
        cursor.execute("ALTER TABLE public.users ADD COLUMN synthetic_drift text")
        db.commit()
    drift = rows(database, (job / "snapshot-read-only.sql").read_text())[0][0]
    require(drift["status"] == "EXCEEDS_SAFE_BOUNDS_OR_SCHEMA_CHANGED"
            and drift["private_records_returned"] is False
            and not ({"public_table_payloads", "public_table_payload_sha256"} & set(drift)),
            "schema_drift_fail_closed_required")
    return {
        "status": "PASS", "postgres_version_num": 170006, "v2_table_counts": completed,
        "old_semantics_equal_table_counts": [25, 50], "old_51_exact_arity_error": True,
        "complete_count_hash_maps": True, "bytea_rows_typed_roundtrip": 8,
        "schema_drift_fail_closed": True, "frozen_source_copies": 5,
        "compressed_transport_verified": True, "frozen_receiver_verified": True,
        "frozen_finalizer_validation_cleanup_verified": True, "sealer_boundary_calls": 1,
        "owner_encryption_or_cloud_executed": False, "production_data_accessed": False,
        "external_network_available": False,
    }


if __name__ == "__main__":
    try:
        result = run()
    except Exception as error:
        kind = type(error).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", kind):
            kind = "UnhandledError"
        reason = str(error) if isinstance(error, Refused) else "unexpected_failure"
        if not re.fullmatch(r"[a-z_0-9]{1,70}", reason):
            reason = "unexpected_failure"
        result = {"status": "FAIL", "stage": stage, "error_type": kind, "reason": reason}
    print("BACKUP_QUERY_COMPAT_RESULT " + json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
    raise SystemExit(0 if result["status"] == "PASS" else 1)
