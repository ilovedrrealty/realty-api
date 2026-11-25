"""Owned native profile proof; old data, version metadata and new DDL are separate."""
import ast
from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
import json
import logging
import os
from pathlib import Path
import re
import smtplib
import subprocess
import sys
from urllib import request as urllib_request
from unittest.mock import patch
import unittest
from uuid import UUID

COMMIT = "a75f9c26f3bceb14afcb6e1150e1b2a3d50fbada"
TREE = "12bbe32d4a6578bc58741c3f10f55bfccbf07e39"
BASE_COMMIT = "17943ba1714673c51c43cdbb800106f91612f527"
BASE_TREE = "ff940a7b6e983d4c669202746d9676b4b82e9a06"
OLD_HEAD, HEAD = "d8f4c2a91e67", "e8b5a3d1f907"
INHERITED = Path(__file__).resolve().parent.parent / "native-inbox/verify.py"
INHERITED_SHA = "c8ca57e59d5f4c355e8a5ebae2a9040231b91d6c7a8a6c8a1677d7089bc391b9"
BLACKHOLE_SHA = "ac58eb70c25035379974859710d4c56e9d0226f98479dd192384943a372196ea"
VISUAL = ("network_media", "social_network_profiles")
NEW_COLUMNS = {"network_media": ("profile_user_id", "profile_kind"),
               "social_network_profiles": ("avatar_media_id", "cover_media_id")}
EXPECTED = {
    "status": "PASS", "tests": 134, "resilience_source_tests": 67, "inbox_tests": 15,
    "hosted_safety_tests": 2, "pure_options_tests": 4, "owned_recovery_tests": 3,
    "delivery_regression_tests": 53, "customer_copy_tests": 7, "owned_handshake_cases": 1,
    "legacy_award_tests": 5, "legacy_negotiation_tests": 5, "profile_route_tests": 30,
    "private_image_tests": 4, "profile_input_tests": 3, "profile_retention_tests": 2,
    "old_tables": 99, "schema_tables": 106, "model_tables": 105,
    "untouched_data_tables": 103, "projected_data_tables": 2, "new_nullable_columns": 4,
    "baseline_visual_sentinel_rows": 2, "owner_contacts_unchanged": 82,
    "private_profiles_unchanged": 46, "accepted_jobs_unchanged": 9, "cms_version_unchanged": 7,
    "floorplans_unchanged": 2, "external_provider_attempts": 0,
    "previous99_preserved_before_sentinels": True, "raw103_data_unchanged": True,
    "old2_column_projection_unchanged": True, "all106_sequences_unchanged": True,
    "old_rows_new_columns_null": True, "exact_version_transition": True,
    "exact_profile_ddl_delta": True, "new9_model_ddl_projection_equal": True,
    "private_rls_acl": True, "old_acl_rls_unchanged": True,
    "new_profile_guard_private_invoker": True, "empty_downgrade_reupgrade_equal": True,
    "selected_downgrade_refused": True, "unselected_downgrade_refused": True,
    "full106_final_rows_sequences_catalog_unchanged": True, "all_owned_schemas_removed": True,
    "server_start_unchanged": True, "tcp_query_deadline_claimed": False,
}
STAGE = "source"
ATTEMPTS = []


