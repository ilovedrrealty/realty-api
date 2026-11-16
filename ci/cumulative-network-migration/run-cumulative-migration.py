"""Host-only rehearsal of the real startup migration on two disposable databases."""

from __future__ import annotations

import argparse
import ast
from contextlib import closing, redirect_stdout
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
from uuid import UUID, uuid4


APPLICATION = Path("/application")
BASELINE = Path("/baseline")
PRIOR = Path("/prior/run_migration_acceptance.py")
PRIOR_SHA256 = "671b132034717c05c9e349d05316a3758d4034bb7649d8decd367ac58c26b00b"
APPLICATION_COMMIT = "b38d477c0e2e50f87f1f2e4cc1ceb3c707af92c5"
APPLICATION_TREE = "a53b3f829f70717069f5c637c826fb8f92c02f7c"
BASELINE_COMMIT = "0811946ea02a310b03116960663e3ba6871a07d0"
BASELINE_TREE = "18b2087a31853a275c5c6ca791a68214b1cd38ef"
OLD = "52b2edc810ad"
CHAIN = (
    "f4b27d6e913c", "e64a91f327bc", "d8f3a7c2e615", "a6e4c2b8d517", "e2b8c4a6d903",
    "f71c5a9d243e", "a61d9e4b7f20", "e4a1c7b9d305", "c6f3a2e8d519", "b7e2c4d6f901",
    "a4b6c0d2e834", "f3a5b9c1d723", "e2f4a8b0c612", "d1e3f7a9b521", "c90e75f2a6d1",
    "b12d41e2c9f0", "82e1a7c4d9b0",
)
HEAD = CHAIN[0]
LATER_TABLES = frozenset({
    "network_service_work", "network_service_work_actions", "network_service_conversation_messages",
    "network_service_negotiation_messages", "network_service_reviews", "network_service_favorites",
    "network_maintenance_preferences", "network_maintenance_schedules", "network_maintenance_occurrences",
    "network_maintenance_actions", "network_due_work_preferences", "network_due_work_dispositions",
    "network_due_work_actions", "network_history_shares", "network_history_share_actions",
    "network_property_custody_transfers", "network_property_custody_actions",
})
ROOM_FIELDS = {
    "listings": "room_photo_tour_snapshot",
    "listing_edits": "room_photo_tour_base_gallery_sha256",
}


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def prior_recipe():
    require(PRIOR.is_file() and hashlib.sha256(PRIOR.read_bytes()).hexdigest() == PRIOR_SHA256,
            "IMMUTABLE_PRIOR_RECIPE_REQUIRED")
    spec = importlib.util.spec_from_file_location("accepted_migration_recipe", PRIOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(module.BASE == BASELINE_COMMIT and module.OLD == OLD and len(module.LEGACY) == 20
            and len(module.NEW | LATER_TABLES) == 40 and not module.NEW & LATER_TABLES,
            "PRIOR_RECIPE_SCOPE_CHANGED")
    return module


def revision_graph(files):
    graph = {}
    for name, raw in files:
        fields = {}
        for node in ast.parse(raw, filename=name).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
            elif isinstance(node, ast.AnnAssign):
                target = node.target
            else:
                continue
            if isinstance(target, ast.Name) and target.id in ("revision", "down_revision"):
                fields[target.id] = ast.literal_eval(node.value)
        require(set(fields) == {"revision", "down_revision"}, "MIGRATION_SOURCE_METADATA_REQUIRED")
        rev, previous = fields["revision"], fields["down_revision"]
        parents = () if previous is None else (previous,) if isinstance(previous, str) else previous
        require(isinstance(rev, str) and rev not in graph and isinstance(parents, tuple)
                and all(isinstance(parent, str) for parent in parents) and len(set(parents)) == len(parents),
                "UNIQUE_MIGRATION_REVISION_AND_PARENTS_REQUIRED")
        graph[rev] = parents
    return graph


def graph_check(active, old):
    def connected(graph, head, count):
        seen = set()

        def visit(revision, ancestors):
            require(revision in graph and revision not in ancestors, "MIGRATION_LINK_OR_CYCLE")
            if revision not in seen:
                for parent in graph[revision]:
                    visit(parent, ancestors | {revision})
                seen.add(revision)

        visit(head, set())
        require(len(seen) == len(graph) == count, "MIGRATION_GRAPH_COUNT_CHANGED")

    connected(active, HEAD, 62)
    connected(old, OLD, 45)
    require(set(active) == set(old) | set(CHAIN) and all(active[key] == value for key, value in old.items())
            and all(active[key] == (parent,) for key, parent in zip(CHAIN, (*CHAIN[1:], OLD))),
            "MIGRATION_ANCESTRY_CHANGED")


def runtime_guard():
    attempts = []

    def audit(event, args):
        forbidden = event in ("subprocess.Popen", "os.system", "socket.bind", "socket.sendto", "socket.sendmsg")
        if event == "socket.connect":
            target = args[1]
            forbidden |= not isinstance(target, tuple) or target[:2] != ("127.0.0.1", 55449)
        if event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex"):
            forbidden |= args[0] != "127.0.0.1"
        if forbidden:
            attempts.append(event)
            raise PermissionError("EXTERNAL_TRANSPORT_REFUSED")

    sys.addaudithook(audit)
    return attempts


def legacy_snapshot(connection, prior, inspect, text, *, room_fields_present):
    rows = prior.fingerprint(connection, prior.LEGACY, text)
    inspector = inspect(connection)
    columns = {}
    for name in sorted(prior.LEGACY):
        observed = inspector.get_columns(name, schema="public")
        if name in ROOM_FIELDS:
            field = ROOM_FIELDS[name]
            matching = [column for column in observed if column["name"] == field]
            if room_fields_present:
                expected_type = "JSONB" if name == "listings" else "TEXT"
                require(len(matching) == 1 and str(matching[0]["type"]) == expected_type
                        and matching[0]["nullable"] and matching[0]["default"] is None,
                        "EXACT_NULLABLE_ROOM_FIELD_REQUIRED")
                require(connection.exec_driver_sql(f'SELECT count(*) FROM "{name}" WHERE "{field}" IS NOT NULL')
                        .scalar_one() == 0, "LEGACY_ROOM_FIELD_BACKFILLED")
                row = connection.execute(text(f"""
                    SELECT count(*) AS n,COALESCE(jsonb_agg(body ORDER BY body::text),'[]'::jsonb)::text AS body
                    FROM (SELECT to_jsonb(t)-CAST(:field AS text) AS body FROM "{name}" t) original
                    """), {"field": field}).one()
                rows[name] = {"rows": row.n, "sha256": prior.sha(row.body.encode())}
            else:
                require(not matching, "ROOM_FIELD_ALREADY_IN_BASELINE")
            observed = [column for column in observed if column["name"] != field]
        columns[name] = [{"name": column["name"], "type": str(column["type"]),
                          "nullable": column["nullable"], "default": column["default"]} for column in observed]
    # Keep named constraints, indexes and every pre-existing field in the comparison.
    relations = {name: [inspector.get_pk_constraint(name, schema="public"),
                        inspector.get_foreign_keys(name, schema="public"),
                        inspector.get_unique_constraints(name, schema="public"),
                        inspector.get_check_constraints(name, schema="public"),
                        inspector.get_indexes(name, schema="public")] for name in sorted(prior.LEGACY)}
    return {"rows": rows, "schema": digest([columns, relations])}


def catalog_facts(connection):
    definitions = {}
    for kind, statement in {
        "triggers": """
            SELECT t.relname,g.tgname,g.tgenabled,pg_get_triggerdef(g.oid)
            FROM pg_trigger g JOIN pg_class t ON t.oid=g.tgrelid
            WHERE t.relnamespace='public'::regnamespace AND NOT g.tgisinternal ORDER BY 1,2
        """,
        "trigger_functions": """
            SELECT p.oid::regprocedure::text,p.prosecdef,p.proconfig,p.proacl::text,pg_get_functiondef(p.oid)
            FROM pg_proc p WHERE p.pronamespace='public'::regnamespace
            AND p.prorettype='pg_catalog.trigger'::regtype ORDER BY 1
        """,
    }.items():
        data = [list(row) for row in connection.exec_driver_sql(statement)]
        require(data, "EXPECTED_MIGRATION_TRIGGER_CATALOG_EMPTY")
        definitions[kind] = data
    return digest(definitions)


def seed_default_grants(connection, prior):
    for role in ("PUBLIC", '"anon"', '"authenticated"'):
        connection.exec_driver_sql(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {role}")
    actual = {(row.grantee, row.privilege_type) for row in connection.exec_driver_sql("""
        SELECT COALESCE(r.rolname,'PUBLIC') AS grantee,a.privilege_type FROM pg_default_acl d
        CROSS JOIN LATERAL aclexplode(d.defaclacl) a LEFT JOIN pg_roles r ON r.oid=a.grantee
        WHERE d.defaclnamespace='public'::regnamespace AND d.defaclobjtype='r'
        AND d.defaclrole=(SELECT oid FROM pg_roles WHERE rolname=current_user)
    """)}
    require(all((role, privilege) in actual for role in ("PUBLIC", *prior.ALLOWED_ROLE)
                for privilege in prior.PG17_TABLE_PRIVILEGES), "NONVACUOUS_ROLE_GRANTS_REQUIRED")


def child(args):
    prior = prior_recipe()
    require(prior.DB_NAME.fullmatch(args.database) and os.environ.get("DATABASE_URL") == prior.url_for(args.database),
            "SANITIZED_CHILD_TARGET_REQUIRED")
    require(Path.cwd().parent == Path("/tmp") and not (Path.cwd() / ".env").exists(), "OWNED_CHILD_SCRATCH_REQUIRED")
    attempts = runtime_guard()
    source = BASELINE if args.mode == "baseline" else APPLICATION
    sys.path.insert(0, str(source))
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.pool import NullPool
    from database import Base, engine as imported_engine
    import models  # noqa: F401 - full application metadata
    import bootstrap_db

    legacy = prior.LEGACY
    new = prior.NEW | LATER_TABLES
    expected = legacy if args.mode == "baseline" else legacy | new
    head = OLD if args.mode == "baseline" else HEAD
    config = Config(str(source / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    require(set(Base.metadata.tables) == expected and scripts.get_heads() == [head]
            and len(list(scripts.walk_revisions())) == (45 if head == OLD else 62), "RUNTIME_SOURCE_GRAPH_CHANGED")
    if args.mode == "upgrade":
        require(tuple(r.revision for r in scripts.iterate_revisions(HEAD, OLD)) == CHAIN,
                "RUNTIME_UPGRADE_CHAIN_CHANGED")
    engine = create_engine(prior.url_for(args.database), poolclass=NullPool)

    def check_database(connection):
        facts = prior.db_facts(connection, text)
        require(facts["oid"] == args.oid and facts["marker"] == args.marker,
                "OWNED_CHILD_DATABASE_IDENTITY_CHANGED")
        require(connection.exec_driver_sql("SELECT current_schema(),current_user").one() == ("public", "postgres"),
                "EXPECTED_DATABASE_SCHEMA_OR_USER_CHANGED")

    def check_version(connection, version):
        require(connection.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalars().all() == [version],
                "ACTUAL_ALEMBIC_VERSION_CHANGED")

    try:
        with engine.begin() as connection:
            check_database(connection)
            tables = set(inspect(connection).get_table_names(schema="public"))
            if args.mode == "upgrade":
                require(tables == legacy | {"alembic_version"}, "UPGRADE_DID_NOT_START_FROM_EXACT_OLD_TABLES")
                check_version(connection, OLD)
                baseline = legacy_snapshot(connection, prior, inspect, text, room_fields_present=False)
                seed_default_grants(connection, prior)
            else:
                require(not tables, "GENUINELY_EMPTY_DATABASE_REQUIRED")

        bootstrap_db.main()
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names(schema="public"))
            if args.mode == "upgrade":
                require(tables == legacy | {"alembic_version"}, "POPULATED_BOOTSTRAP_CHANGED_TABLES")
                check_version(connection, OLD)
                require(legacy_snapshot(connection, prior, inspect, text, room_fields_present=False) == baseline,
                        "POPULATED_BOOTSTRAP_CHANGED_DATA")
            else:
                require(tables == expected, "FRESH_BOOTSTRAP_DID_NOT_CREATE_TABLES_OR_STAMPED")
        command.upgrade(config, "head")

        with engine.begin() as connection:
            check_version(connection, head)
            require(set(inspect(connection).get_table_names(schema="public")) == expected | {"alembic_version"},
                    "POST_UPGRADE_TABLE_SET_CHANGED")
            proof = {"mode": args.mode, "head": head, "oid": args.oid}
            if args.mode == "baseline":
                seeded = prior.seed_baseline(connection, Base.metadata)
                connection.execute(Base.metadata.tables["listing_edits"].insert(), {
                    "id": uuid4(), "listing_id": UUID(seeded["listing"]), "submitted_by": UUID(seeded["actor"]),
                    "proposed_data": {"title": "Synthetic pending villa edit"}, "status": "pending",
                })
                prior.strict_profiles(connection, text)
                proof["legacy"] = legacy_snapshot(connection, prior, inspect, text, room_fields_present=False)
            else:
                prior.privacy(connection, text, new)
                proof["private_schema"] = prior.schema_facts(connection, new, inspect)
                proof["catalog"] = catalog_facts(connection)
                proof["legacy"] = legacy_snapshot(connection, prior, inspect, text, room_fields_present=True)
                require(all(item["rows"] == 0 for item in prior.fingerprint(connection, new, text).values()),
                        "NO_PRIVATE_BACKFILL_OR_NOTIFICATION_OUTBOX_EXPECTED")
                if args.mode == "upgrade":
                    prior.strict_profiles(connection, text)
                    require(proof["legacy"] == baseline, "ADDITIVE_MIGRATION_CHANGED_EXISTING_LEGACY_FIELDS")
            retained = prior.fingerprint(connection, expected, text)
            schema = prior.schema_facts(connection, expected, inspect)
            require(connection.exec_driver_sql("SELECT pg_get_serial_sequence('public.users','user_code')")
                    .scalar_one() == "public.users_user_code_seq", "USER_CODE_SEQUENCE_MISSING")

        bootstrap_db.main()
        command.upgrade(config, "head")
        with engine.connect() as connection:
            check_version(connection, head)
            require(prior.fingerprint(connection, expected, text) == retained
                    and prior.schema_facts(connection, expected, inspect) == schema,
                    "REPEATED_STARTUP_CHANGED_SCHEMA_OR_DATA")
            if args.mode != "baseline":
                prior.privacy(connection, text, new)
                require(catalog_facts(connection) == proof["catalog"], "REPEATED_STARTUP_CHANGED_TRIGGER_CATALOG")
        require(not attempts, "UNEXPECTED_TRANSPORT_ATTEMPT")
        proof["transports"] = len(attempts)
    finally:
        engine.dispose()
        imported_engine.dispose()
    return proof


def database_facts(cursor, name):
    cursor.execute("""
        SELECT d.oid,r.rolname,shobj_description(d.oid,'pg_database') FROM pg_database d
        JOIN pg_roles r ON r.oid=d.datdba WHERE d.datname=%s
    """, (name,))
    return cursor.fetchone()


def run_phase(prior, scratch, mode, name, oid, marker):
    environment = prior.clean_environment(name, "cumulative-migration-" + mode)
    environment.update(HOME=str(scratch), TMPDIR=str(scratch))
    process = subprocess.run([sys.executable, "-I", "-B", __file__, mode, name, str(oid), marker],
                             cwd=scratch, env=environment, capture_output=True, timeout=110, check=False)
    require(len(process.stdout) <= prior.MAX_PROOF and len(process.stderr) <= prior.MAX_PROOF,
            "CHILD_OUTPUT_SIZE_REFUSED")
    try:
        proof = json.loads(process.stdout)
    except (UnicodeError, ValueError) as error:
        raise RuntimeError(mode.upper() + "_CHILD_PROOF_MISSING") from error
    if process.returncode:
        reason = proof.get("reason", "CHILD_FAILED")
        require(False, mode.upper() + "_" + reason[:65])
    require(proof["mode"] == mode and proof["oid"] == oid and proof["transports"] == 0,
            "CHILD_PROOF_IDENTITY_OR_TRANSPORT_CHANGED")
    return proof


def parent():
    require(sys.platform == "linux" and sys.version_info[:2] == (3, 11), "LINUX_PYTHON311_REQUIRED")
    require(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("GITHUB_REPOSITORY") == "ilovedrrealty/realty-api"
            and {name for _, name in socket.if_nameindex()} == {"lo"}, "ISOLATED_HOSTED_LOOPBACK_REQUIRED")
    nonce = os.environ.get("ILDR_CUMULATIVE_MIGRATION_NONCE", "")
    require(re.fullmatch(r"[a-f0-9]{32}", nonce), "EXACT_HOSTED_NONCE_REQUIRED")
    graph_check(*(revision_graph((str(path), path.read_bytes()) for path in
                                 sorted((source / "alembic" / "versions").glob("*.py")))
                  for source in (APPLICATION, BASELINE)))
    prior = prior_recipe()
    from psycopg2 import connect, sql

    databases = {}
    with closing(connect(host="127.0.0.1", port=55449, user="postgres", dbname=prior.ANCHOR, connect_timeout=5,
                         sslmode="disable", gssencmode="disable", options="-c statement_timeout=5000")) as controller:
        controller.autocommit = True
        with controller.cursor() as cursor:
            anchor = database_facts(cursor, prior.ANCHOR)
            require(anchor is not None and anchor[1:] == ("postgres", "ildr-cumulative-migration-ci:" + nonce),
                    "EXPECTED_DISPOSABLE_DATABASE_MARKER_REQUIRED")
            cursor.execute("SHOW server_version_num")
            postgres_version = int(cursor.fetchone()[0])
            require(postgres_version // 10000 == 17, "POSTGRES17_REQUIRED")
            try:
                for role in prior.ALLOWED_ROLE:
                    cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role)))
                for kind in ("a", "b"):
                    name = prior.PREFIX + kind + "_" + nonce
                    require(prior.DB_NAME.fullmatch(name) and database_facts(cursor, name) is None, "OWNED_NEW_DATABASE_NAME_REQUIRED")
                    cursor.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
                    marker = "ildr-cumulative-owned:" + nonce + ":" + kind
                    cursor.execute(sql.SQL("COMMENT ON DATABASE {} IS %s").format(sql.Identifier(name)), (marker,))
                    facts = database_facts(cursor, name)
                    require(facts[1:] == ("postgres", marker), "OWNED_NEW_DATABASE_MARKER_REQUIRED")
                    databases[kind] = (name, facts[0], marker)

                with tempfile.TemporaryDirectory(prefix="ildr-cumulative-", dir="/tmp") as temporary:
                    scratch = Path(temporary)
                    fresh = run_phase(prior, scratch, "fresh", *databases["a"])
                    baseline = run_phase(prior, scratch, "baseline", *databases["b"])
                    upgraded = run_phase(prior, scratch, "upgrade", *databases["b"])
                require(baseline["head"] == OLD and fresh["head"] == HEAD and upgraded["head"] == HEAD,
                        "EXPECTED_ACTUAL_PHASE_HEADS_REQUIRED")
                require(baseline["legacy"] == upgraded["legacy"], "CROSS_PROCESS_BASELINE_RETENTION_CHANGED")
                counts = baseline["legacy"]["rows"]
                require(all(counts[table]["rows"] == number for table, number in {
                    "users": 2, "listings": 1, "listing_edits": 1, "leads": 1,
                    "professional_profiles": 46, "professional_profile_revisions": 46,
                }.items()), "EXPECTED_POPULATED_LEGACY_FIXTURE_REQUIRED")
                require(fresh["private_schema"] == upgraded["private_schema"], "DIFFERENT_FRESH_AND_UPGRADED_PRIVATE_DDL")
                require(fresh["catalog"] == upgraded["catalog"], "DIFFERENT_FRESH_AND_UPGRADED_TRIGGER_CATALOG")
            finally:
                for name, oid, marker in reversed(tuple(databases.values())):
                    require(database_facts(cursor, name) == (oid, "postgres", marker), "DATABASE_CLEANUP_OWNERSHIP_CHANGED")
                    cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
                    require(database_facts(cursor, name) is None, "OWNED_DATABASE_CLEANUP_NOT_VERIFIED")

    return {
        "status": "PASS", "application_commit": APPLICATION_COMMIT, "application_tree": APPLICATION_TREE,
        "baseline_commit": BASELINE_COMMIT, "baseline_tree": BASELINE_TREE, "baseline_head": OLD,
        "application_head": HEAD, "postgres_major": postgres_version // 10000, "postgres_version_num": postgres_version,
        "baseline_revisions": 45, "application_revisions": 62, "migration_delta": len(CHAIN), "private_table_count": 40,
        "private_draft_profile_count": 46, "historical_lead_count": 1, "legacy_rows_preserved": True,
        "legacy_schema_preserved": True, "room_tour_null": True, "new_schema_matches": True,
        "database_catalog_matches": True, "nonvacuous_grant_fixture": True, "no_outbox_backfill": True,
        "startup_idempotent": True, "database_cleanup": True, "external_transport_attempts": 0,
        "private_schema_sha256": fresh["private_schema"], "trigger_catalog_sha256": fresh["catalog"],
    }


def failure_code(error):
    if len(error.args) == 1 and isinstance(error.args[0], str) and re.fullmatch(r"[A-Z0-9_]{1,75}", error.args[0]):
        return error.args[0]
    return type(error).__name__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs="?", choices=("fresh", "baseline", "upgrade"))
    parser.add_argument("database", nargs="?")
    parser.add_argument("oid", nargs="?", type=int)
    parser.add_argument("marker", nargs="?")
    args = parser.parse_args()
    prefix = "" if args.mode else "CUMULATIVE_MIGRATION_RESULT "
    try:
        with redirect_stdout(sys.stderr):
            proof = child(args) if args.mode else parent()
    except Exception as error:
        print(prefix + json.dumps({"status": "FAILED", "reason": failure_code(error)}), flush=True)
        return 1
    print(prefix + json.dumps(proof, sort_keys=True, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
