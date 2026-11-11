"""Unexecuted Linux CI candidate: genuine bootstrap and real 081-to-head migration.

Only the ephemeral fixture described in EXECUTION-PLAN.txt is accepted. No app
server, worker, production data, settings secrets or provider is needed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

BASE = "0811946ea02a310b03116960663e3ba6871a07d0"
OLD = "52b2edc810ad"
HEAD = "a4b6c0d2e834"
REGISTRY_HEAD = "f3a5b9c1d723"
CHAIN = [HEAD, REGISTRY_HEAD, "e2f4a8b0c612", "d1e3f7a9b521", "c90e75f2a6d1", "b12d41e2c9f0", "82e1a7c4d9b0"]
ANCHOR = "ildr_platform_test_20261025"
PREFIX = "ildr_migration_ci_"
DB_NAME = re.compile(r"ildr_migration_ci_[ab]_[a-f0-9]{32}\Z")
LEGACY = frozenset({
    "users", "pending_users", "listings", "upgrade_requests", "inquiries",
    "anonymous_leads", "listing_edits", "saved_homes", "bookings", "activity_log",
    "deal_requests", "listing_events", "leads", "settings", "bulk_import_jobs",
    "website_settings", "website_settings_revisions", "professional_profiles",
    "professional_profile_revisions", "research_workspaces",
})
NEW = frozenset({
    "lead_delivery_jobs", "lead_delivery_worker_state", "listing_location_reviews",
    "listing_location_review_revisions", "property_management_records",
    "property_management_revisions", "network_projects", "network_project_members",
    "network_milestones", "network_project_updates", "network_project_comments",
    "social_network_profiles", "network_posts", "network_post_comments",
    "network_blocks", "network_media", "network_audit", "network_properties",
    "network_service_preferences", "network_service_requests", "network_service_recipients",
    "network_service_proposals", "network_service_actions",
})
SERVICE = frozenset({"network_service_preferences", "network_service_requests", "network_service_recipients",
                     "network_service_proposals", "network_service_actions"})
REGISTRY = NEW - SERVICE
PRE_REGISTRY = REGISTRY - {"network_properties"}
ALLOWED_ROLE = ("anon", "authenticated")
PG17_TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN")
MAX_PROOF = 131072


def need(ok, code):
    if not ok:
        raise RuntimeError(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path, limit=MAX_PROOF):
    need(path.is_file() and not path.is_symlink() and path.stat().st_size <= limit, "BOUNDED_REGULAR_JSON_REQUIRED")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def exclusive_json(path, value):
    raw = json.dumps(value, sort_keys=True, indent=2).encode()
    need(len(raw) <= MAX_PROOF, "PROOF_TOO_LARGE")
    with path.open("xb") as stream:
        stream.write(raw)


def save_journal(path, value):
    temporary = path.with_name(path.name + ".next")
    exclusive_json(temporary, value)
    os.replace(temporary, path)


def verify_sources(source, baseline, manifest):
    need(manifest["baseline_commit"] == BASE and manifest["candidate_source_count"] == 204, "SOURCE_SCOPE_CHANGED")
    for root, entries, algorithm in (
        (source, manifest["candidate_lf_sha256"], "sha256"),
        (baseline, manifest["baseline_git_blobs"], "git-blob-sha1"),
    ):
        need(root.is_dir() and not root.is_symlink(), "REGULAR_SOURCE_DIRECTORY_REQUIRED")
        for name, expected in entries.items():
            need(not name.startswith("/") and ".." not in Path(name).parts, "SOURCE_PATH_REFUSED")
            path = root / name
            need(path.is_file() and not path.is_symlink() and root.resolve() in path.resolve().parents, "SOURCE_FILE_REFUSED")
            raw = path.read_bytes()
            actual = sha(raw) if algorithm == "sha256" else hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            need(actual == expected, "SOURCE_BYTES_CHANGED")
        expected = set(entries)
        # Git metadata and independently hashed CI-only operational files are
        # excluded from app imports; no unexpected app module may be injected.
        operational = manifest.get("candidate_operational_sha256", {}) if root == source else {}
        for name, pin in operational.items():
            need(name.startswith((".github/workflows/", "ci/")), "OPERATIONAL_SCOPE_REFUSED")
            path = root / name
            need(path.is_file() and not path.is_symlink() and sha(path.read_bytes()) == pin, "OPERATIONAL_BYTES_CHANGED")
        actual_files = {str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*")
                        if p.is_file() and ".git" not in p.relative_to(root).parts}
        need(actual_files == expected | set(operational), "UNEXPECTED_SOURCE_FILE_SET")
        need(not (root / ".env").exists(), "ENV_FILE_REFUSED")


def url_for(name):
    need(name == ANCHOR or DB_NAME.fullmatch(name), "DATABASE_NAME_REFUSED")
    return "postgresql+psycopg2://postgres@127.0.0.1:55449/" + name


def clean_environment(name, application):
    keep = {key: value for key, value in os.environ.items() if key in {
        "PATH", "LANG", "LC_ALL", "TZ", "HOME", "TMPDIR", "SSL_CERT_FILE",
    }}
    keep.update(DATABASE_URL=url_for(name), JWT_SECRET="synthetic-ci-migration-only",
        ENVIRONMENT="development", GHL_ENABLED="false", LEAD_DELIVERY_ENABLED="false",
        CUSTOMER_MESSAGES_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="",
        GHL_WEBHOOK_SECRET="", RESEND_API_KEY="", NOTIFY_EMAIL="", SUPABASE_URL="",
        SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="",
        PAYPAL_WEBHOOK_ID="", PGHOST="127.0.0.1", PGPORT="55449", PGUSER="postgres",
        PGDATABASE=name, PGPASSFILE="/dev/null", PGCONNECT_TIMEOUT="5",
        PGSSLMODE="disable", PGGSSENCMODE="disable", PGAPPNAME=application,
        PGOPTIONS="-c statement_timeout=5000 -c lock_timeout=2000",
        PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    return keep


def socket_guard():
    def audit(event, args):
        if event in ("subprocess.Popen", "os.system", "socket.bind", "socket.sendto", "socket.sendmsg"):
            raise PermissionError("MIGRATION_EXTERNAL_ACTION_REFUSED")
        if event == "socket.connect":
            target = args[1]
            if not (isinstance(target, tuple) and target[:2] == ("127.0.0.1", 55449)):
                raise PermissionError("MIGRATION_EXTERNAL_CONNECT_REFUSED")
        if event in ("socket.getaddrinfo", "socket.gethostbyname") and args[0] != "127.0.0.1":
            raise PermissionError("MIGRATION_EXTERNAL_DNS_REFUSED")
    sys.addaudithook(audit)


def db_facts(connection, text):
    row = connection.execute(text("SELECT oid,datdba,shobj_description(oid,'pg_database') AS marker FROM pg_database WHERE datname=current_database()" )).mappings().one()
    return dict(row)


def parent(args):
    need(sys.platform == "linux" and sys.version_info[:2] == (3, 11), "LINUX_PYTHON311_REQUIRED")
    need(os.environ.get("CI") == "true" and os.environ.get("GITHUB_ACTIONS") == "true"
         and os.environ.get("ILDR_EPHEMERAL_CI") == "true", "EPHEMERAL_CI_REQUIRED")
    run, attempt = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_RUN_ATTEMPT", "")
    need(re.fullmatch(r"[1-9][0-9]{0,19}", run) and re.fullmatch(r"[1-9][0-9]{0,5}", attempt), "CI_RUN_ID_REQUIRED")
    need(sha(Path(__file__).read_bytes()) == args.self_sha256, "RUNNER_SOURCE_CHANGED")
    need(args.database_url == url_for(ANCHOR), "FIXED_QUERYLESS_FIXTURE_REQUIRED")
    raw = args.manifest.read_bytes()
    need(len(raw) < 4194304 and sha(raw) == args.manifest_sha256, "MANIFEST_CHANGED")
    manifest = json.loads(raw.decode("utf-8-sig"))
    verify_sources(args.source, args.baseline_source, manifest)
    need(args.result == Path("/results/migration-result.json") and args.result.parent.is_dir()
         and not args.result.exists() and not args.result.is_symlink(), "EXCLUSIVE_RESULT_REQUIRED")
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool
    nonce = uuid4().hex
    parent_environment = clean_environment(ANCHOR, "ildr-migration-" + nonce + "-parent")
    os.environ.clear()
    os.environ.update(parent_environment)
    admin = create_engine(url_for(ANCHOR), poolclass=NullPool, isolation_level="AUTOCOMMIT")
    marker = "ildr-ephemeral-ci:" + run + ":" + attempt
    registry = []
    report = {"status": "FAILED", "scope": "SYNTHETIC_CI_BOOTSTRAP_AND_MIGRATIONS_ONLY",
              "baseline_commit": BASE, "target_revision": HEAD, "source_manifest_sha256": args.manifest_sha256,
              "runner_sha256": args.self_sha256, "ci_run_id": run, "ci_run_attempt": attempt,
              "python_version": sys.version.split()[0], "phases": [], "cleanup_errors": [],
              "production_provider_or_server_actions": False, "databases_absent": False, "source_unchanged": False}
    journal = args.result.parent / ("migration-databases-" + nonce + ".json")
    exclusive_json(journal, {"nonce": nonce, "marker": marker, "databases": registry})
    deadline = time.monotonic() + 480
    fixture_state = None

    def fixture(connection):
        nonlocal fixture_state
        actual = connection.execute(text("SELECT current_database(),current_user,host(inet_server_addr()),inet_server_port()" )).one()
        version = int(connection.execute(text("SHOW server_version_num")).scalar_one())
        anchor = db_facts(connection, text)
        need(tuple(actual) == (ANCHOR, "postgres", "127.0.0.1", 55449)
             and version // 10000 == 17 and anchor["marker"] == marker, "ACTUAL_EPHEMERAL_PG17_ANCHOR_REQUIRED")
        observed = {"identity": tuple(actual), "server_version_num": version, "anchor": anchor}
        if fixture_state is None:
            fixture_state = observed
            report["fixture_identity"] = observed
        else:
            need(observed == fixture_state, "FROZEN_EPHEMERAL_FIXTURE_CHANGED")

    def identity(item):
        with admin.connect() as connection:
            fixture(connection)
            row = connection.execute(text("SELECT oid,datdba,shobj_description(oid,'pg_database') AS marker FROM pg_database WHERE datname=:name"), {"name": item["name"]}).mappings().one()
            need(row["oid"] == item["oid"] and row["datdba"] == item["owner"], "DATABASE_IDENTITY_CHANGED")
            need(row["marker"] == (item["marker"] if item["comment_applied"] else None), "DATABASE_MARKER_CHANGED")

    def drop(item):
        need(item["created"] and DB_NAME.fullmatch(item["name"]) and item["oid"] is not None, "OWNED_DATABASE_REQUIRED")
        need(all(record.get("child_reaped") is True for record in report["phases"] if "pid" in record), "OWNED_CHILD_REAP_REQUIRED_BEFORE_DROP")
        identity(item)
        # All subprocesses have exited/reaped before this point. Refuse an
        # unexplained remaining session; never terminate unrelated sessions.
        deadline = time.monotonic() + 3
        while True:
            with admin.connect() as connection:
                sessions = connection.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname=:name"), {"name": item["name"]}).scalar_one()
            if sessions == 0 or time.monotonic() >= deadline:
                break
            time.sleep(.1)
        need(sessions == 0, "DATABASE_CLIENT_REMAINS")
        identity(item)
        with admin.connect() as connection:
            connection.exec_driver_sql('DROP DATABASE "' + item["name"] + '"')
            need(connection.execute(text("SELECT count(*) FROM pg_database WHERE datname=:name"), {"name": item["name"]}).scalar_one() == 0, "DATABASE_DROP_UNVERIFIED")
        item["dropped"] = True
        save_journal(journal, {"nonce": nonce, "marker": marker, "databases": registry})

    def phase(item, source, mode, previous=None):
        identity(item)
        label = item["name"][len(PREFIX)] + "-" + mode
        proof = args.result.parent / (label + "-" + nonce + ".json")
        argv = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--child", "--source", str(source),
                "--database", item["name"], "--mode", mode, "--proof", str(proof)]
        if previous:
            argv += ["--previous", str(previous), "--previous-sha256", sha(previous.read_bytes())]
        need(time.monotonic() < deadline, "OVERALL_DEADLINE_EXCEEDED")
        record = {"phase": label, "proof": proof.name, "native_exit": None, "child_reaped": False, "timed_out": False}
        report["phases"].append(record)
        with tempfile.TemporaryDirectory(prefix="ildr-migration-empty-") as cwd:
            child = None
            try:
                child = subprocess.Popen(argv, cwd=cwd, env=clean_environment(item["name"], "ildr-migration-" + nonce + "-" + label),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                record["pid"] = child.pid
                try:
                    child.wait(timeout=min(150, max(1, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    record["timed_out"] = True
            finally:
                if child is not None:
                    try:
                        if child.poll() is None:
                            try:
                                child.terminate()
                            except OSError:
                                record["terminate_error"] = True
                            try:
                                child.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                child.kill()
                                child.wait(timeout=10)
                        else:
                            child.wait(timeout=1)
                    finally:
                        record["native_exit"] = child.returncode
                        record["child_reaped"] = child.poll() is not None
        if proof.is_file():
            record["sha256"] = sha(proof.read_bytes())
            record["child_result"] = read_json(proof).get("failure_code")
        need(record["native_exit"] == 0 and record["child_reaped"] and not record["timed_out"], "MIGRATION_CHILD_FAILED")
        value = read_json(proof)
        need(value["status"] == "ACTUAL_PHASE_COMPLETED" and value["database"] == item["name"]
             and value["mode"] == mode and value["database_oid"] == item["oid"], "PHASE_PROOF_MISMATCH")
        identity(item)
        return proof

    try:
        with admin.connect() as connection:
            fixture(connection)
            need(connection.execute(text("SELECT count(*) FROM pg_database WHERE datname LIKE 'ildr_migration_ci_%'")).scalar_one() == 0, "PRIOR_MIGRATION_DATABASE_REFUSED")
            for role in ALLOWED_ROLE:
                exists = connection.execute(text("SELECT count(*) FROM pg_roles WHERE rolname=:role"), {"role": role}).scalar_one()
                if not exists:
                    connection.exec_driver_sql('CREATE ROLE "' + role + '" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS')
                flags = connection.execute(text("SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=:role"), {"role": role}).one()
                need(tuple(flags) == (False,) * 7, "NONLOGIN_FIXTURE_ROLE_REQUIRED")
                need(connection.execute(text("SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=:role"), {"role": role}).scalar_one() == 0, "FIXTURE_ROLE_MEMBERSHIP_REFUSED")
        for kind in ("a", "b"):
            item = {"name": PREFIX + kind + "_" + nonce, "oid": None, "owner": None,
                    "marker": "ildr-owned-migration:" + nonce + ":" + kind, "created": False, "comment_applied": False}
            registry.append(item)
            save_journal(journal, {"nonce": nonce, "marker": marker, "databases": registry})
            with admin.connect() as connection:
                connection.exec_driver_sql('CREATE DATABASE "' + item["name"] + '" TEMPLATE template0')
                item["created"] = True
                row = connection.execute(text("SELECT oid,datdba FROM pg_database WHERE datname=:name"), {"name": item["name"]}).one()
                item["oid"], item["owner"] = row
                save_journal(journal, {"nonce": nonce, "marker": marker, "databases": registry})
                connection.exec_driver_sql('COMMENT ON DATABASE "' + item["name"] + '" IS \'' + item["marker"] + "'")
                item["comment_applied"] = True
                save_journal(journal, {"nonce": nonce, "marker": marker, "databases": registry})
            if kind == "a":
                fresh_proof = phase(item, args.source, "fresh")
            else:
                previous = phase(item, args.baseline_source, "baseline")
                upgrade_proof = phase(item, args.source, "upgrade", previous)
                fresh_value = read_json(fresh_proof)
                upgrade_value = read_json(upgrade_proof)
                need(fresh_value["new_schema_sha256"] == upgrade_value["new_schema_sha256"], "FRESH_UPGRADE_SCHEMA_DIVERGENCE")
                need(fresh_value["catalog_sha256"] == upgrade_value["catalog_sha256"], "FRESH_UPGRADE_CATALOG_DIVERGENCE")
            # Free each database before creating the next; final cleanup also
            # handles a failure at any later stage without touching other DBs.
            drop(item)
        verify_sources(args.source, args.baseline_source, manifest)
        report["status"] = "ACTUAL_BOOTSTRAP_AND_MIGRATION_ASSERTIONS_COMPLETED_CLEANUP_PENDING"
    except Exception:
        report["failure_code"] = "BOUNDED_MIGRATION_ACCEPTANCE_FAILED"
    finally:
        for item in registry:
            if item["created"] and not item.get("dropped"):
                try:
                    drop(item)
                except Exception:
                    report["cleanup_errors"].append("EXACT_DATABASE_CLEANUP_UNRESOLVED:" + item["name"])
        try:
            with admin.connect() as connection:
                fixture(connection)
                report["databases_absent"] = all(connection.execute(text("SELECT count(*) FROM pg_database WHERE datname=:name"), {"name": item["name"]}).scalar_one() == 0 for item in registry)
        except Exception:
            report["cleanup_errors"].append("DATABASE_ABSENCE_UNVERIFIED")
        try:
            verify_sources(args.source, args.baseline_source, manifest)
            report["source_unchanged"] = True
        except Exception:
            report["failure_code"] = "SOURCE_CHANGED_DURING_MIGRATION_RUN"
        admin.dispose()
        save_journal(journal, {"nonce": nonce, "marker": marker, "databases": registry})
        if (report["status"].endswith("CLEANUP_PENDING") and report["databases_absent"]
                and report["source_unchanged"] and not report["cleanup_errors"]):
            report["status"] = "PASS_ACTUAL_CI_BOOTSTRAP_MIGRATIONS_AND_EXACT_DATABASE_CLEANUP"
        exclusive_json(args.result, report)
    return 0 if report["status"].startswith("PASS_") else 1

def fingerprint(connection, tables, text):
    result = {}
    for name in sorted(tables):
        need(re.fullmatch(r"[a-z_]+", name), "FIXED_TABLE_NAME_REQUIRED")
        row = connection.exec_driver_sql('SELECT count(*) AS n,COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),\'[]\'::jsonb)::text AS body FROM "' + name + '" t').one()
        result[name] = {"rows": row.n, "sha256": sha(row.body.encode())}
    return result


def pre_registry_history(connection, text):
    # f3's empty downgrade removes exactly four newly added NULL columns.
    # Compare every original50 field, not row JSON with different field sets.
    # This applies ONLY before any registry/creation identity exists; full raw
    # schema parity and populated18-row retention never use this projection.
    need(connection.execute(text("SELECT count(*) FROM network_properties")).scalar_one() == 0,
         "EMPTY_REGISTRY_RETENTION_FIXTURE_REQUIRED")
    need(connection.execute(text("SELECT count(*) FROM network_projects WHERE property_id IS NOT NULL OR created_by_id IS NOT NULL OR submission_id IS NOT NULL OR creation_sha256 IS NOT NULL")).scalar_one() == 0,
         "NULL_NEW_PROJECT_METADATA_RETENTION_FIXTURE_REQUIRED")
    result = fingerprint(connection, PRE_REGISTRY, text)
    row = connection.exec_driver_sql("SELECT count(*) AS n,COALESCE(jsonb_agg(body ORDER BY body::text),'[]'::jsonb)::text AS body FROM (SELECT to_jsonb(t)-'property_id'-'created_by_id'-'submission_id'-'creation_sha256' AS body FROM network_projects t) original_fields").one()
    result["network_projects"] = {"rows": row.n, "sha256": sha(row.body.encode())}
    return result


def schema_facts(connection, tables, inspect, diagnostic=None):
    inspector = inspect(connection)
    result = {}
    for name in sorted(tables):
        result[name] = {
            "columns": [{"name": c["name"], "type": str(c["type"]), "nullable": c["nullable"], "default": c["default"]}
                        for c in inspector.get_columns(name, schema="public")],
            "pk": inspector.get_pk_constraint(name, schema="public"),
            "fk": inspector.get_foreign_keys(name, schema="public"),
            "unique": inspector.get_unique_constraints(name, schema="public"),
            "checks": inspector.get_check_constraints(name, schema="public"),
            "indexes": inspector.get_indexes(name, schema="public"),
        }
    if diagnostic is not None:
        diagnostic.update(schema_diagnostic(result))
    return sha(json.dumps(result, sort_keys=True, default=str).encode())


def schema_diagnostic(facts):
    # Hash-only evidence. Never mutate the raw projection or accept its
    # separately sorted diagnostic as a replacement for the parity guard.
    need(set(facts) == NEW, "FIXED_NEW_SCHEMA_DIAGNOSTIC_SCOPE_REQUIRED")
    digest = lambda value: sha(json.dumps(value, sort_keys=True, default=str).encode())
    components, sorted_projection = {}, {}
    for name in sorted(NEW):
        table = facts[name]
        need(set(table) == {"columns", "pk", "fk", "unique", "checks", "indexes"}, "EXACT_SCHEMA_DIAGNOSTIC_COMPONENTS_REQUIRED")
        columns = table["columns"]
        names = [column["name"] for column in columns]
        need(0 < len(names) <= 256 and len(set(names)) == len(names), "UNIQUE_BOUNDED_SCHEMA_COLUMNS_REQUIRED")
        ordered = {**table, "columns": sorted(columns, key=lambda column: column["name"])}
        sorted_projection[name] = ordered
        components[name] = {
            "column_count": len(names),
            "user_id_ordinal": names.index("user_id") + 1 if name == "lead_delivery_jobs" else None,
            "raw_table_sha256": digest(table),
            "columns_order_sha256": digest(columns),
            "column_names_order_sha256": digest(names),
            "columns_by_name_sha256": digest({column["name"]: column for column in columns}),
            "columns_sorted_table_sha256": digest(ordered),
            **{kind + "_sha256": digest(table[kind]) for kind in ("pk", "fk", "unique", "checks", "indexes")},
        }
    value = {"schema_version": 1, "scope": "FIXED23_SCHEMA_COMPONENT_HASHES_DIAGNOSTIC_ONLY",
             "table_count": len(NEW), "raw_schema_sha256": digest(facts),
             "columns_sorted_schema_sha256": digest(sorted_projection), "tables": components}
    need(len(json.dumps(value, sort_keys=True).encode()) <= 32768, "BOUNDED_SCHEMA_DIAGNOSTIC_REQUIRED")
    return value


def privacy(connection, text, tables=NEW):
    for name in sorted(tables):
        facts = connection.execute(text("SELECT c.relrowsecurity,c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AS own,n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=CAST(:table AS regclass)"), {"table": "public." + name}).one()
        need(tuple(facts) == (True, True, "public"), "TABLE_RLS_OWNER_OR_SCHEMA_MISMATCH")
        grants = connection.execute(text("SELECT count(*) FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a WHERE c.oid=CAST(:table AS regclass) AND a.grantee=0"), {"table": "public." + name}).scalar_one()
        need(grants == 0, "PUBLIC_TABLE_GRANT_REMAINS")
        need(connection.execute(text("SELECT count(*) FROM pg_policies WHERE schemaname='public' AND tablename=:table"), {"table": name}).scalar_one() == 0, "UNEXPECTED_RLS_POLICY")
        for role in ALLOWED_ROLE:
            for privilege in PG17_TABLE_PRIVILEGES:
                need(connection.execute(text("SELECT has_table_privilege(:role,:table,:priv)"), {"role": role, "table": "public." + name, "priv": privilege}).scalar_one() is False, "DATA_API_ROLE_PRIVILEGE_REMAINS")


def catalog(connection, metadata, inspect, text, CheckConstraint, UniqueConstraint, *, registry_only=False):
    # The restricted18-table mode is used ONLY while empty a4 is genuinely
    # downgraded to f3, never as a replacement for current23 raw schema parity.
    scope = REGISTRY if registry_only else NEW
    inspector = inspect(connection)
    need(set(inspector.get_table_names(schema="public")) == LEGACY | scope | {"alembic_version"}, "EXACT_TABLE_SET_MISMATCH")
    signatures = {}
    for name in sorted(scope):
        model = metadata.tables[name]
        columns = {c["name"]: c for c in inspector.get_columns(name, schema="public")}
        need(set(columns) == set(model.columns.keys()), "COLUMN_SET_MISMATCH")
        for expected in model.columns:
            actual = columns[expected.name]
            need(str(actual["type"].compile(dialect=connection.dialect)).upper() == str(expected.type.compile(dialect=connection.dialect)).upper()
                 and actual["nullable"] == expected.nullable, "COLUMN_TYPE_OR_NULLABILITY_MISMATCH")
            default = expected.server_default.arg if expected.server_default is not None else None
            normalize = lambda value: None if value is None else re.sub(r"::(?:text|integer|boolean|numeric)", "", str(value).lower()).strip().strip("'\"")
            # Do not hide SERIAL/implicit defaults or any other fresh-versus-
            # upgrade discrepancy by ignoring an unexpected physical default.
            need(normalize(actual["default"]) == normalize(default), "PHYSICAL_DEFAULT_DIFFERS_FROM_DECLARED_MODEL")
        need(tuple(inspector.get_pk_constraint(name, schema="public")["constrained_columns"]) == tuple(c.name for c in model.primary_key.columns), "PRIMARY_KEY_MISMATCH")
        expected_fk = {(tuple(e.parent.name for e in fk.elements), fk.referred_table.name,
                        tuple(e.column.name for e in fk.elements), (fk.ondelete or "NO ACTION").upper()) for fk in model.foreign_key_constraints}
        actual_fk = {(tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]),
                      (fk.get("options", {}).get("ondelete") or "NO ACTION").upper()) for fk in inspector.get_foreign_keys(name, schema="public")}
        need(actual_fk == expected_fk, "FOREIGN_KEY_MISMATCH")
        checks = inspector.get_check_constraints(name, schema="public")
        need({c["name"] for c in checks} == {c.name for c in model.constraints if isinstance(c, CheckConstraint)}, "CHECK_SET_MISMATCH")
        for expected in (c for c in model.constraints if isinstance(c, CheckConstraint)):
            actual = next(c["sqltext"] for c in checks if c["name"] == expected.name)
            literal_set = lambda value: set(re.findall(r"'([^']*)'", str(value)))
            # PostgreSQL may cast/quote numeric bounds in its deparser. Token
            # comparisons supplement, never replace, actual negative operations.
            numbers = lambda value: sorted(re.findall(r"(?<![\w])-?\d+(?:\.\d+)?(?![\w])", str(value)))
            need({v for v in literal_set(actual) if not re.fullmatch(r"-?\d+(?:\.\d+)?", v)} ==
                 {v for v in literal_set(expected.sqltext) if not re.fullmatch(r"-?\d+(?:\.\d+)?", v)}
                 and numbers(actual) == numbers(expected.sqltext), "CHECK_LITERAL_OR_BOUND_MISMATCH")
        unique = inspector.get_unique_constraints(name, schema="public")
        need({tuple(c["column_names"]) for c in unique} == {tuple(c.name for c in u.columns) for u in model.constraints if isinstance(u, UniqueConstraint)}, "UNIQUE_SET_MISMATCH")
        for expected in (u for u in model.constraints if isinstance(u, UniqueConstraint) and u.name):
            need(any(u["name"] == expected.name and tuple(u["column_names"]) == tuple(c.name for c in expected.columns) for u in unique), "NAMED_UNIQUE_MISMATCH")
        signatures[name] = {"checks": checks, "indexes": inspector.get_indexes(name, schema="public")}
        required = {index.name: (index.unique, tuple(c.name for c in index.columns)) for index in model.indexes}
        if name == "listing_location_reviews":
            required["ix_location_review_status"] = (False, ("review_status", "listing_id"))
        actual_indexes = {i["name"]: i for i in signatures[name]["indexes"]}
        need({i["name"] for i in signatures[name]["indexes"] if not i.get("duplicates_constraint")} == set(required), "PLAIN_INDEX_SET_MISMATCH")
        for index, (unique_flag, cols) in required.items():
            actual = actual_indexes.get(index)
            need(actual is not None and bool(actual["unique"]) == unique_flag and tuple(actual["column_names"]) == cols, "REQUIRED_INDEX_MISMATCH")
            if index == "uq_management_recurring_origin":
                predicate = str(actual.get("dialect_options", {}).get("postgresql_where", "")).lower()
                need("task" in predicate and "origin_record_id is not null" in predicate, "PARTIAL_INDEX_PREDICATE_MISMATCH")
            if index == "uq_service_recipient_winner":
                predicate = str(actual.get("dialect_options", {}).get("postgresql_where", "")).lower()
                # PG17 deparses this as (status = 'awarded'::text). Require
                # the actual equality, including its direction and one value.
                canonical_predicate = re.sub(r"[()\s]", "", predicate.replace("::text", ""))
                need(canonical_predicate == "status='awarded'", "SERVICE_WINNER_PREDICATE_MISMATCH")
    privacy(connection, text, tables=scope)
    triggers = connection.execute(text("SELECT c.relname,t.tgname,t.tgenabled,t.tgtype,p.proname AS function_name,pn.nspname AS function_schema,pg_get_triggerdef(t.oid) AS definition FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace pn ON pn.oid=p.pronamespace WHERE NOT t.tgisinternal AND n.nspname='public' AND (t.tgname IN ('network_append_only','network_content_guard','network_registry_identity','network_project_creation_identity') OR c.relname IN ('network_service_preferences','network_service_requests','network_service_recipients','network_service_proposals','network_service_actions')) ORDER BY c.relname,t.tgname")).mappings().all()
    expected_triggers = {(t, "network_append_only") for t in ("network_project_updates", "network_project_comments", "network_audit")} | {(t, "network_content_guard") for t in ("network_posts", "network_post_comments", "network_media")}
    expected_triggers |= {("network_properties", "network_registry_identity"), ("network_projects", "network_project_creation_identity")}
    trigger_types = {pair: 27 for pair in expected_triggers}
    if not registry_only:
        trigger_types.update({(table, "service_identity"): 27 for table in SERVICE})
        trigger_types.update({(table, "service_catalog"): 23 for table in ("network_service_preferences", "network_service_requests")})
        trigger_types.update({(table, "service_links"): 23 for table in ("network_service_recipients", "network_service_proposals", "network_service_actions")})
    need({(t["relname"], t["tgname"]) for t in triggers} == set(trigger_types)
         and all(t["tgenabled"] == "O" and t["tgtype"] == trigger_types[(t["relname"], t["tgname"])] for t in triggers), "ACTUAL_TRIGGER_SET_MISMATCH")
    service_functions = {
        "service_identity": "network_guard_service_identity",
        "service_catalog": "network_guard_service_catalog",
        "service_links": "network_guard_service_links",
    }
    need(all(t["function_schema"] == "public" and t["function_name"] == service_functions[t["tgname"]]
             for t in triggers if t["relname"] in SERVICE), "SERVICE_TRIGGER_FUNCTION_MISMATCH")
    functions = connection.execute(text("SELECT p.proname,p.prosecdef,l.lanname,pg_get_functiondef(p.oid) AS definition FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang WHERE n.nspname='public' AND p.proname IN ('network_reject_history_change','network_guard_content','network_guard_registry_property','network_guard_project_creation_identity','network_guard_service_identity','network_guard_service_catalog','network_guard_service_links') ORDER BY p.proname")).mappings().all()
    expected_functions = {"network_reject_history_change", "network_guard_content", "network_guard_registry_property", "network_guard_project_creation_identity"}
    if not registry_only:
        expected_functions |= {"network_guard_service_identity", "network_guard_service_catalog", "network_guard_service_links"}
    need({f["proname"] for f in functions} == expected_functions
         and all(not f["prosecdef"] and f["lanname"] == "plpgsql" for f in functions), "ACTUAL_INVOKER_FUNCTIONS_REQUIRED")
    for function in sorted(expected_functions - {"network_reject_history_change", "network_guard_content"}):
        public = connection.execute(text("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE n.nspname='public' AND p.proname=:name AND a.grantee=0"), {"name": function}).scalar_one()
        need(public == 0, "PUBLIC_REGISTRY_TRIGGER_FUNCTION_GRANT_REMAINS")
        for role in ALLOWED_ROLE:
            need(connection.execute(text("SELECT has_function_privilege(:role,:function,'EXECUTE')"), {"role": role, "function": "public." + function + "()"}).scalar_one() is False, "DATA_API_REGISTRY_TRIGGER_FUNCTION_GRANT_REMAINS")
    return sha(json.dumps({"tables": signatures, "triggers": [dict(r) for r in triggers], "functions": [dict(r) for r in functions]}, sort_keys=True, default=str).encode())


def seed_baseline(connection, metadata):
    from decimal import Decimal
    ids = {key: uuid4() for key in ("actor", "other", "listing", "lead")}
    for index, key in enumerate(("actor", "other"), 710001):
        connection.execute(metadata.tables["users"].insert(), {"id": ids[key], "user_code": index,
            "email": key + "@synthetic.example.invalid", "display_name": "Synthetic " + key,
            "role": "owner", "status": "active", "email_verified": True})
    connection.execute(metadata.tables["listings"].insert(), {"id": ids["listing"], "title": "Synthetic migration villa",
        "type": "villa", "transaction": "rent", "price": Decimal("1000.00"), "location": "Synthetic fixture",
        "submitted_by": ids["actor"], "owner_id": ids["actor"], "status": "draft", "bedrooms": 3,
        "bathrooms": Decimal("3.0"), "latitude": Decimal("19.7000000"), "longitude": Decimal("-70.4000000")})
    connection.execute(metadata.tables["leads"].insert(), {"id": ids["lead"], "type": "buyer_interest",
        "name": "Synthetic historical event", "phone": "+18095550100", "status": "closed",
        "tracking": {"internal_test": True, "measurement_consent": "denied"}})
    for number in range(46):
        profile = uuid4()
        connection.execute(metadata.tables["professional_profiles"].insert(), {"id": profile, "user_id": None,
            "version": 1, "review_status": "draft", "listing_status": "hidden",
            "published_at": None, "draft_data": {"business_name": "Synthetic private " + str(number), "public_contact_consent": False}})
        connection.execute(metadata.tables["professional_profile_revisions"].insert(), {"id": uuid4(),
            "profile_id": profile, "version": 1, "action": "synthetic_fixture", "snapshot": {"public_contact_consent": False}})
    return {key: str(value) for key, value in ids.items()}


def seed_service_actors(connection, metadata, ids):
    # These users are inserted under ACTUAL081 before its legacy fingerprints
    # are frozen. Current migrations must not create, alter or notify them.
    for number, key in enumerate(("service_p1", "service_p2", "service_p3", "service_outsider",
                                 "service_unverified", "service_unused"), 710010):
        identity = uuid4()
        connection.execute(metadata.tables["users"].insert(), {"id": identity, "user_code": number,
            "email": key + "@synthetic.example.invalid", "display_name": "Synthetic service member",
            "role": "buyer", "status": "active", "email_verified": key != "service_unverified"})
        ids[key] = str(identity)
    return ids


def strict_profiles(connection, text):
    need(connection.execute(text("SELECT count(*) FROM professional_profiles")).scalar_one() == 46, "SYNTHETIC_PROFILE_COUNT_CHANGED")
    need(connection.execute(text("SELECT count(*) FROM professional_profiles WHERE user_id IS NULL AND review_status='draft' AND listing_status='hidden' AND published_data IS NULL AND published_at IS NULL AND draft_data->'public_contact_consent'='false'::jsonb")).scalar_one() == 46, "SYNTHETIC_PROFILE_PRIVACY_CHANGED")


def seed_expansion(connection, metadata, baseline_ids):
    from datetime import date
    from decimal import Decimal
    from uuid import UUID
    ids = {key: UUID(value) for key, value in baseline_ids.items()}
    for key in ("job", "signup", "task", "task_revision", "project", "other_project", "member", "milestone",
                "update", "other_update", "project_comment", "post", "other_post", "post_comment",
                "media", "wall_media", "audit"):
        ids[key] = uuid4()
    tables = metadata.tables
    connection.execute(tables["lead_delivery_jobs"].insert(), {"id": ids["job"], "lead_id": ids["lead"],
        "step": "synthetic_retained", "provider": "synthetic", "payload": {"synthetic_only": True},
        "payload_sha256": "0" * 64, "status": "accepted", "attempt_count": 1})
    connection.execute(tables["lead_delivery_jobs"].insert(), {"id": ids["signup"], "user_id": ids["actor"],
        "step": "synthetic_signup", "provider": "synthetic", "payload": {"synthetic_only": True},
        "payload_sha256": "1" * 64, "status": "accepted", "attempt_count": 1})
    connection.execute(tables["lead_delivery_worker_state"].insert(), {"id": 1, "last_poll_at": datetime(2026, 1, 1, tzinfo=timezone.utc)})
    connection.execute(tables["listing_location_reviews"].insert(), {"listing_id": ids["listing"], "version": 1,
        "review_status": "verified", "public_precision": "hidden", "latitude": Decimal("19.7"), "longitude": Decimal("-70.4")})
    connection.execute(tables["listing_location_review_revisions"].insert(), {"listing_id": ids["listing"], "version": 1,
        "action": "synthetic", "snapshot": {"synthetic_only": True}})
    connection.execute(tables["property_management_records"].insert(), {"id": ids["task"], "listing_id": ids["listing"],
        "kind": "task", "title": "Synthetic task", "status": "open", "data": {}, "version": 1})
    connection.execute(tables["property_management_revisions"].insert(), {"id": ids["task_revision"], "record_id": ids["task"],
        "version": 1, "action": "synthetic", "snapshot": {}})
    for key in ("project", "other_project"):
        connection.execute(tables["network_projects"].insert(), {"id": ids[key], "listing_id": ids["listing"],
            "owner_id": ids["actor"], "title": "Synthetic project", "summary": "", "property_class": "villa",
            "service_category_ids": [], "status": "open", "version": 1})
    connection.execute(tables["network_project_members"].insert(), {"id": ids["member"], "project_id": ids["project"],
        "user_id": ids["other"], "role": "viewer", "active": True})
    connection.execute(tables["network_milestones"].insert(), {"id": ids["milestone"], "project_id": ids["project"],
        "title": "Synthetic milestone", "status": "open", "version": 1})
    for key, project in (("update", "project"), ("other_update", "other_project")):
        connection.execute(tables["network_project_updates"].insert(), {"id": ids[key], "project_id": ids[project],
            "author_id": ids["actor"], "submission_id": uuid4(), "kind": "progress", "body": "Synthetic retained progress",
            "occurred_on": date(2026, 1, 1), "media_ids": []})
    connection.execute(tables["network_project_comments"].insert(), {"id": ids["project_comment"], "update_id": ids["update"],
        "author_id": ids["actor"], "submission_id": uuid4(), "body": "Synthetic retained comment"})
    connection.execute(tables["social_network_profiles"].insert(), {"user_id": ids["actor"], "display_name": "Synthetic member",
        "bio": "", "status_text": "", "visibility": "private", "wall_policy": "own_only", "comments_enabled": True, "version": 1})
    for key, author in (("post", "actor"), ("other_post", "other")):
        connection.execute(tables["network_posts"].insert(), {"id": ids[key], "profile_user_id": ids["actor"],
            "author_id": ids[author], "submission_id": uuid4(), "body": "Synthetic post", "media_ids": [], "status": "pending"})
    connection.execute(tables["network_post_comments"].insert(), {"id": ids["post_comment"], "post_id": ids["post"],
        "author_id": ids["actor"], "submission_id": uuid4(), "body": "Synthetic wall comment", "hidden": False})
    connection.execute(tables["network_blocks"].insert(), {"blocker_id": ids["actor"], "blocked_id": ids["other"]})
    for key, project in (("media", ids["project"]), ("wall_media", None)):
        connection.execute(tables["network_media"].insert(), {"id": ids[key], "uploader_id": ids["actor"], "project_id": project,
            "object_key": "synthetic/" + str(ids[key]) + ".webp", "sha256": "0" * 64, "byte_count": 10, "width": 2, "height": 2})
    connection.execute(tables["network_audit"].insert(), {"id": ids["audit"], "actor_id": ids["actor"],
        "target_kind": "project", "target_id": ids["project"], "action": "synthetic", "data": {}})
    return ids


def seed_registry(connection, metadata, ids):
    """Two new synthetic identities after the old50 retention path is proven."""
    ids["registry"], ids["registry_project"] = uuid4(), uuid4()
    connection.execute(metadata.tables["network_properties"].insert(), {
        "id": ids["registry"], "custodian_id": ids["actor"], "submission_id": uuid4(),
        "title": "Synthetic unlisted property", "location": "Synthetic private district", "address": "",
        "property_class": "house_villa", "relationship": "manager", "notes": "", "status": "active", "version": 1,
        "creation_sha256": "2" * 64, "authority_acknowledged": True})
    connection.execute(metadata.tables["network_projects"].insert(), {
        "id": ids["registry_project"], "listing_id": None, "property_id": ids["registry"], "owner_id": ids["actor"],
        "created_by_id": ids["other"], "submission_id": uuid4(), "creation_sha256": "3" * 64,
        "title": "Synthetic private project", "summary": "", "property_class": "house_villa", "service_category_ids": [],
        "status": "open", "version": 1})
    connection.execute(metadata.tables["network_audit"].insert(), {"id": uuid4(), "actor_id": ids["actor"],
        "target_kind": "registry_property", "target_id": ids["registry"], "action": "synthetic", "data": {}})


def seed_services(connection, metadata, ids):
    """Real linked synthetic rows, after independent populated-f3 retention."""
    from datetime import date
    from decimal import Decimal
    tables = metadata.tables
    for key in ("service_property_b", "service_project_w", "service_request_a", "service_request_b", "service_request_w",
                "service_a1", "service_a2", "service_a3", "service_b1", "service_w1", "service_w2",
                "service_qa1", "service_qa2", "service_qa3", "service_qb1", "service_qw1", "service_qw2", "service_action"):
        ids[key] = uuid4()
    codes = {}
    for key in ("service_p1", "service_p2", "service_p3"):
        codes[key] = connection.execute(tables["users"].select().where(tables["users"].c.id == ids[key])).mappings().one()["user_code"]
        connection.execute(tables["network_service_preferences"].insert(), {"user_id": ids[key], "service_opt_in": True,
            "member_alias": "Synthetic opted-in member", "category_ids": ["pool_service"],
            "property_class_ids": ["house_villa"], "version": 1})
    connection.execute(tables["network_properties"].insert(), {"id": ids["service_property_b"], "custodian_id": ids["actor"],
        "submission_id": uuid4(), "creation_sha256": "4" * 64, "authority_acknowledged": True,
        "title": "Synthetic second private property", "location": "Synthetic district", "address": "", "notes": "",
        "property_class": "house_villa", "relationship": "owner", "status": "active", "version": 1})
    connection.execute(tables["network_projects"].insert(), {"id": ids["service_project_w"], "listing_id": None,
        "property_id": ids["registry"], "owner_id": ids["actor"], "created_by_id": ids["actor"],
        "submission_id": uuid4(), "creation_sha256": "5" * 64, "title": "Synthetic awarded service project",
        "summary": "Synthetic agreed scope", "property_class": "house_villa", "service_category_ids": ["pool_service"], "status": "open", "version": 1})
    request_model, recipient_model, proposal_model = (tables[name] for name in
        ("network_service_requests", "network_service_recipients", "network_service_proposals"))
    for key, property_key, member_keys in (("service_request_a", "registry", ("service_p1", "service_p2", "service_p3")),
            ("service_request_b", "service_property_b", ("service_p1",)),
            ("service_request_w", "registry", ("service_p1", "service_p2"))):
        connection.execute(request_model.insert(), {"id": ids[key], "property_id": ids[property_key], "listing_id": None,
            "custodian_id": ids["actor"], "requester_id": ids["actor"], "category_id": "pool_service", "property_class": "house_villa",
            "title": "Synthetic selected service request", "brief": "Deliberately shared synthetic work", "area_label": "Synthetic area",
            "requester_alias": "Synthetic custodian", "recipient_user_codes": sorted(codes[m] for m in member_keys),
            "scope_sha256": "6" * 64, "status": "open", "version": 1})
    for recipient, request, member, proposal in (
        ("service_a1", "service_request_a", "service_p1", "service_qa1"),
        ("service_a2", "service_request_a", "service_p2", "service_qa2"),
        ("service_a3", "service_request_a", "service_p3", "service_qa3"),
        ("service_b1", "service_request_b", "service_p1", "service_qb1"),
        ("service_w1", "service_request_w", "service_p1", "service_qw1"),
        ("service_w2", "service_request_w", "service_p2", "service_qw2")):
        connection.execute(recipient_model.insert(), {"id": ids[recipient], "request_id": ids[request], "provider_id": ids[member],
            "user_code": codes[member], "member_alias": "Synthetic selected member", "status": "pending", "version": 1})
        connection.execute(recipient_model.update().where(recipient_model.c.id == ids[recipient]).values(status="accepted", version=2))
        connection.execute(proposal_model.insert(), {"id": ids[proposal], "request_id": ids[request], "recipient_id": ids[recipient],
            "author_id": ids[member], "parent_proposal_id": None, "revision_no": 1, "kind": "provider_quote", "status": "submitted", "version": 1,
            "amount": Decimal("123.45"), "currency": "USD", "pricing_basis": "fixed", "scope_text": "Synthetic immutable proposed work",
            "exclusions": "", "estimated_days": 3, "valid_until": date(2026, 12, 31)})
        if recipient != "service_a3":
            connection.execute(recipient_model.update().where(recipient_model.c.id == ids[recipient]).values(current_proposal_id=ids[proposal], version=3))
    # A3 intentionally retains current=NULL with a real first proposal, so S17
    # reaches revision uniqueness instead of rejecting an unrelated parent.
    connection.execute(proposal_model.update().where(proposal_model.c.id == ids["service_qw1"]).values(status="awarded", version=2))
    connection.execute(recipient_model.update().where(recipient_model.c.id == ids["service_w1"]).values(status="awarded", version=4))
    connection.execute(request_model.update().where(request_model.c.id == ids["service_request_w"]).values(
        status="awarded", project_id=ids["service_project_w"], version=2))
    connection.execute(tables["network_service_actions"].insert(), {"id": ids["service_action"], "actor_id": ids["actor"],
        "submission_id": uuid4(), "operation": "request_create", "target_id": ids["service_request_a"], "request_id": ids["service_request_a"],
        "creation_sha256": "7" * 64, "result_versions": {"request_version": 1}})


def exercise_constraints(engine, metadata, ids, text):
    from sqlalchemy.exc import DBAPIError
    count = 0

    def reject(sql, params, constraint=None, state=None):
        nonlocal count
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                try:
                    connection.execute(text(sql) if isinstance(sql, str) else sql, params)
                except DBAPIError as failure:
                    if constraint:
                        need(getattr(getattr(failure.orig, "diag", None), "constraint_name", None) == constraint, "WRONG_CONSTRAINT_REJECTED")
                    if state:
                        need(getattr(failure.orig, "pgcode", None) == state, "WRONG_SQLSTATE_REJECTED")
                    count += 1
                else:
                    raise RuntimeError("INVALID_DDL_OPERATION_ACCEPTED")
            finally:
                transaction.rollback()

    def update(table, column, value, key, constraint):
        reject('UPDATE "' + table + '" SET "' + column + '"=:value WHERE ' + ("listing_id" if table.startswith("listing_location") else "id") + '=:id',
               {"value": value, "id": ids[key]}, constraint)

    update("lead_delivery_jobs", "status", "invalid", "job", "ck_lead_delivery_status")
    for value in (-1, 5):
        update("lead_delivery_jobs", "attempt_count", value, "job", "ck_lead_delivery_attempts")
    reject("UPDATE lead_delivery_worker_state SET id=2 WHERE id=1", {}, "ck_lead_delivery_worker_singleton")
    for column, value, constraint in (("version", 0, "ck_location_review_version"), ("review_status", "invalid", "ck_location_review_status"),
        ("public_precision", "invalid", "ck_location_public_precision"), ("latitude", -91, "ck_location_review_coordinates"),
        ("latitude", 91, "ck_location_review_coordinates"), ("longitude", -181, "ck_location_review_coordinates"),
        ("longitude", 181, "ck_location_review_coordinates"), ("latitude", None, "ck_location_review_coordinates"),
        ("longitude", None, "ck_location_review_coordinates")):
        update("listing_location_reviews", column, value, "listing", constraint)
    update("listing_location_review_revisions", "version", 0, "listing", "ck_location_revision_version")
    update("property_management_records", "version", 0, "task", "ck_management_record_version")
    update("property_management_records", "kind", "invalid", "task", "ck_management_record_kind")
    for table, column, value, key, constraint in (
        ("network_projects", "version", 0, "project", "ck_network_project_version"),
        ("network_projects", "status", "invalid", "project", "ck_network_project_status"),
        ("network_project_members", "role", "owner", "member", "ck_network_member_role"),
        ("network_milestones", "version", 0, "milestone", "ck_network_milestone_version"),
        ("network_milestones", "status", "invalid", "milestone", "ck_network_milestone_status"),
        ("network_posts", "status", "invalid", "post", "ck_network_post_status")):
        update(table, column, value, key, constraint)
    for column, value, constraint in (("visibility", "public", "ck_network_profile_visibility"),
        ("wall_policy", "anyone", "ck_network_wall_policy"), ("version", 0, "ck_network_profile_version")):
        reject('UPDATE social_network_profiles SET "' + column + '"=:value WHERE user_id=:id', {"value": value, "id": ids["actor"]}, constraint)
    for column, value, constraint in (("byte_count", 0, "ck_network_media_size"), ("byte_count", 5242881, "ck_network_media_size"),
        ("width", 0, "ck_network_media_dimensions"), ("height", 0, "ck_network_media_dimensions")):
        # Immutable media UPDATE would hit the trigger first. Test the actual
        # CHECK through a new INSERT, without disabling or rewriting that guard.
        parameters = {"id": uuid4(), "actor": ids["actor"], "key": "synthetic/" + uuid4().hex,
                      "bytes": value if column == "byte_count" else 10,
                      "width": value if column == "width" else 2, "height": value if column == "height" else 2}
        reject("INSERT INTO network_media(id,uploader_id,object_key,sha256,byte_count,width,height) VALUES(:id,:actor,:key,'x',:bytes,:width,:height)", parameters, constraint)
    reject("INSERT INTO network_blocks(blocker_id,blocked_id) VALUES(:id,:id)", {"id": ids["actor"]}, "ck_network_no_self_block")
    reject("INSERT INTO network_media(id,uploader_id,update_id,post_id,object_key,sha256,byte_count,width,height) VALUES(:id,:actor,:update,:post,:key,'x',10,2,2)",
        {"id": uuid4(), "actor": ids["actor"], "update": ids["update"], "post": ids["post"], "key": "synthetic/" + uuid4().hex}, "ck_network_media_one_binding")
    for lead, user in ((None, None), (ids["lead"], ids["actor"])):
        reject("INSERT INTO lead_delivery_jobs(id,lead_id,user_id,step,provider,payload,payload_sha256) VALUES(:id,:lead,:user,'xor','synthetic','{}'::jsonb,'x')",
               {"id": uuid4(), "lead": lead, "user": user}, "ck_delivery_exactly_one_event")
    for field, key, step, constraint in (("lead_id", "lead", "synthetic_retained", "uq_lead_delivery_step"),
                                       ("user_id", "actor", "synthetic_signup", "uq_signup_delivery_step")):
        reject('INSERT INTO lead_delivery_jobs(id,' + field + ",step,provider,payload,payload_sha256) VALUES(:id,:event,:step,'synthetic','{}'::jsonb,'x')",
               {"id": uuid4(), "event": ids[key], "step": step}, constraint)
    reject("INSERT INTO property_management_revisions(id,record_id,version,action,snapshot) VALUES(:id,:record,1,'synthetic','{}'::jsonb)",
           {"id": uuid4(), "record": ids["task"]}, "uq_management_revision_version")
    for table, key, constraint in (
        ("network_project_members", "member", "uq_network_project_member"),
        ("network_project_updates", "update", "uq_network_update_submission"),
        ("network_project_comments", "project_comment", "uq_network_project_comment_submission"),
        ("network_posts", "post", "uq_network_post_submission"),
        ("network_post_comments", "post_comment", "uq_network_post_comment_submission"),
        ("network_media", "media", "network_media_object_key_key")):
        model = metadata.tables[table]
        with engine.connect() as connection:
            duplicate = dict(connection.execute(model.select().where(model.c.id == ids[key])).mappings().one())
        duplicate["id"] = uuid4()
        reject(model.insert(), duplicate, constraint)
    with engine.connect() as connection:
        invalid_update = dict(connection.execute(metadata.tables["network_project_updates"].select().where(metadata.tables["network_project_updates"].c.id == ids["update"])).mappings().one())
    invalid_update.update(id=uuid4(), submission_id=uuid4(), kind="invalid")
    reject(metadata.tables["network_project_updates"].insert(), invalid_update, "ck_network_update_kind")
    for table, constraint in (("listing_location_reviews", "listing_location_reviews_pkey"),
                              ("listing_location_review_revisions", "listing_location_review_revisions_pkey")):
        with engine.connect() as connection:
            duplicate = dict(connection.execute(metadata.tables[table].select()).mappings().one())
        reject(metadata.tables[table].insert(), duplicate, constraint)
    reject("UPDATE network_project_members SET user_id=:missing WHERE id=:id", {"missing": uuid4(), "id": ids["member"]}, state="23503")
    reject("INSERT INTO network_project_comments(id,update_id,author_id,submission_id,body,media_id) VALUES(:id,:update,:actor,:submission,'synthetic',:missing)",
           {"id": uuid4(), "update": ids["update"], "actor": ids["actor"], "submission": uuid4(), "missing": uuid4()}, state="23503")
    # Partial unique index: only a second task with the same non-null origin is
    # forbidden; two different records with a null origin were already valid.
    with engine.begin() as connection:
        connection.execute(metadata.tables["property_management_records"].insert(), {"id": uuid4(), "listing_id": ids["listing"],
            "kind": "task", "title": "Synthetic recurring child", "status": "open", "data": {}, "origin_record_id": ids["task"]})
        for _ in range(2):
            connection.execute(metadata.tables["property_management_records"].insert(), {"id": uuid4(), "listing_id": ids["listing"],
                "kind": "ledger", "title": "Synthetic non-task origin", "status": "open", "data": {}, "origin_record_id": ids["task"]})
    reject("INSERT INTO property_management_records(id,listing_id,kind,title,status,data,origin_record_id) VALUES(:id,:listing,'task','duplicate','open','{}'::jsonb,:origin)",
           {"id": uuid4(), "listing": ids["listing"], "origin": ids["task"]}, "uq_management_recurring_origin")
    reject("DELETE FROM users WHERE id=:id", {"id": ids["actor"]}, state="23503")
    reject("DELETE FROM listings WHERE id=:id", {"id": ids["listing"]}, state="23503")
    for table, key in (("network_project_updates", "update"), ("network_project_comments", "project_comment"), ("network_audit", "audit")):
        for sql in ('UPDATE "' + table + '" SET id=id WHERE id=:id', 'DELETE FROM "' + table + '" WHERE id=:id'):
            reject(sql, {"id": ids[key]}, state="23000")
    for table, key in (("network_posts", "post"), ("network_post_comments", "post_comment"), ("network_media", "media")):
        reject('DELETE FROM "' + table + '" WHERE id=:id', {"id": ids[key]}, state="23000")
    reject("UPDATE network_posts SET body='changed' WHERE id=:id", {"id": ids["post"]}, state="23000")
    reject("UPDATE network_post_comments SET body='changed' WHERE id=:id", {"id": ids["post_comment"]}, state="23000")
    reject("UPDATE network_media SET object_key='changed' WHERE id=:id", {"id": ids["media"]}, state="23000")
    reject("UPDATE network_media SET update_id=:update WHERE id=:id", {"id": ids["media"], "update": ids["other_update"]}, state="23000")
    reject("UPDATE network_media SET post_id=:post WHERE id=:id", {"id": ids["wall_media"], "post": ids["other_post"]}, state="23000")
    with engine.begin() as connection:
        connection.execute(text("UPDATE lead_delivery_jobs SET attempt_count=4 WHERE id=:id"), {"id": ids["job"]})
        connection.execute(text("UPDATE network_posts SET status='visible' WHERE id=:id"), {"id": ids["post"]})
        connection.execute(text("UPDATE network_post_comments SET hidden=true WHERE id=:id"), {"id": ids["post_comment"]})
        connection.execute(text("UPDATE network_media SET update_id=:update WHERE id=:id"), {"id": ids["media"], "update": ids["update"]})
        connection.execute(text("UPDATE network_media SET post_id=:post WHERE id=:id"), {"id": ids["wall_media"], "post": ids["post"]})
        connection.execute(text("UPDATE listing_location_reviews SET latitude=-90,longitude=-180 WHERE listing_id=:id"), {"id": ids["listing"]})
        connection.execute(text("UPDATE listing_location_reviews SET latitude=90,longitude=180 WHERE listing_id=:id"), {"id": ids["listing"]})
        connection.execute(text("UPDATE listing_location_reviews SET latitude=NULL,longitude=NULL WHERE listing_id=:id"), {"id": ids["listing"]})
    reject("UPDATE network_media SET update_id=NULL WHERE id=:id", {"id": ids["media"]}, state="23000")
    reject("UPDATE network_media SET post_id=NULL WHERE id=:id", {"id": ids["wall_media"]}, state="23000")
    need(count == 66, "ORIGINAL66_NEGATIVE_CASES_REQUIRED")
    # The original66 operations above are retained verbatim. These additional
    # fixed35 cases exercise actual registry CHECK/FK/unique/identity guards.
    property_model, project_model = metadata.tables["network_properties"], metadata.tables["network_projects"]
    with engine.connect() as connection:
        registry = dict(connection.execute(property_model.select().where(property_model.c.id == ids["registry"])).mappings().one())
        project = dict(connection.execute(project_model.select().where(project_model.c.id == ids["registry_project"])).mappings().one())
    for column, value, constraint in (
        ("status", "public", "ck_network_property_status"),
        ("relationship", "viewer", "ck_network_property_relationship"),
        ("property_class", "invented", "ck_network_property_class"),
        ("version", 0, "ck_network_property_version"),
        ("title", "", "ck_network_property_bounds"), ("location", "", "ck_network_property_bounds"),
        ("address", "x" * 501, "ck_network_property_bounds"), ("notes", "x" * 4001, "ck_network_property_bounds"),
        ("creation_sha256", "invalid", "ck_network_property_creation_hash"),
        ("authority_acknowledged", False, "ck_network_property_authority")):
        reject(property_model.insert(), {**registry, "id": uuid4(), "submission_id": uuid4(), column: value}, constraint)
    reject(property_model.insert(), {**registry, "id": uuid4()}, "uq_network_property_submission")
    reject(property_model.insert(), {**registry, "id": uuid4(), "submission_id": uuid4(), "custodian_id": uuid4()}, state="23503")
    for column, value in (("id", uuid4()), ("custodian_id", ids["other"]), ("submission_id", uuid4()),
                          ("creation_sha256", "f" * 64), ("authority_acknowledged", False),
                          ("created_at", datetime(2000, 1, 1, tzinfo=timezone.utc))):
        reject('UPDATE network_properties SET "' + column + '"=:value WHERE id=:id', {"value": value, "id": ids["registry"]}, state="23000")
    reject("DELETE FROM network_properties WHERE id=:id", {"id": ids["registry"]}, state="23000")
    for changes, constraint, state in (
        ({"listing_id": ids["listing"]}, "ck_network_project_one_property", None),
        ({"property_id": None}, "ck_network_project_one_property", None),
        ({"property_id": uuid4()}, None, "23503"),
        ({"created_by_id": None, "submission_id": None, "creation_sha256": None}, "ck_network_registry_project_submission", None),
        ({"created_by_id": None}, "ck_network_project_submission", None),
        ({"creation_sha256": "invalid"}, "ck_network_project_submission", None)):
        reject(project_model.insert(), {**project, "id": uuid4(), "submission_id": uuid4(), **changes}, constraint, state)
    reject(project_model.insert(), {**project, "id": uuid4()}, "uq_network_project_submission")
    reject(project_model.insert(), {**project, "id": uuid4(), "submission_id": uuid4(), "created_by_id": uuid4()}, state="23503")
    for column, value in (("id", uuid4()), ("owner_id", ids["other"]), ("property_id", uuid4()),
                          ("listing_id", ids["listing"]), ("created_by_id", ids["actor"]),
                          ("submission_id", uuid4()), ("creation_sha256", "f" * 64)):
        reject('UPDATE network_projects SET "' + column + '"=:value WHERE id=:id', {"value": value, "id": ids["registry_project"]}, state="23000")
    reject("DELETE FROM network_projects WHERE id=:id", {"id": ids["registry_project"]}, state="23000")
    need(count == 101, "EXACT66_PLUS35_NEGATIVE_CASES_REQUIRED")
    # Prove RLS independently of ACL: transaction-only SELECT grants let both
    # non-owner/no-bypass fixture roles query, but every populated table yields0.
    for role in ALLOWED_ROLE:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql("GRANT SELECT ON " + ",".join('"' + table + '"' for table in sorted(NEW)) + ' TO "' + role + '"')
                connection.exec_driver_sql('SET LOCAL ROLE "' + role + '"')
                need(connection.execute(text("SELECT current_user")).scalar_one() == role, "RLS_PROBE_ROLE_MISMATCH")
                for table in sorted(NEW):
                    need(connection.exec_driver_sql('SELECT count(*) FROM "' + table + '"').scalar_one() == 0, "RLS_PROBE_LEAKED_SYNTHETIC_ROW")
            finally:
                transaction.rollback()
    return count


def exercise_service_constraints(engine, metadata, ids, text):
    """Fixed S01..S47: real foreign-scope rows, exact states, rollback each."""
    from sqlalchemy.exc import DBAPIError
    tables, count = metadata.tables, 0

    def reject(sql, params, constraint=None, state=None):
        nonlocal count
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                try:
                    connection.execute(text(sql) if isinstance(sql, str) else sql, params)
                except DBAPIError as failure:
                    if constraint:
                        need(getattr(getattr(failure.orig, "diag", None), "constraint_name", None) == constraint, "WRONG_SERVICE_CONSTRAINT_REJECTED")
                    need(getattr(failure.orig, "pgcode", None) == state, "WRONG_SERVICE_SQLSTATE_REJECTED")
                    count += 1
                else:
                    raise RuntimeError("INVALID_SERVICE_OPERATION_ACCEPTED")
            finally:
                transaction.rollback()

    def fresh(table, row, **changes):
        value = dict(row)
        if "id" in value:
            value["id"] = uuid4()
        if "submission_id" in value:
            value["submission_id"] = uuid4()
        value.update(changes)
        return tables[table].insert(), value

    rows = {}
    with engine.connect() as connection:
        for key, table in (
            ("service_request_a", "network_service_requests"), ("service_request_b", "network_service_requests"),
            ("service_request_w", "network_service_requests"), ("service_a1", "network_service_recipients"),
            ("service_a2", "network_service_recipients"), ("service_a3", "network_service_recipients"),
            ("service_b1", "network_service_recipients"), ("service_w1", "network_service_recipients"),
            ("service_w2", "network_service_recipients"), ("service_qa1", "network_service_proposals"),
            ("service_qa2", "network_service_proposals"), ("service_qa3", "network_service_proposals"),
            ("service_qb1", "network_service_proposals"), ("service_qw1", "network_service_proposals"),
            ("service_action", "network_service_actions")):
            rows[key] = dict(connection.execute(tables[table].select().where(tables[table].c.id == ids[key])).mappings().one())
        preference = dict(connection.execute(tables["network_service_preferences"].select().where(
            tables["network_service_preferences"].c.user_id == ids["service_p1"])).mappings().one())
        for key in ("service_p1", "service_p2", "service_p3", "service_outsider", "service_unverified", "service_unused"):
            member = connection.execute(tables["users"].select().where(tables["users"].c.id == ids[key])).mappings().one()
            need(member["status"] == "active" and member["user_code"] > 0 and member["role"] == "buyer"
                 and member["email_verified"] is (key != "service_unverified"), "SERVICE_ACTOR_FIXTURE_REQUIRED")
        need(connection.execute(tables["network_properties"].select().where(
            tables["network_properties"].c.id == ids["service_property_b"])).mappings().one()["id"] != ids["registry"], "DISTINCT_REAL_SERVICE_SOURCES_REQUIRED")
    a, b, w = (rows["service_request_" + key] for key in ("a", "b", "w"))
    a1, a2, a3, b1, w1, w2 = (rows["service_" + key] for key in ("a1", "a2", "a3", "b1", "w1", "w2"))
    qa1, qa2, qa3, qb1, qw1 = (rows["service_" + key] for key in ("qa1", "qa2", "qa3", "qb1", "qw1"))
    need(a["property_id"] != b["property_id"] and a1["request_id"] == a["id"] and b1["request_id"] == b["id"]
         and qa1["recipient_id"] == a1["id"] and qa2["recipient_id"] == a2["id"]
         and qb1["recipient_id"] == b1["id"] and qb1["request_id"] == b["id"]
         and a1["current_proposal_id"] == qa1["id"] and b1["current_proposal_id"] == qb1["id"]
         and a3["current_proposal_id"] is None and qa3["recipient_id"] == a3["id"] and qa3["revision_no"] == 1,
         "EXISTING_FOREIGN_SCOPE_AND_NULL_CURRENT_FIXTURES_REQUIRED")
    need(w["status"] == w1["status"] == qw1["status"] == "awarded" and w["project_id"] == ids["service_project_w"]
         and w2["status"] == "accepted", "REAL_TERMINAL_WINNER_FIXTURE_REQUIRED")
    pref = {**preference, "user_id": ids["service_unused"]}
    recipient = {**a2, "request_id": b["id"], "current_proposal_id": None, "status": "pending", "version": 1}
    action = rows["service_action"]
    # S01..S15: every named CHECK via INSERT, so immutable UPDATE guards do not mask it.
    for table, row, changes, constraint in (
        ("network_service_preferences", pref, {"version": 0}, "ck_service_preference_version"),
        ("network_service_preferences", pref, {"service_opt_in": False, "member_alias": "x" * 121}, "ck_service_preference_bounds"),
        ("network_service_preferences", pref, {"member_alias": ""}, "ck_service_preference_consent"),
        ("network_service_requests", a, {"listing_id": ids["listing"]}, "ck_service_request_source"),
        ("network_service_requests", a, {"version": 0}, "ck_service_request_state"),
        ("network_service_requests", a, {"status": "awarded"}, "ck_service_request_project"),
        ("network_service_requests", a, {"title": ""}, "ck_service_request_bounds"),
        ("network_service_requests", a, {"recipient_user_codes": []}, "ck_service_request_recipients"),
        ("network_service_recipients", recipient, {"user_code": 0}, "ck_service_recipient_bounds"),
        ("network_service_recipients", recipient, {"status": "invalid"}, "ck_service_recipient_state"),
        ("network_service_proposals", qa3, {"kind": "invalid"}, "ck_service_proposal_state"),
        ("network_service_proposals", qa3, {"amount": 0}, "ck_service_proposal_price"),
        ("network_service_proposals", qa3, {"scope_text": ""}, "ck_service_proposal_bounds"),
        ("network_service_actions", action, {"creation_sha256": "z" * 64}, "ck_service_action_hash"),
        ("network_service_actions", action, {"operation": "invalid"}, "ck_service_action_operation")):
        sql, values = fresh(table, row, **changes)
        reject(sql, values, constraint, "23514")
    # S16..S20: real UNIQUE keys and actual terminal winner, never nonexistent FKs.
    reject(*fresh("network_service_recipients", a1, current_proposal_id=None), "uq_service_recipient_member", "23505")
    reject(*fresh("network_service_proposals", qa3), "uq_service_proposal_revision", "23505")
    reject(*fresh("network_service_actions", action, submission_id=action["submission_id"]), "uq_service_action_submission", "23505")
    reject(*fresh("network_service_requests", w), "network_service_requests_project_id_key", "23505")
    reject("UPDATE network_service_recipients SET status='awarded',version=version+1 WHERE id=:id", {"id": w2["id"]}, "uq_service_recipient_winner", "23505")
    # S21..S24: catalogs, duplicate context/selection and real unverified member.
    reject(*fresh("network_service_preferences", pref, service_opt_in=False, category_ids=["unknown_service"]), state="23514")
    reject(*fresh("network_service_preferences", pref, service_opt_in=False, property_class_ids=["house_villa", "house_villa"]), state="23514")
    reject(*fresh("network_service_requests", a, recipient_user_codes=[a1["user_code"], a1["user_code"]]), state="23514")
    reject(*fresh("network_service_preferences", pref, user_id=ids["service_unverified"]), state="23514")
    # S25..S34: cross-scope UUIDs were SELECT-verified above; each requires23514, not23503.
    reject("UPDATE network_service_recipients SET current_proposal_id=:foreign,version=version+1 WHERE id=:id", {"foreign": qb1["id"], "id": a1["id"]}, state="23514")
    next_proposal = {**qa1, "parent_proposal_id": qa1["id"], "revision_no": qa1["revision_no"] + 1}
    reject(*fresh("network_service_proposals", next_proposal, recipient_id=b1["id"], parent_proposal_id=qb1["id"]), state="23514")
    reject(*fresh("network_service_proposals", next_proposal, parent_proposal_id=qb1["id"]), state="23514")
    reject(*fresh("network_service_proposals", next_proposal, revision_no=qa1["revision_no"] + 2), state="23514")
    reject(*fresh("network_service_proposals", next_proposal, author_id=ids["service_outsider"]), state="23514")
    reject(*fresh("network_service_proposals", next_proposal, kind="owner_counter", author_id=ids["service_outsider"]), state="23514")
    reject(*fresh("network_service_actions", action, operation="preference_save", target_id=ids["service_unused"], request_id=None,
                  recipient_id=None, proposal_id=None), state="23514")
    reject(*fresh("network_service_actions", action, request_id=None), state="23514")
    reject(*fresh("network_service_actions", action, recipient_id=b1["id"]), state="23514")
    reject(*fresh("network_service_actions", action, recipient_id=a1["id"], proposal_id=qa2["id"]), state="23514")
    # S35..S47: immutable identity/terms, exact missing version, deletes and true terminal state.
    for sql, params in (
        ("UPDATE network_service_preferences SET user_id=:other,version=version+1 WHERE user_id=:id", {"other": ids["service_unused"], "id": ids["service_p1"]}),
        ("UPDATE network_service_requests SET brief='Different valid scope',version=version+1 WHERE id=:id", {"id": a["id"]}),
        ("UPDATE network_service_recipients SET status='declined' WHERE id=:id", {"id": a1["id"]}),
        ("UPDATE network_service_proposals SET amount=200,version=version+1 WHERE id=:id", {"id": qa1["id"]}),
        ("UPDATE network_service_actions SET result_versions='{}'::jsonb WHERE id=:id", {"id": action["id"]}),
        ("DELETE FROM network_service_preferences WHERE user_id=:id", {"id": ids["service_p1"]}),
        ("DELETE FROM network_service_requests WHERE id=:id", {"id": a["id"]}),
        ("DELETE FROM network_service_recipients WHERE id=:id", {"id": a1["id"]}),
        ("DELETE FROM network_service_proposals WHERE id=:id", {"id": qa1["id"]}),
        ("DELETE FROM network_service_actions WHERE id=:id", {"id": action["id"]}),
        ("UPDATE network_service_requests SET updated_at=now(),version=version+1 WHERE id=:id", {"id": w["id"]}),
        ("UPDATE network_service_recipients SET version=version+1 WHERE id=:id", {"id": w1["id"]}),
        ("UPDATE network_service_proposals SET version=version+1 WHERE id=:id", {"id": qw1["id"]})):
        reject(sql, params, state="23000")
    need(count == 47, "EXACT47_SERVICE_NEGATIVE_CASES_REQUIRED")
    # All23 are genuinely populated before the service RLS proof; temporary
    # SELECT grants distinguish RLS denial from absence of direct privileges.
    with engine.connect() as connection:
        need(all(row["rows"] > 0 for row in fingerprint(connection, NEW, text).values()), "FULL23_RLS_FIXTURE_REQUIRED")
    for role in ALLOWED_ROLE:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql("GRANT SELECT ON " + ",".join('"' + table + '"' for table in sorted(NEW)) + ' TO "' + role + '"')
                connection.exec_driver_sql('SET LOCAL ROLE "' + role + '"')
                need(connection.execute(text("SELECT current_user")).scalar_one() == role, "SERVICE_RLS_PROBE_ROLE_MISMATCH")
                for table in sorted(NEW):
                    need(connection.exec_driver_sql('SELECT count(*) FROM "' + table + '"').scalar_one() == 0, "SERVICE_RLS_PROBE_LEAKED_ROW")
            finally:
                transaction.rollback()
    return count


def child(args):
    need(sys.platform == "linux" and sys.version_info[:2] == (3, 11), "LINUX_PYTHON311_REQUIRED")
    need(DB_NAME.fullmatch(args.database) and args.proof.parent == Path("/results"), "OWNED_CHILD_TARGET_REQUIRED")
    need(Path.cwd() != args.source and not (Path.cwd() / ".env").exists()
         and os.environ.get("DATABASE_URL") == url_for(args.database), "SANITIZED_CHILD_CONTEXT_REQUIRED")
    socket_guard()
    sys.path.insert(0, str(args.source))
    from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, inspect, text
    from sqlalchemy.pool import NullPool
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    # These are the ACTUAL config/database/model/bootstrap modules of the
    # selected source. No config shim, metadata exclusion or stamp is used.
    from database import Base, engine as imported_engine
    import models  # noqa: F401
    import bootstrap_db
    engine = create_engine(url_for(args.database), poolclass=NullPool)
    proof = {"status": "FAILED", "database": args.database, "mode": args.mode,
             "provider_or_application_lifespan_started": False}
    config = Config(str(args.source / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)

    def version(expected):
        with engine.connect() as connection:
            need(connection.execute(text("SELECT version_num FROM public.alembic_version")).scalars().all() == [expected], "ACTUAL_ALEMBIC_VERSION_MISMATCH")

    try:
        proof["source_graph_revision_count"] = len(list(scripts.walk_revisions()))
        need(proof["source_graph_revision_count"] == (45 if args.mode == "baseline" else 52), "EXACT_CURRENT_OR_BASELINE_GRAPH_REQUIRED")
        with engine.connect() as connection:
            facts = db_facts(connection, text)
            proof["database_oid"] = facts["oid"]
            nonce = args.database.rsplit("_", 1)[1]
            kind = args.database[len(PREFIX)]
            need(facts["marker"] == "ildr-owned-migration:" + nonce + ":" + kind, "ACTUAL_CHILD_DATABASE_MARKER_REQUIRED")
            need(connection.execute(text("SELECT current_schema(),current_user")).one() == ("public", "postgres"), "REAL_PUBLIC_SCHEMA_REQUIRED")
        if args.mode in ("fresh", "baseline"):
            with engine.connect() as connection:
                need(inspect(connection).get_table_names(schema="public") == [], "GENUINELY_EMPTY_DATABASE_REQUIRED")
            expected_tables = LEGACY if args.mode == "baseline" else LEGACY | NEW
            expected_head = OLD if args.mode == "baseline" else HEAD
            need(set(Base.metadata.tables) == expected_tables and scripts.get_heads() == [expected_head], "SELECTED_SOURCE_GRAPH_MISMATCH")
            need(len(list(scripts.walk_revisions())) == (45 if args.mode == "baseline" else 52), "EXACT45_BASELINE_OR52_CURRENT_GRAPH_REQUIRED")
            bootstrap_db.main()
            with engine.connect() as connection:
                need(set(inspect(connection).get_table_names(schema="public")) == expected_tables, "REAL_BOOTSTRAP_TABLE_SET_MISMATCH")
                need(not inspect(connection).has_table("alembic_version", schema="public"), "BOOTSTRAP_MUST_NOT_STAMP")
            command.upgrade(config, "head")
            version(expected_head)
            with engine.begin() as connection:
                # Sequence/default produced by real old migrations, not only
                # SQLAlchemy's client-side Sequence object.
                default = connection.execute(text("SELECT column_default FROM information_schema.columns WHERE table_schema='public' AND table_name='users' AND column_name='user_code'")).scalar_one()
                need(default is not None and "users_user_code_seq" in default, "REAL_USER_CODE_DEFAULT_MISSING")
                need(connection.execute(text("SELECT pg_get_serial_sequence('public.users','user_code')")).scalar_one() == "public.users_user_code_seq", "REAL_USER_CODE_SEQUENCE_OWNERSHIP_MISSING")
                if args.mode == "baseline":
                    ids = seed_service_actors(connection, Base.metadata, seed_baseline(connection, Base.metadata))
                    strict_profiles(connection, text)
                    proof["baseline_ids"] = ids  # Synthetic UUIDs only; no row fields.
                    proof["legacy_rows"] = fingerprint(connection, LEGACY, text)
                    proof["legacy_schema_sha256"] = schema_facts(connection, LEGACY, inspect)
                else:
                    proof["catalog_sha256"] = catalog(connection, Base.metadata, inspect, text, CheckConstraint, UniqueConstraint)
                    need(all(v["rows"] == 0 for v in fingerprint(connection, NEW, text).values()), "FRESH_EXPANSION_BACKFILL_DETECTED")
                    diagnostic = {}
                    proof["new_schema_sha256"] = schema_facts(connection, NEW, inspect, diagnostic=diagnostic)
                    proof["new_schema_diagnostic"] = diagnostic
            before = None
            with engine.connect() as connection:
                before = fingerprint(connection, expected_tables, text)
            bootstrap_db.main()
            command.upgrade(config, "head")
            version(expected_head)
            with engine.connect() as connection:
                need(fingerprint(connection, expected_tables, text) == before, "REPEATED_STARTUP_CHANGED_ROWS")
                if args.mode == "baseline":
                    need(schema_facts(connection, LEGACY, inspect) == proof["legacy_schema_sha256"], "BASELINE_STARTUP_CHANGED_SCHEMA")
                else:
                    need(schema_facts(connection, NEW, inspect) == proof["new_schema_sha256"]
                         and catalog(connection, Base.metadata, inspect, text, CheckConstraint, UniqueConstraint) == proof["catalog_sha256"],
                         "FRESH_STARTUP_CHANGED_FULL23_SCHEMA")
        else:
            need(args.mode == "upgrade" and args.previous is not None, "UPGRADE_PREDECESSOR_REQUIRED")
            raw = args.previous.read_bytes()
            need(len(raw) <= MAX_PROOF and sha(raw) == args.previous_sha256, "BASELINE_PROOF_CHANGED")
            previous = json.loads(raw)
            need(previous["status"] == "ACTUAL_PHASE_COMPLETED" and previous["mode"] == "baseline"
                 and previous["database"] == args.database and previous["database_oid"] == proof["database_oid"], "BASELINE_PROOF_SCOPE_MISMATCH")
            need(set(Base.metadata.tables) == LEGACY | NEW and scripts.get_heads() == [HEAD]
                 and len(list(scripts.walk_revisions())) == 52
                 and [r.revision for r in scripts.iterate_revisions(HEAD, OLD)] == CHAIN, "EXACT_SEVEN_REVISION_CHAIN_REQUIRED")
            version(OLD)
            with engine.connect() as connection:
                need(set(inspect(connection).get_table_names(schema="public")) == LEGACY | {"alembic_version"}, "BASELINE_TABLES_PRECREATED_OR_MISSING")
                need(fingerprint(connection, LEGACY, text) == previous["legacy_rows"]
                     and schema_facts(connection, LEGACY, inspect) == previous["legacy_schema_sha256"], "BASELINE_CONTENT_CHANGED")
            bootstrap_db.main()
            version(OLD)
            with engine.begin() as connection:
                need(fingerprint(connection, LEGACY, text) == previous["legacy_rows"], "BASELINE_BOOTSTRAP_CHANGED_ROWS")
                connection.exec_driver_sql("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO PUBLIC")
                for role in ALLOWED_ROLE:
                    connection.exec_driver_sql('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO "' + role + '"')
                # Existing alembic_version makes actual bootstrap return;
                # NEW tables must be created only by the genuine chain below.
                need(not (NEW & set(inspect(connection).get_table_names(schema="public"))), "NEW_TABLES_PRECREATED_BEFORE_UPGRADE")
                default_grants = {(int(row.grantee), row.privilege_type) for row in connection.execute(text(
                    "SELECT a.grantee,a.privilege_type FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a WHERE d.defaclrole=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND d.defaclnamespace='public'::regnamespace AND d.defaclobjtype='r'"))}
                need(all((0, privilege) in default_grants for privilege in PG17_TABLE_PRIVILEGES), "NONVACUOUS_PUBLIC_DEFAULT_ACL_FIXTURE_REQUIRED")
                for role in ALLOWED_ROLE:
                    role_oid = connection.execute(text("SELECT oid FROM pg_roles WHERE rolname=:role"), {"role": role}).scalar_one()
                    need(all((role_oid, privilege) in default_grants for privilege in PG17_TABLE_PRIVILEGES), "NONVACUOUS_DATA_API_DEFAULT_ACL_FIXTURE_REQUIRED")
                proof["default_acl_seeded_before_new_tables"] = True
            command.upgrade(config, "head")
            version(HEAD)
            with engine.begin() as connection:
                proof["catalog_sha256"] = catalog(connection, Base.metadata, inspect, text, CheckConstraint, UniqueConstraint)
                diagnostic = {}
                proof["new_schema_sha256"] = schema_facts(connection, NEW, inspect, diagnostic=diagnostic)
                proof["new_schema_diagnostic"] = diagnostic
                need(fingerprint(connection, LEGACY, text) == previous["legacy_rows"]
                     and schema_facts(connection, LEGACY, inspect) == previous["legacy_schema_sha256"], "ADDITIVE_UPGRADE_CHANGED_BASELINE")
                strict_profiles(connection, text)
                need(all(v["rows"] == 0 for v in fingerprint(connection, NEW, text).values()), "UPGRADE_ENQUEUED_OR_BACKFILLED_ROWS")
                ids = seed_expansion(connection, Base.metadata, previous["baseline_ids"])
            # Preserve the original full50 forward-only retention path before
            # introducing data that the new f3 downgrade must refuse to remove.
            with engine.connect() as connection:
                need(all(v["rows"] > 0 for v in fingerprint(connection, PRE_REGISTRY, text).values()), "ORIGINAL17_FIXTURE_TABLE_EMPTY")
                privacy(connection, text)
                retained_before_registry = pre_registry_history(connection, text)
                strict_profiles(connection, text)
            command.downgrade(config, OLD)
            version(OLD)
            with engine.connect() as connection:
                need(set(inspect(connection).get_table_names(schema="public")) == LEGACY | PRE_REGISTRY | {"alembic_version"}
                     and fingerprint(connection, PRE_REGISTRY, text) == retained_before_registry, "FORWARD_ONLY_DOWNGRADE_LOST_HISTORY")
                need(fingerprint(connection, LEGACY, text) == previous["legacy_rows"]
                     and schema_facts(connection, LEGACY, inspect) == previous["legacy_schema_sha256"], "ORIGINAL50_DOWNGRADE_CHANGED_BASELINE")
                privacy(connection, text, tables=PRE_REGISTRY)
            command.upgrade(config, "head")
            version(HEAD)
            with engine.connect() as connection:
                need(pre_registry_history(connection, text) == retained_before_registry
                     and fingerprint(connection, LEGACY, text) == previous["legacy_rows"], "REPLAY_CHANGED_HISTORY_OR_BASELINE")
                need(catalog(connection, Base.metadata, inspect, text, CheckConstraint, UniqueConstraint) == proof["catalog_sha256"], "REPLAY_CHANGED_CATALOG")
                need(schema_facts(connection, NEW, inspect) == proof["new_schema_sha256"], "REPLAY_CHANGED_RAW_SCHEMA")
                strict_profiles(connection, text)
            proof["pre_registry_original50_downgrade_reupgrade_preserved"] = True
            with engine.begin() as connection:
                seed_registry(connection, Base.metadata, ids)
            proof["negative_operations_rejected"] = exercise_constraints(engine, Base.metadata, ids, text)
            with engine.connect() as connection:
                retained_registry = fingerprint(connection, REGISTRY, text)
                need(all(value["rows"] > 0 for value in retained_registry.values()), "REGISTRY18_FIXTURE_TABLE_EMPTY")
                need(all(value["rows"] == 0 for value in fingerprint(connection, SERVICE, text).values()), "SERVICE5_MUST_REMAIN_EMPTY_FOR_F3_PROOF")
                registry_raw = schema_facts(connection, REGISTRY, inspect)
                privacy(connection, text)
                strict_profiles(connection, text)
            # Prove the REAL populated-f3 guard independently. Empty a4 is
            # downgraded first, in its own completed command, so its retention
            # guard cannot be mistaken for the older populated-registry refusal.
            command.downgrade(config, REGISTRY_HEAD)
            version(REGISTRY_HEAD)
            with engine.connect() as connection:
                need(fingerprint(connection, REGISTRY, text) == retained_registry
                     and schema_facts(connection, REGISTRY, inspect) == registry_raw
                     and fingerprint(connection, LEGACY, text) == previous["legacy_rows"]
                     and schema_facts(connection, LEGACY, inspect) == previous["legacy_schema_sha256"], "EMPTY_SERVICE_DOWNGRADE_CHANGED_REGISTRY_OR_BASELINE")
                registry_catalog = catalog(connection, Base.metadata, inspect, text, CheckConstraint, UniqueConstraint, registry_only=True)
                strict_profiles(connection, text)
            proof["empty_service_downgrade_to_registry_preserved"] = True
            from sqlalchemy.exc import DBAPIError
            try:
                command.downgrade(config, OLD)
            except DBAPIError as failure:
                need(getattr(failure.orig, "pgcode", None) == "55000", "REGISTRY_RETENTION_WRONG_DOWNGRADE_REFUSAL")
            else:
                raise RuntimeError("POPULATED_REGISTRY_DOWNGRADE_WAS_NOT_REFUSED")
            version(REGISTRY_HEAD)
            with engine.connect() as connection:
                need(fingerprint(connection, REGISTRY, text) == retained_registry
                     and fingerprint(connection, LEGACY, text) == previous["legacy_rows"]
                     and schema_facts(connection, LEGACY, inspect) == previous["legacy_schema_sha256"], "REFUSED_F3_DOWNGRADE_CHANGED_BASELINE")
                need(schema_facts(connection, REGISTRY, inspect) == registry_raw
                      and catalog(connection, Base.metadata, inspect, text, CheckConstraint, UniqueConstraint, registry_only=True) == registry_catalog,
                      "REFUSED_F3_DOWNGRADE_CHANGED_SCHEMA")
                strict_profiles(connection, text)
            command.upgrade(config, "head")
            version(HEAD)
            with engine.begin() as connection:
                need(fingerprint(connection, REGISTRY, text) == retained_registry
                     and all(value["rows"] == 0 for value in fingerprint(connection, SERVICE, text).values()), "A4_REUPGRADE_CHANGED_HISTORY_OR_SEEDED_SERVICES")
                need(schema_facts(connection, NEW, inspect) == proof["new_schema_sha256"]
                     and catalog(connection, Base.metadata, inspect, text, CheckConstraint, UniqueConstraint) == proof["catalog_sha256"], "A4_REUPGRADE_CHANGED_FULL23_SCHEMA")
                seed_services(connection, Base.metadata, ids)
            service_count = exercise_service_constraints(engine, Base.metadata, ids, text)
            proof["negative_operations_rejected"] += service_count
            need(proof["negative_operations_rejected"] == 148, "EXACT101_PLUS47_NEGATIVE_CASES_REQUIRED")
            with engine.connect() as connection:
                retained = fingerprint(connection, NEW, text)
                need(all(value["rows"] > 0 for value in retained.values()), "FULL23_POPULATED_RETENTION_FIXTURE_REQUIRED")
                privacy(connection, text)
                strict_profiles(connection, text)
            try:
                command.downgrade(config, REGISTRY_HEAD)
            except DBAPIError as failure:
                need(getattr(failure.orig, "pgcode", None) == "55000", "SERVICE_RETENTION_WRONG_DOWNGRADE_REFUSAL")
            else:
                raise RuntimeError("POPULATED_SERVICE_DOWNGRADE_WAS_NOT_REFUSED")
            version(HEAD)
            with engine.connect() as connection:
                need(fingerprint(connection, NEW, text) == retained
                     and fingerprint(connection, LEGACY, text) == previous["legacy_rows"]
                     and schema_facts(connection, LEGACY, inspect) == previous["legacy_schema_sha256"], "REFUSED_A4_DOWNGRADE_CHANGED_BASELINE_OR_FULL23_ROWS")
                need(schema_facts(connection, NEW, inspect) == proof["new_schema_sha256"]
                     and catalog(connection, Base.metadata, inspect, text, CheckConstraint, UniqueConstraint) == proof["catalog_sha256"], "REFUSED_A4_DOWNGRADE_CHANGED_FULL23_SCHEMA")
                strict_profiles(connection, text)
            bootstrap_db.main()
            command.upgrade(config, "head")
            version(HEAD)
            with engine.connect() as connection:
                need(fingerprint(connection, NEW, text) == retained
                     and fingerprint(connection, LEGACY, text) == previous["legacy_rows"]
                     and schema_facts(connection, LEGACY, inspect) == previous["legacy_schema_sha256"], "POST_REFUSAL_STARTUP_CHANGED_HISTORY")
                need(schema_facts(connection, NEW, inspect) == proof["new_schema_sha256"]
                     and catalog(connection, Base.metadata, inspect, text, CheckConstraint, UniqueConstraint) == proof["catalog_sha256"], "POST_REFUSAL_STARTUP_CHANGED_FULL23_SCHEMA")
            proof.update(preserved_synthetic_private_profiles=46, new_tables_empty_before_synthetic_inserts=23,
                          effective_data_api_roles_tested=list(ALLOWED_ROLE), rls_populated_tables_tested=23,
                         original_negative_operations_rejected=66, registry_negative_operations_rejected=35,
                          service_negative_operations_rejected=service_count,
                          seeded_default_acl_grant_revocation_tables_tested=23, registry_populated_downgrade_refused=True,
                          registry_populated_downgrade_sqlstate="55000", registry_post_refusal_head=REGISTRY_HEAD,
                          service_populated_downgrade_refused=True, service_populated_downgrade_sqlstate="55000", service_post_refusal_head=HEAD,
                          retention_sequence=["ORIGINAL50_EMPTY_SERVICE_REGISTRY", "EMPTY_A4_TO_F3", "POPULATED_F3_REFUSED",
                              "A4_REUPGRADE_EMPTY_SERVICE", "POPULATED_A4_REFUSED", "HEAD_STARTUP_REPLAY"],
                         retained_history_sha256=sha(json.dumps(retained, sort_keys=True).encode()))
        proof["status"] = "ACTUAL_PHASE_COMPLETED"
    except Exception as failure:
        message = str(failure)
        proof["failure_code"] = message if re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", message) else "CHILD_DDL_OR_BOOTSTRAP_ERROR"
        original = getattr(failure, "orig", None)
        state = getattr(original, "pgcode", None)
        if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state):
            proof["sqlstate"] = state
    finally:
        engine.dispose()
        imported_engine.dispose()
        exclusive_json(args.proof, proof)
    return 0 if proof["status"] == "ACTUAL_PHASE_COMPLETED" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--self-sha256")
    parser.add_argument("--database-url")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--database")
    parser.add_argument("--mode", choices=("fresh", "baseline", "upgrade"))
    parser.add_argument("--proof", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--previous-sha256")
    args = parser.parse_args()
    if args.child:
        return child(args)
    need(all((args.baseline_source, args.manifest, args.manifest_sha256, args.self_sha256,
              args.database_url, args.result)), "ALL_REVIEWED_PARENT_ARGUMENTS_REQUIRED")
    return parent(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError, KeyError):
        print("MIGRATION_ENTRY_REFUSED_OR_UNRESOLVED", file=sys.stderr)
        raise SystemExit(1)