class Failure(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise Failure(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def load_pinned(path, digest, name):
    require(path.is_file() and not path.is_symlink() and os.statvfs(path).f_flag & os.ST_RDONLY
            and sha256(path.read_bytes()).hexdigest() == digest, "exact_readonly_helper_required")
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def refuse(*_args, **_kwargs):
    ATTEMPTS.append(1)
    raise RuntimeError("external_transport_refused")


def source_at(base, root, commit, tree):
    previous = (base.APP_COMMIT, base.APP_TREE)
    require("ILDR_BROWSER_APPLICATION_ROOT" not in os.environ, "no_source_override_required")
    try:
        base.APP_COMMIT, base.APP_TREE = commit, tree
        os.environ["ILDR_BROWSER_APPLICATION_ROOT"] = str(root)
        require(base.verified_checkout() == root, "exact_reference_source_required")
    finally:
        os.environ.pop("ILDR_BROWSER_APPLICATION_ROOT", None)
        base.APP_COMMIT, base.APP_TREE = previous


def baseline_upgrade(base, url):
    root = Path("/baseline106")
    source_at(base, root, BASE_COMMIT, BASE_TREE)
    # This process imports only the real d8 source. Current e8 models cannot
    # materialize the new columns before their genuine migration is exercised.
    script = "import sys;sys.path.insert(0,'/baseline106');from alembic import command;from alembic.config import Config;command.upgrade(Config('alembic.ini'),'head')"
    result = subprocess.run(["/usr/local/bin/python", "-I", "-B", "-c", script], cwd=root,
        env=dict(os.environ, DATABASE_URL=url), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    require(result.returncode == 0 and len(result.stdout) <= 2 * 1024 * 1024,
            "actual_baseline106_migration_required")


def version(catalog, database, expected):
    require(catalog.query(database, "SELECT version_num FROM public.alembic_version") == [(expected,)],
            "exact_single_version_row_required")


def seed_visual_sentinels(catalog, database, retained):
    from database import engine
    from sqlalchemy import text
    require(all(catalog.query(database, "SELECT count(*) FROM public." + table) == [(0,)] for table in VISUAL),
            "empty_old_visual_tables_required")
    # Only old d8 columns are named. This declaration adds no account, contact,
    # private upload or storage object; the inert media row is deliberately not
    # selected. It makes every old-column/new-NULL comparison nonvacuous.
    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO social_network_profiles
            (user_id,display_name,bio,status_text,visibility,wall_policy,comments_enabled,version)
            VALUES (:owner,'Synthetic retained alias','Retain this old biography','Retained status',
                    'private','own_only',true,3)"""), {"owner": retained.IDS["owner"]})
        connection.execute(text("""INSERT INTO network_media
            (id,uploader_id,project_id,update_id,post_id,object_key,sha256,byte_count,width,height,created_at)
            VALUES (:id,:owner,NULL,NULL,NULL,:key,:sha,12,2,2,:created)"""),
            {"id": UUID(int=6101), "owner": retained.IDS["owner"],
             "key": "synthetic-inert-old-media/" + os.environ["ILDR_BROWSER_CI_NONCE"] + ".webp",
             "sha": "a" * 64, "created": retained.HISTORY})
    require(all(catalog.query(database, "SELECT count(*) FROM public." + table) == [(1,)] for table in VISUAL),
            "two_declared_old_visual_sentinels_required")


def data_baseline(catalog, database, tables, schema):
    raw_tables = tuple(table for table in tables if table not in (*VISUAL, "alembic_version"))
    require(len(raw_tables) == 103 and len(tables) == 106, "103_raw_plus2_projected_plus_version_required")
    columns = {table: [row[1] for row in schema["columns"] if row[0] == table] for table in VISUAL}
    require(all(columns.values()) and all(not set(columns[table]) & set(NEW_COLUMNS[table]) for table in VISUAL),
            "real_old_visual_columns_required")
    raw = catalog.snapshot(database, raw_tables)
    projected = catalog.snapshot(database, VISUAL, columns=columns)
    # The immutable snapshot helper excludes alembic_version. Version metadata
    # is deliberately proved by version(), never counted as unchanged data.
    require("alembic_version" not in raw and "alembic_version" not in projected
            and len(raw) == 104 and len(projected) == 3 and raw["sequences"] == projected["sequences"],
            "snapshot_version_exclusion_and_all_sequences_required")
    return {"tables": raw_tables, "columns": columns,
            "raw_hashes": {key: sha256(value.encode()).hexdigest() for key, value in raw.items() if key != "sequences"},
            "projected": projected, "sequences": raw["sequences"]}


def preserve_data(catalog, database, before, with_visuals):
    raw = catalog.snapshot(database, before["tables"])
    require({key: sha256(value.encode()).hexdigest() for key, value in raw.items() if key != "sequences"}
            == before["raw_hashes"], "all103_raw_data_hashes_required")
    require(catalog.snapshot(database, VISUAL, columns=before["columns"]) == before["projected"],
            "both_old_column_projections_and_sequences_required")
    require(raw["sequences"] == before["sequences"], "all106_sequences_unchanged_required")
    if with_visuals:
        for table in VISUAL:
            fields = " OR ".join(name + " IS NOT NULL" for name in NEW_COLUMNS[table])
            require(catalog.query(database, "SELECT count(*),count(*) FILTER(WHERE " + fields + ") FROM public." + table)
                    == [(1, 0)], "all_old_visual_rows_new_columns_null_required")


def auxiliary_metadata(catalog, database):
    # Snapshot's last_values are complemented by the complete definition and
    # ownership of every public sequence, including independent sequences.
    sequences = catalog.query(database, """SELECT sequencename,sequenceowner,data_type::text,
        start_value,min_value,max_value,increment_by,cycle,cache_size,last_value
        FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename""")
    functions = catalog.query(database, """SELECT p.proname,pg_get_function_identity_arguments(p.oid),
        p.prosecdef,p.proleakproof,p.proparallel,p.provolatile,p.proconfig,o.rolname,
        coalesce(r.rolname,'PUBLIC'),a.privilege_type,a.is_grantable
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles o ON o.oid=p.proowner
        CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
        LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE n.nspname='public' AND p.prokind='f'
        ORDER BY p.proname,pg_get_function_identity_arguments(p.oid),coalesce(r.rolname,'PUBLIC'),a.privilege_type,a.is_grantable""")
    return {"sequences": sequences, "functions": functions}


def preserve_auxiliary(catalog, database, before, with_visuals):
    current = auxiliary_metadata(catalog, database)
    require(current["sequences"] == before["sequences"], "all_public_sequence_definitions_and_values_required")
    if with_visuals:
        added = [row for row in current["functions"] if row[0] == "network_guard_profile_visuals"]
        require(len(added) == 1 and added[0][1] == "" and added[0][2] is False
                and added[0][7:] == ("postgres", "postgres", "EXECUTE", False),
                "one_private_invoker_function_acl_required")
        current["functions"] = [row for row in current["functions"] if row not in added]
    require(current["functions"] == before["functions"], "all_old_public_function_security_metadata_required")


def ddl_delta(catalog, database, tables, before):
    current = catalog.catalog(database, tables)
    original = {key: list(rows) for key, rows in current.items()}
    expected_columns = {("network_media", "profile_user_id", "uuid", False, None),
                        ("network_media", "profile_kind", "text", False, None),
                        ("social_network_profiles", "avatar_media_id", "uuid", False, None),
                        ("social_network_profiles", "cover_media_id", "uuid", False, None)}
    added = {row for row in current["columns"] if row not in before["columns"]}
    require(added == expected_columns, "exact_four_nullable_columns_required")
    current["columns"] = [row for row in current["columns"] if row not in added]
    constraints = [row for row in current["constraints"] if row not in before["constraints"]]
    expected_constraints = {
        ("network_media", "network_media_profile_user_id_fkey", "f"),
        ("network_media", "ck_network_media_profile_scope", "c"),
        ("social_network_profiles", "social_network_profiles_avatar_media_id_fkey", "f"),
        ("social_network_profiles", "social_network_profiles_cover_media_id_fkey", "f"),
        ("social_network_profiles", "ck_network_profile_visuals_distinct", "c")}
    require(len(constraints) == 5 and {row[:3] for row in constraints} == expected_constraints,
            "exact_three_fks_two_checks_required")
    current["constraints"] = [row for row in current["constraints"] if row not in constraints]
    indexes = [row for row in current["indexes"] if row not in before["indexes"]]
    require(len(indexes) == 1 and indexes[0][:4] == ("network_media", "ix_network_media_profile_user", False, False),
            "exact_profile_index_required")
    current["indexes"] = [row for row in current["indexes"] if row not in indexes]
    triggers = [row for row in current["triggers"] if row not in before["triggers"]]
    require(len(triggers) == 1 and triggers[0][:3] == ("social_network_profiles", "network_profile_visual_guard", "O"),
            "exact_profile_trigger_required")
    # Native flags prove ROW|BEFORE|INSERT|UPDATE, zero arguments, no WHEN,
    # no column restriction and no constraint trigger without guessing how
    # pg_get_triggerdef qualifies visible identifiers on this connection.
    require(catalog.query(database, """SELECT c.relname,t.tgname,t.tgenabled,t.tgtype,p.proname,
        t.tgnargs,t.tgqual IS NULL,t.tgconstraint=0,t.tgattr::text
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_proc p ON p.oid=t.tgfoid WHERE n.nspname='public' AND NOT t.tgisinternal
        AND c.relname='social_network_profiles' AND t.tgname='network_profile_visual_guard'""")
        == [("social_network_profiles", "network_profile_visual_guard", "O", 23,
             "network_guard_profile_visuals", 0, True, True, "")], "native_profile_trigger_structure_required")
    require(catalog.query(database, """SELECT x.indisvalid,x.indisready FROM pg_index x
        JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=i.relnamespace
        WHERE n.nspname='public' AND i.relname='ix_network_media_profile_user'""") == [(True, True)],
        "valid_ready_profile_index_required")
    current["triggers"] = [row for row in current["triggers"] if row not in triggers]
    functions = [row for row in current["functions"] if row not in before["functions"]]
    require(len(functions) == 1 and functions[0][:2] == ("network_guard_profile_visuals", ""),
            "exact_one_profile_guard_function_required")
    current["functions"] = [row for row in current["functions"] if row not in functions]
    require(current == before, "all_existing106_catalog_acl_rls_unchanged_required")
    rows = catalog.query(database, """SELECT p.prosecdef,p.prorettype::regtype::text,l.lanname,
        coalesce(r.rolname,'PUBLIC'),a.privilege_type,a.is_grantable
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang
        CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
        LEFT JOIN pg_roles r ON r.oid=a.grantee
        WHERE n.nspname='public' AND p.proname='network_guard_profile_visuals' AND p.pronargs=0""")
    require(rows and all(row[:3] == (False, "trigger", "plpgsql") and row[3] == "postgres"
                        and row[4] == "EXECUTE" and not row[5] for row in rows),
            "private_profile_guard_invoker_acl_required")
    require(catalog.query(database, """SELECT count(*) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(%s)
        AND NOT k.convalidated""", (list(VISUAL),)) == [(0,)], "validated_visual_constraints_required")
    return original


def native_projection(connection, schema, tables):
    from sqlalchemy import text
    statements = {
        "columns": """SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
            pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE n.nspname=:schema AND c.relname=ANY(:tables) AND a.attnum>0 AND NOT a.attisdropped
            ORDER BY c.relname,a.attnum""",
        "constraints": """SELECT c.relname,k.conname,k.contype,pg_get_constraintdef(k.oid,true) FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_constraint k ON k.conrelid=c.oid
            WHERE n.nspname=:schema AND c.relname=ANY(:tables) ORDER BY c.relname,k.conname""",
        "indexes": """SELECT c.relname,i.relname,x.indisprimary,x.indisunique,x.indisvalid,x.indisready,pg_get_indexdef(i.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_index x ON x.indrelid=c.oid
            JOIN pg_class i ON i.oid=x.indexrelid WHERE n.nspname=:schema AND c.relname=ANY(:tables)
            ORDER BY c.relname,i.relname"""}
    def normalize(value):
        if isinstance(value, str):
            for name in (schema, "public"):
                value = value.replace('"' + name + '".', "").replace(name + ".", "")
        return value
    return {key: [[normalize(value) for value in row] for row in connection.execute(text(statement),
                   {"schema": schema, "tables": list(tables)}).all()] for key, statement in statements.items()}


def compare_models(catalog, database, tables):
    from database import Base, engine
    from sqlalchemy import text
    schema = "profile_model_" + os.environ["ILDR_BROWSER_CI_NONCE"]
    require(re.fullmatch(r"profile_model_[a-f0-9]{32}", schema) and len(Base.metadata.tables) == 105
            and set(Base.metadata.tables) == set(tables) - {"alembic_version"}, "exact105_model_table_set_required")
    selected = tuple(sorted(set(VISUAL) | set(INBOX)))
    require(len(selected) == 9, "nine_native_projection_tables_required")
    catalog.verify_check_render_controls(database)
    with engine.begin() as connection:
        connection.execute(text('CREATE SCHEMA "' + schema + '"'))
        connection.execute(text('SET LOCAL search_path TO "' + schema + '",public'))
        Base.metadata.create_all(connection, checkfirst=False)
        require(connection.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname=:schema"),
                                   {"schema": schema}).scalar() == 105, "actual105_model_tables_required")
        connection.execute(text("SET LOCAL search_path TO pg_catalog"))
        actual, expected = native_projection(connection, "public", selected), native_projection(connection, schema, selected)
        if actual != expected:
            defaults = {None: "none", "0": "zero_bare", "'0'::bigint": "zero_bigint_quoted", "now()": "now"}
            census = {"columns": 0, "constraints": 0, "indexes": 0, "samples": []}
            for section in ("columns", "constraints", "indexes"):
                left = {(row[0], row[2] if section == "columns" else row[1]): row for row in actual[section]}
                right = {(row[0], row[2] if section == "columns" else row[1]): row for row in expected[section]}
                for key in sorted(set(left) | set(right)):
                    a, b = left.get(key), right.get(key)
                    if a == b:
                        continue
                    census[section] += 1
                    if len(census["samples"]) >= 3:
                        continue
                    table, field = key
                    require(table in selected and re.fullmatch(r"[a-z_][a-z0-9_]{0,100}", field),
                            "source_owned_parity_identifier_required")
                    census["samples"].append({"section": section, "table": table, "field": field,
                        "actual_present": a is not None, "expected_present": b is not None,
                        "different_positions": [index for index in range(max(len(a or []), len(b or [])))
                                                if a is None or b is None or a[index] != b[index]],
                        "actual_default_kind": defaults.get(a[5], "other") if section == "columns" and a else "not_applicable",
                        "expected_default_kind": defaults.get(b[5], "other") if section == "columns" and b else "not_applicable"})
            print("PROFILE_CUSTOMIZATION_NATIVE_PARITY_CENSUS " + canonical(census), flush=True)
        require(actual == expected, "nine_native_model_ddl_projections_required")
        connection.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))


def suite_inventory(application, filename, classname, count, allowed_classes=None):
    tree = ast.parse((application / "tests" / filename).read_text())
    inventory = {node.name: {item.name for item in node.body if isinstance(item, ast.FunctionDef)
                           and item.name.startswith("test_")} for node in tree.body if isinstance(node, ast.ClassDef)}
    require(set(inventory) == (allowed_classes or {classname}) and len(inventory[classname]) == count,
            "actual_source_test_inventory_required")
    return inventory[classname]


def run_suite(testcase, count, label, methods):
    captured = StringIO()
    result = unittest.TestResult()
    with redirect_stdout(captured), redirect_stderr(captured):
        unittest.defaultTestLoader.loadTestsFromTestCase(testcase).run(result)
    counts = {"suite": label, "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skipped": len(result.skipped), "provider_attempts": len(ATTEMPTS)}
    print("PROFILE_CUSTOMIZATION_NATIVE_COUNTS " + canonical(counts), flush=True)
    for case, _ in result.failures + result.errors:
        method = getattr(case, "_testMethodName", "fixture_setup_or_cleanup")
        if method not in methods:
            method = "fixture_setup_or_cleanup"
        print("PROFILE_CUSTOMIZATION_NATIVE_FAILED_METHOD " + label + " " + method, flush=True)
    require(len(captured.getvalue().encode()) <= 2 * 1024 * 1024, "bounded_native_output_required")
    require(result.testsRun == count and result.wasSuccessful() and not result.skipped
            and not result.expectedFailures and not result.unexpectedSuccesses and not ATTEMPTS,
            "all_exact_native_cases_required")


def handshake():
    path = Path(__file__).with_name("blackhole-connect.py")
    require(path.is_file() and not path.is_symlink() and sha256(path.read_bytes()).hexdigest() == BLACKHOLE_SHA,
            "exact_owned_handshake_copy_required")
    result = subprocess.run(["/usr/local/bin/python", "-I", "-B", str(path)], env=dict(os.environ),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
    require(result.returncode == 0 and len(result.stdout) <= 2048 and not result.stderr, "bounded_handshake_child_required")
    lines = result.stdout.splitlines()
    require(len(lines) == 1 and lines[0].startswith(b"RESILIENCE_BLACKHOLE_RESULT "), "one_handshake_receipt_required")
    report = json.loads(lines[0].split(b" ", 1)[1])
    expected = {"status": "PASS", "case": "owned_startup_handshake_timeout", "connect_timeout_seconds": 10,
                "accepted_connections": 1, "startup_seen": True, "server_thread_joined": True,
                "application_queries": 0, "external_provider_attempts": 0}
    require(set(report) == set(expected) | {"elapsed_ms"} and type(report["elapsed_ms"]) is int
            and 8000 <= report["elapsed_ms"] <= 15000 and all(report.get(k) == v and type(report.get(k)) is type(v)
                                                           for k, v in expected.items()),
            "actual_handshake_timeout_and_thread_cleanup_required")
    print("RESILIENCE_BLACKHOLE_RESULT " + canonical(report), flush=True)


def retention_cases(routes, application):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    class ProfileRetentionTests(unittest.TestCase):
        # Copy just the fixture helpers, never inherit the 30 route tests.
        setUpClass = classmethod(routes.PropertyNetworkRoutes.setUpClass.__func__)
        cleanup_owned_schema = classmethod(routes.PropertyNetworkRoutes.cleanup_owned_schema.__func__)
        setUp = routes.PropertyNetworkRoutes.setUp
        patched = routes.PropertyNetworkRoutes.patched
        stop_patches = routes.PropertyNetworkRoutes.stop_patches
        request = routes.PropertyNetworkRoutes.request
        profile = routes.PropertyNetworkRoutes.profile
        profile_photo = routes.PropertyNetworkRoutes.profile_photo

        def retained_snapshot(self):
            with self.engine.connect() as connection:
                names = [row[0] for row in connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname=:schema ORDER BY tablename"),
                                                           {"schema": self.schema})]
                self.assertEqual(len(names), 105)
                payload = {name: connection.execute(text('SELECT coalesce(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text),\'[]\'::jsonb)::text FROM "'
                           + self.schema + '"."' + name + '" r')).scalar() for name in names}
                columns = connection.execute(text("""SELECT table_name,column_name,data_type,is_nullable
                    FROM information_schema.columns WHERE table_schema=:schema ORDER BY table_name,ordinal_position"""),
                    {"schema": self.schema}).all()
                sequences = connection.execute(text("SELECT sequencename,last_value FROM pg_sequences WHERE schemaname=:schema ORDER BY sequencename"),
                    {"schema": self.schema}).all()
            return payload, columns, sequences

        def refuse_downgrade(self):
            before = self.retained_snapshot()
            path = application / "alembic/versions/e8b5a3d1f907_private_profile_visuals.py"
            spec = spec_from_file_location("profile_owned_retention_migration", path)
            migration = module_from_spec(spec)
            spec.loader.exec_module(migration)
            with self.assertRaises(DBAPIError) as rejected:
                with self.engine.begin() as connection:
                    migration.op = Operations(MigrationContext.configure(connection))
                    migration.downgrade()
            self.assertEqual(getattr(rejected.exception.orig, "pgcode", None), "23000")
            self.assertEqual(self.retained_snapshot(), before)
            with self.engine.connect() as connection:
                self.assertEqual(connection.execute(text("""SELECT count(*) FROM information_schema.columns
                    WHERE table_schema=:schema AND ((table_name='social_network_profiles' AND column_name IN ('avatar_media_id','cover_media_id'))
                    OR (table_name='network_media' AND column_name IN ('profile_user_id','profile_kind')))"""),
                    {"schema": self.schema}).scalar(), 4)

        def test_selected_visual_prevents_schema_downgrade_without_data_loss(self):
            image = self.profile_photo(kind="avatar")
            self.profile(avatar_media_id=image["id"])
            self.refuse_downgrade()

        def test_unselected_visual_prevents_schema_downgrade_without_data_loss(self):
            self.profile_photo(kind="cover")
            with self.engine.connect() as connection:
                self.assertEqual(connection.execute(text("SELECT count(*) FROM social_network_profiles")).scalar(), 0)
                self.assertEqual(connection.execute(text("SELECT count(*) FROM network_media WHERE profile_user_id IS NOT NULL AND profile_kind='cover'")).scalar(), 1)
            self.refuse_downgrade()

    return ProfileRetentionTests


def run():
    global STAGE, INBOX
    logging.disable(logging.CRITICAL)
    import importlib.metadata as metadata
    baseline = json.loads(Path("/opt/inbox-native/baseline-inventory.json").read_text())
    actual = {re.sub(r"[-_.]+", "-", item.metadata["Name"].lower()): item.version for item in metadata.distributions()}
    require(actual == {**baseline, "openpyxl": "3.1.5", "defusedxml": "0.7.1", "et-xmlfile": "2.0.0"},
            "unchanged_dependency_inventory_required")
    inherited = load_pinned(INHERITED, INHERITED_SHA, "profile_inherited_native_helpers")
    inherited.COMMIT, inherited.TREE, inherited.ATTEMPTS = COMMIT, TREE, ATTEMPTS
    INBOX = inherited.INBOX
    base = inherited.helpers()
    application = base.verified_checkout()
    source_at(base, Path("/baseline106"), BASE_COMMIT, BASE_TREE)
    database, url = base.claim_database()
    base.configure(application, url)
    os.environ["ALLOWED_ORIGINS"] = "http://localhost:5174"
    catalog, retained = base.frozen_helpers()
    server_start = catalog.query(database, "SELECT pg_postmaster_start_time()")
    legacy = tuple(sorted(catalog.OLD_TABLES + inherited.GROUPS))
    full_tables = tuple(sorted(legacy + INBOX))
    require(len(legacy) == 99 and len(full_tables) == 106 and "alembic_version" in legacy,
            "exact99_to106_table_inventory_required")
    import httpx, requests, resend
    captured = StringIO()
    with (patch.object(httpx.HTTPTransport, "handle_request", refuse),
          patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse),
          patch.object(requests.Session, "send", refuse), patch.object(urllib_request, "urlopen", refuse),
          patch.object(resend.Emails, "send", refuse), patch.object(smtplib.SMTP, "connect", refuse),
          patch.object(smtplib.SMTP_SSL, "connect", refuse)):
        STAGE = "previous99"
        inherited.previous_start(base, url)
        require(catalog.table_names(database) == legacy, "real_previous99_required")
        version(catalog, database, inherited.OLD_HEAD)
        with redirect_stdout(captured), redirect_stderr(captured):
            require(retained.seed_old() == inherited.EXPECTED_SEED, "accepted_retained_seed_required")
        original99, original_schema = catalog.snapshot(database, legacy), catalog.catalog(database, legacy)
        STAGE = "baseline106"
        from database import engine
        from sqlalchemy import text
        from alembic import command
        from alembic.config import Config
        with engine.begin() as connection:
            connection.execute(text("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon,authenticated,service_role"))
        baseline_upgrade(base, url)
        require(catalog.table_names(database) == full_tables, "real106_baseline_required")
        version(catalog, database, OLD_HEAD)
        inherited.old_preserved(catalog, database, legacy, original99, original_schema)
        inherited.privacy(catalog, database)
        # Original old99 is proven here, BEFORE the declared two-row fixture
        # extension. All later data proof compares the seeded d8 baseline.
        STAGE = "seed106"
        seed_visual_sentinels(catalog, database, retained)
        schema106 = catalog.catalog(database, full_tables)
        data106 = data_baseline(catalog, database, full_tables, schema106)
        auxiliary106 = auxiliary_metadata(catalog, database)
        STAGE = "upgrade_visuals"
        with redirect_stdout(captured), redirect_stderr(captured):
            command.upgrade(Config("alembic.ini"), "head")
        version(catalog, database, HEAD)
        require(catalog.table_names(database) == full_tables, "same106_catalog_tables_required")
        upgraded = ddl_delta(catalog, database, full_tables, schema106)
        preserve_data(catalog, database, data106, True)
        preserve_auxiliary(catalog, database, auxiliary106, True)
        STAGE = "profile_model_parity"
        compare_models(catalog, database, full_tables)
        inherited.privacy(catalog, database)
        STAGE = "empty_visual_rollback"
        with redirect_stdout(captured), redirect_stderr(captured):
            command.downgrade(Config("alembic.ini"), OLD_HEAD)
        version(catalog, database, OLD_HEAD)
        require(catalog.table_names(database) == full_tables and catalog.catalog(database, full_tables) == schema106,
                "empty_visual_rollback_catalog_equal_required")
        preserve_data(catalog, database, data106, False)
        preserve_auxiliary(catalog, database, auxiliary106, False)
        with redirect_stdout(captured), redirect_stderr(captured):
            command.upgrade(Config("alembic.ini"), "head")
        version(catalog, database, HEAD)
        require(catalog.catalog(database, full_tables) == upgraded, "empty_visual_reupgrade_catalog_equal_required")
        preserve_data(catalog, database, data106, True)
        preserve_auxiliary(catalog, database, auxiliary106, True)
        final106 = catalog.snapshot(database, full_tables)
        STAGE = "inbox15"
        os.environ["SEARCH_FILTER_TEST_DATABASE_URL"] = url
        sys.path.insert(0, str(application / "tests"))
        import test_native_inbox as inbox
        run_suite(inbox.NativeInboxTests, 15, "inbox", suite_inventory(application, "test_native_inbox.py", "NativeInboxTests", 15))
        STAGE = "safety2"
        safety = inherited.safety_case(inbox)
        run_suite(safety, 2, "safety", {name for name in safety.__dict__ if name.startswith("test_")})
        STAGE = "resilience67"
        for module_name, classname, count, label in (
            ("test_database_connection_options", "DatabaseConnectionOptionsTests", 4, "options"),
            ("test_database_pool_recovery", "DatabasePoolRecoveryTests", 3, "recovery"),
            ("test_lead_delivery", "LeadDeliveryTests", 53, "delivery"),
            ("test_customer_messages", "CustomerMessageTests", 7, "customer_copy")):
            methods = suite_inventory(application, module_name + ".py", classname, count)
            __import__(module_name)
            run_suite(getattr(sys.modules[module_name], classname), count, label, methods)
        STAGE = "owned_handshake"
        handshake()
        STAGE = "legacy10"
        nonce = os.environ["ILDR_BROWSER_CI_NONCE"]
        legacy_db = "ildr_platform_test_20261025"
        require(catalog.query(legacy_db, "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()")
                == [("ildr-inbox-legacy-ci:" + nonce,)], "owned_legacy_database_marker_required")
        os.environ["SEARCH_FILTER_TEST_DATABASE_URL"] = "postgresql+psycopg2://postgres@127.0.0.1:55449/" + legacy_db
        import test_service_conversation as award
        import test_service_negotiation as negotiation
        for filename, classname, count, module, label in (
            ("test_service_conversation.py", "PrivateAwardedServiceConversation", 5, award, "legacy_award"),
            ("test_service_negotiation.py", "PrivateServiceNegotiationConversation", 5, negotiation, "legacy_negotiation")):
            run_suite(getattr(module, classname), count, label, suite_inventory(application, filename, classname, count))
        STAGE = "profile30"
        import test_property_network as routes
        allowed = {"PropertyNetworkRoutes", "PrivateImageContracts"}
        run_suite(routes.PropertyNetworkRoutes, 30, "profile",
                  suite_inventory(application, "test_property_network.py", "PropertyNetworkRoutes", 30, allowed))
        STAGE = "private_images4"
        run_suite(routes.PrivateImageContracts, 4, "private_images",
                  suite_inventory(application, "test_property_network.py", "PrivateImageContracts", 4, allowed))
        STAGE = "profile_inputs3"
        import test_profile_visual_inputs as inputs
        run_suite(inputs.ProfileVisualInputs, 3, "profile_inputs",
                  suite_inventory(application, "test_profile_visual_inputs.py", "ProfileVisualInputs", 3))
        STAGE = "retention2"
        retention = retention_cases(routes, application)
        run_suite(retention, 2, "retention", {name for name in retention.__dict__ if name.startswith("test_")})
        STAGE = "preservation"
        require(catalog.table_names(database) == full_tables, "all106_tables_retained_required")
        version(catalog, database, HEAD)
        preserve_data(catalog, database, data106, True)
        preserve_auxiliary(catalog, database, auxiliary106, True)
        require(catalog.catalog(database, full_tables) == upgraded and catalog.snapshot(database, full_tables) == final106,
                "seeded106_final_rows_sequences_catalog_equal_required")
        ddl_delta(catalog, database, full_tables, schema106)
        inherited.privacy(catalog, database)
        require(all(catalog.query(database, "SELECT count(*) FROM public." + name) == [(0,)] for name in INBOX),
                "canonical_inbox_unmodified_required")
        require(catalog.query(database, "SELECT nspname FROM pg_namespace WHERE nspname LIKE %s OR nspname LIKE %s OR nspname LIKE %s OR nspname LIKE %s",
                              ("native_inbox_test_%", "profile_model_%", "pool_recovery_test_%", "lead_delivery_test_%")) == [],
                "canonical_owned_schemas_removed_required")
        require(catalog.query(legacy_db, "SELECT nspname FROM pg_namespace WHERE nspname LIKE %s OR nspname LIKE %s",
                              ("service_test_%", "network_test_%")) == [], "legacy_profile_owned_schemas_removed_required")
        require(catalog.query(database, "SELECT pg_postmaster_start_time()") == server_start,
                "unchanged_postmaster_start_required")
        require(not ATTEMPTS and len(captured.getvalue().encode()) <= 2 * 1024 * 1024,
                "zero_provider_bounded_capture_required")
        base.verified_checkout()
        source_at(base, Path("/baseline106"), BASE_COMMIT, BASE_TREE)
    print("PROFILE_CUSTOMIZATION_NATIVE_RESULT " + canonical(EXPECTED), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        name = type(error).__name__
        cause = getattr(error, "orig", error)
        state = getattr(cause, "pgcode", None)
        print("PROFILE_CUSTOMIZATION_NATIVE_FAILURE " + canonical({"stage": STAGE,
            "error_type": name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name) else "UnhandledError",
            "reason": error.args[0] if isinstance(error, Failure) else "external_failure",
            "sqlstate": state if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state) else "unavailable"}), flush=True)
        raise SystemExit(1) from None
