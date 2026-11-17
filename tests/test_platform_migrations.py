"""Native Alembic chain test; never bootstrap the six tables under test.

Execution is opt-in via SEARCH_FILTER_TEST_DATABASE_URL, which must name an
explicit ildr_platform_test_* database on literal loopback. The parent creates
one UUID-named disposable database, runs the real Alembic env.py in a separate
Python interpreter, and drops only that database in finally. No .env is read,
no server or provider is started, and no production configuration is imported.

Run this suite alone after lifting the local resource hold. A missing local
fixture is a skip; a present but unsafe URL is a failure, never a fallback.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types
import unittest
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
BASELINE = "52b2edc810ad"
HEAD = "c90e75f2a6d1"
DATABASE_PREFIX = "ildr_migration_chain_"
DATABASE_NAME = re.compile(r"ildr_migration_chain_[0-9a-f]{32}\Z")
CHILD_URL_ENV = "ILDR_PLATFORM_MIGRATION_CHILD_URL"
NEW_TABLES = frozenset({
    "lead_delivery_jobs", "lead_delivery_worker_state",
    "listing_location_reviews", "listing_location_review_revisions",
    "property_management_records", "property_management_revisions",
})
LEGACY_TABLES = frozenset({
    "users", "pending_users", "listings", "upgrade_requests", "inquiries",
    "anonymous_leads", "listing_edits", "saved_homes", "bookings",
    "activity_log", "deal_requests", "listing_events", "leads", "settings",
    "bulk_import_jobs", "website_settings", "website_settings_revisions",
    "professional_profiles", "professional_profile_revisions", "research_workspaces",
})


def _loopback_url(raw, *, child=False):
    from sqlalchemy.engine import make_url
    url = make_url(raw)
    if url.get_backend_name() != "postgresql" or url.host not in {"127.0.0.1", "::1"}:
        raise ValueError("Native migration test requires explicit loopback PostgreSQL")
    # Reject alternate transport/service/search-path options that could bypass
    # the inspected host/database. This fixture uses a direct TCP connection.
    if url.query or not url.port or not url.username:
        raise ValueError("Native migration fixture needs an explicit local TCP user/port and no URL options")
    if child:
        if not DATABASE_NAME.fullmatch(url.database or ""):
            raise ValueError("Child may connect only to its UUID-owned migration database")
    elif not re.fullmatch(r"ildr_platform_test_[a-z0-9_]+", url.database or ""):
        raise ValueError("Use a dedicated ildr_platform_test_* loopback fixture")
    return url


def _safe_output(value):
    return re.sub(r"postgres(?:ql)?(?:\+\w+)?://[^\s\"']+", "<local-test-database>", value)[-12000:]


class MigrationSafetyGuardTests(unittest.TestCase):
    def test_only_explicit_dedicated_loopback_urls_are_accepted(self):
        self.assertEqual(_loopback_url("postgresql+psycopg2://postgres@127.0.0.1:55449/ildr_platform_test_synthetic").host, "127.0.0.1")
        for unsafe in (
            "postgresql://postgres@db.example.invalid:5432/ildr_platform_test_synthetic",
            "postgresql://postgres@localhost:5432/ildr_platform_test_synthetic",
            "postgresql://postgres@127.0.0.1:5432/production",
            "postgresql://postgres@127.0.0.1/ildr_platform_test_synthetic",
            "postgresql://postgres@127.0.0.1:5432/ildr_platform_test_synthetic?host=db.example.invalid",
            "postgresql://postgres@127.0.0.1:5432/ildr_platform_test_synthetic?service=production",
            "sqlite+pysqlite:///:memory:",
        ):
            with self.subTest(url_kind=unsafe.split(":", 1)[0]):
                with self.assertRaises(ValueError):
                    _loopback_url(unsafe)
        with self.assertRaises(ValueError):
            _loopback_url("postgresql://postgres@127.0.0.1:55449/ildr_platform_test_synthetic", child=True)


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires dedicated disposable loopback PostgreSQL")
class NativePlatformMigrationTests(unittest.TestCase):
    def test_actual_env_upgrade_repeat_and_history_preserving_downgrade(self):
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import NullPool
        source = _loopback_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        token = uuid4().hex
        name = DATABASE_PREFIX + token
        self.assertIsNotNone(DATABASE_NAME.fullmatch(name))
        owner_marker = "codex-native-migration-test:" + token
        target = source.set(database=name)
        admin = create_engine(source.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool,
                              connect_args={"connect_timeout": 5})
        created = False
        try:
            with admin.connect() as connection:
                # Never use IF NOT EXISTS: this test must own a newly created DB.
                connection.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE template0')
                created = True
                connection.exec_driver_sql(f'COMMENT ON DATABASE "{name}" IS \'{owner_marker}\'')
            child_env = {key: value for key, value in os.environ.items() if key in {
                "PATH", "Path", "SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR",
                "PYTHONPATH", "PYTHONHOME", "LANG", "LC_ALL", "SSL_CERT_FILE",
            }}
            child_env.update({CHILD_URL_ENV: target.render_as_string(hide_password=False), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"})
            with tempfile.TemporaryDirectory(prefix="ildr-migration-run-") as directory:
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--platform-migration-child"],
                                        cwd=directory, env=child_env, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", timeout=180, check=False)
            self.assertEqual(result.returncode, 0, _safe_output(result.stdout + "\n" + result.stderr))
            receipts = [line for line in result.stdout.splitlines() if line.startswith("PLATFORM_MIGRATION_RECEIPT=")]
            self.assertEqual(len(receipts), 1, _safe_output(result.stdout))
            receipt = json.loads(receipts[0].split("=", 1)[1])
            self.assertEqual(receipt["baseline"], BASELINE)
            self.assertEqual(receipt["head"], HEAD)
            self.assertEqual(receipt["new_tables"], sorted(NEW_TABLES))
            self.assertEqual(receipt["legacy_tables_unchanged"], len(LEGACY_TABLES))
            self.assertTrue(receipt["no_backfill"])
            self.assertTrue(receipt["model_parity"])
            self.assertTrue(receipt["rls_and_grants"])
            self.assertTrue(receipt["history_survives_downgrade"])
        finally:
            try:
                if created:
                    # The only destructive target is the exact DB successfully
                    # created above. Never enumerate/drop fixture databases.
                    if not DATABASE_NAME.fullmatch(name) or target.database != name:
                        raise AssertionError("Refusing cleanup outside owned migration DB")
                    with admin.connect() as connection:
                        marker = connection.execute(text("SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=:name"), {"name": name}).scalar_one_or_none()
                        if marker not in (None, owner_marker):
                            raise AssertionError("Owned database marker changed; cleanup refused")
                        connection.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name AND pid<>pg_backend_pid()"), {"name": name})
                        connection.exec_driver_sql(f'DROP DATABASE "{name}"')
            finally:
                admin.dispose()


def _child_run():
    """Runs only in the dedicated child. Imports cannot read deployment settings."""
    raw = os.environ.get(CHILD_URL_ENV, "")
    url = _loopback_url(raw, child=True)
    sys.path.insert(0, str(ROOT))
    synthetic_config = types.ModuleType("config")
    synthetic_config.settings = types.SimpleNamespace(database_url=url.render_as_string(hide_password=False))
    sys.modules["config"] = synthetic_config
    # env.py is the real repository file; only its configuration source is
    # replaced so importing Settings can never read a developer .env or secret.
    from datetime import date, datetime, timezone
    from decimal import Decimal
    from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, inspect, select, text
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import NullPool
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from database import Base, engine as imported_engine
    import models
    from models.lead_delivery import LeadDeliveryJob, LeadDeliveryWorkerState
    from models.listing_location_review import ListingLocationReview, ListingLocationReviewRevision
    from models.property_management import PropertyManagementRecord, PropertyManagementRevision

    engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    case = unittest.TestCase()
    check = case.assertEqual
    metadata = Base.metadata
    ids = {key: uuid4() for key in ("user", "listing", "lead", "inquiry", "profile", "profile_revision", "research", "job", "task", "next_task", "task_revision", "next_revision")}

    def snapshot(tables):
        value = {}
        with engine.connect() as connection:
            for table in sorted(tables):
                if not re.fullmatch(r"[a-z_]+", table):
                    raise AssertionError("Unexpected fixture table")
                row = connection.exec_driver_sql(f'SELECT count(*) AS n, COALESCE(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text), \'[]\'::jsonb)::text AS body FROM "{table}" r').one()
                value[table] = (row.n, hashlib.sha256(row.body.encode("utf-8")).hexdigest())
        return value

    def private_profile_unchanged():
        with engine.connect() as connection:
            row = connection.execute(text("SELECT user_id, review_status, listing_status, published_data, published_at, draft_data->'public_contact_consent' AS consent FROM professional_profiles WHERE id=:id"), {"id": ids["profile"]}).mappings().one()
        check(dict(row), {"user_id": None, "review_status": "draft", "listing_status": "hidden", "published_data": None, "published_at": None, "consent": False})

    def version(expected):
        with engine.connect() as connection:
            check(connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalars().all(), [expected])

    def normalize_default(value):
        if value is None:
            return None
        value = re.sub(r"::(?:text|integer|boolean|numeric)", "", str(value).lower()).strip()
        return value.strip("'\"")

    def fk_signature(fk):
        return (tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]), (fk.get("options", {}).get("ondelete") or "NO ACTION").upper())

    def verify_schema_and_models():
        # Reuse one inspected connection. Inspecting a NullPool Engine opens a
        # new PostgreSQL backend per reflection method, which adds needless
        # process/memory pressure on this small Windows test computer.
        with engine.connect() as connection:
            verify_reflected_schema(inspect(connection))

    def verify_reflected_schema(inspector):
        check(set(inspector.get_table_names()), LEGACY_TABLES | NEW_TABLES | {"alembic_version"})
        for table_name in sorted(NEW_TABLES):
            expected = metadata.tables[table_name]
            actual = {column["name"]: column for column in inspector.get_columns(table_name)}
            check(set(actual), set(expected.columns.keys()), table_name + " columns")
            for column in expected.columns:
                reflected = actual[column.name]
                check(str(reflected["type"].compile(dialect=engine.dialect)).upper(), str(column.type.compile(dialect=engine.dialect)).upper(), table_name + "." + column.name + " type")
                check(reflected["nullable"], column.nullable, table_name + "." + column.name + " nullable")
                default = column.server_default.arg if column.server_default is not None else None
                check(normalize_default(reflected.get("default")), normalize_default(default), table_name + "." + column.name + " default")
            check(tuple(inspector.get_pk_constraint(table_name)["constrained_columns"]), tuple(column.name for column in expected.primary_key.columns), table_name + " primary key")
            expected_fks = {(tuple(column.parent.name for column in fk.elements), fk.referred_table.name, tuple(column.column.name for column in fk.elements), (fk.ondelete or "NO ACTION").upper()) for fk in expected.foreign_key_constraints}
            check({fk_signature(fk) for fk in inspector.get_foreign_keys(table_name)}, expected_fks, table_name + " foreign keys")
            model_checks = {constraint.name for constraint in expected.constraints if isinstance(constraint, CheckConstraint)}
            reflected_checks = {constraint["name"]: constraint for constraint in inspector.get_check_constraints(table_name)}
            check(set(reflected_checks), model_checks, table_name + " checks")
            for constraint in expected.constraints:
                if isinstance(constraint, CheckConstraint):
                    # PostgreSQL rewrites IN into ANY(ARRAY[...]); compare the
                    # actual allowed literals independently of that formatting.
                    reflected_sql = reflected_checks[constraint.name]["sqltext"]
                    model_sql = str(constraint.sqltext)
                    numeric = r"-?\d+(?:\.\d+)?"
                    strings = lambda sql: {value for value in re.findall(r"'([^']*)'", sql) if not re.fullmatch(numeric, value)}
                    check(strings(reflected_sql), strings(model_sql), table_name + " " + constraint.name + " literals")
                    # PostgreSQL may quote a negative numeric bound when
                    # deparsing a CHECK. Compare bounds separately, so ignoring
                    # that representation cannot hide changed limits.
                    bounds = lambda sql: sorted(Decimal(value) for value in re.findall(r"(?<![\w])" + numeric + r"(?![\w])", sql))
                    check(bounds(reflected_sql), bounds(model_sql), table_name + " " + constraint.name + " numeric bounds")
            model_unique = {(constraint.name, tuple(column.name for column in constraint.columns)) for constraint in expected.constraints if isinstance(constraint, UniqueConstraint)}
            check({(constraint["name"], tuple(constraint["column_names"])) for constraint in inspector.get_unique_constraints(table_name)}, model_unique, table_name + " unique constraints")
        required_indexes = {
            "lead_delivery_jobs": {"ix_lead_delivery_due": (False, ["status", "next_attempt_at"]), "ix_lead_delivery_lead": (False, ["lead_id", "created_at"])},
            "listing_location_reviews": {"ix_location_review_status": (False, ["review_status", "listing_id"])},
            "property_management_records": {"ix_management_listing_kind_due": (False, ["listing_id", "kind", "due_date"]), "uq_management_recurring_origin": (True, ["origin_record_id"])},
        }
        for table_name, indexes in required_indexes.items():
            actual = {index["name"]: index for index in inspector.get_indexes(table_name)}
            for name, (unique, columns) in indexes.items():
                case.assertIn(name, actual)
                check(bool(actual[name]["unique"]), unique)
                check(actual[name]["column_names"], columns)
                if name == "uq_management_recurring_origin":
                    predicate = str(actual[name].get("dialect_options", {}).get("postgresql_where", "")).lower()
                    case.assertIn("task", predicate); case.assertIn("origin_record_id is not null", predicate)

    def verify_privacy():
        with engine.connect() as connection:
            roles = connection.execute(text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated') ORDER BY rolname")).scalars().all()
            for table_name in sorted(NEW_TABLES):
                security = connection.execute(text("SELECT relrowsecurity FROM pg_class WHERE oid=CAST(:table AS regclass)"), {"table": "public." + table_name}).scalar_one()
                check(security, True, table_name + " RLS")
                public_grants = connection.execute(text("SELECT count(*) FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl, acldefault('r',c.relowner))) a WHERE c.oid=CAST(:table AS regclass) AND a.grantee=0"), {"table": "public." + table_name}).scalar_one()
                check(public_grants, 0, table_name + " PUBLIC grants")
                check(connection.execute(text("SELECT count(*) FROM pg_policies WHERE schemaname='public' AND tablename=:table"), {"table": table_name}).scalar_one(), 0, table_name + " unexpected API policy")
                for role in roles:
                    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                        check(connection.execute(text("SELECT has_table_privilege(:role,:table,:privilege)"), {"role": role, "table": "public." + table_name, "privilege": privilege}).scalar_one(), False, table_name + " " + role + " " + privilege)
        return roles

    def rejected(sql, params, constraint):
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                with case.assertRaises(IntegrityError) as caught:
                    connection.execute(text(sql), params)
                check(getattr(getattr(caught.exception.orig, "diag", None), "constraint_name", None), constraint)
            finally:
                transaction.rollback()

    try:
        check(set(metadata.tables), LEGACY_TABLES | NEW_TABLES, "Update the frozen baseline deliberately when adding a migration")
        # Explicitly exclude ALL six expansion tables. create_all must not hide
        # a missing CREATE TABLE, column, constraint, index or native env failure.
        legacy = [table for table in metadata.sorted_tables if table.name in LEGACY_TABLES]
        metadata.create_all(engine, tables=legacy)
        check(set(inspect(engine).get_table_names()), LEGACY_TABLES)
        with engine.begin() as connection:
            connection.execute(metadata.tables["users"].insert(), {"id": ids["user"], "email": "synthetic-migration@example.invalid", "display_name": "Synthetic migration actor", "role": "admin", "status": "active", "email_verified": True})
            connection.execute(metadata.tables["listings"].insert(), {"id": ids["listing"], "title": "Synthetic legacy property", "type": "apartment", "transaction": "rent", "price": Decimal("1000.25"), "currency": "DOP", "location": "Synthetic test area", "submitted_by": ids["user"], "owner_id": ids["user"], "status": "active", "latitude": Decimal("19.7500000"), "longitude": Decimal("-70.4000000")})
            connection.execute(metadata.tables["leads"].insert(), {"id": ids["lead"], "type": "property_inquiry", "name": "Synthetic old inquiry", "email": None, "phone": "+18095551234", "message": "Synthetic old record; never deliver", "property_id": ids["listing"], "from_user_id": ids["user"], "status": "closed", "tracking": {"internal_test": True, "measurement_consent": "denied"}})
            connection.execute(metadata.tables["inquiries"].insert(), {"id": ids["inquiry"], "listing_id": ids["listing"], "name": "Synthetic legacy inquiry", "email": "synthetic-inquiry@example.invalid", "message": "Synthetic legacy history", "from_user_id": ids["user"]})
            connection.execute(metadata.tables["professional_profiles"].insert(), {"id": ids["profile"], "user_id": None, "version": 1, "draft_data": {"business_name": "Synthetic private professional", "public_contact_consent": False}, "review_status": "draft", "listing_status": "hidden", "published_data": None, "published_at": None})
            connection.execute(metadata.tables["professional_profile_revisions"].insert(), {"id": ids["profile_revision"], "profile_id": ids["profile"], "version": 1, "action": "synthetic_private_import", "snapshot": {"public_contact_consent": False, "listing_status": "hidden"}, "actor_id": ids["user"]})
            connection.execute(metadata.tables["research_workspaces"].insert(), {"id": ids["research"], "user_id": ids["user"], "title": "Synthetic saved research", "version": 1, "data": {"notes": "Synthetic private notes"}})
        old_rows = snapshot(LEGACY_TABLES)
        private_profile_unchanged()
        cfg = Config(str(ROOT / "alembic.ini"))
        scripts = ScriptDirectory.from_config(cfg)
        check(scripts.get_heads(), [HEAD])
        check([revision.revision for revision in scripts.iterate_revisions(HEAD, BASELINE)], [HEAD, "b12d41e2c9f0", "82e1a7c4d9b0"])
        command.stamp(cfg, BASELINE)  # Real env.py, real dedicated connection.
        version(BASELINE)
        check(set(inspect(engine).get_table_names()), LEGACY_TABLES | {"alembic_version"})
        # Make grants assertions non-vacuous: without migration REVOKEs these
        # new tables would inherit PUBLIC (and existing Data API role) grants.
        # These default privileges are database-local; create no cluster roles.
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO PUBLIC")
            existing_roles = connection.execute(text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")).scalars().all()
            for role in existing_roles:
                connection.exec_driver_sql(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO "{role}"')
        command.upgrade(cfg, "head")
        version(HEAD)
        verify_schema_and_models()
        roles_checked = verify_privacy()
        check(snapshot(LEGACY_TABLES), old_rows)
        private_profile_unchanged()
        check({name: count for name, (count, _) in snapshot(NEW_TABLES).items()}, {name: 0 for name in NEW_TABLES}, "Migration must not queue old leads, start a heartbeat or backfill location/management records")
        command.upgrade(cfg, "head")
        check({name: count for name, (count, _) in snapshot(NEW_TABLES).items()}, {name: 0 for name in NEW_TABLES})

        now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
        with Session(engine) as db:
            db.add(LeadDeliveryJob(id=ids["job"], lead_id=ids["lead"], step="synthetic_history", provider="synthetic", payload={"synthetic_only": True}, payload_sha256="0" * 64, status="accepted", attempt_count=1, accepted_at=now))
            db.add(LeadDeliveryWorkerState(id=1, last_poll_at=now, last_completed_at=now, last_batch_count=1))
            db.add(ListingLocationReview(listing_id=ids["listing"], version=1, review_status="verified", public_precision="hidden", latitude=Decimal("19.7500000"), longitude=Decimal("-70.4000000"), source_type="owner", source_reference="Synthetic evidence", reviewed_by=ids["user"], reviewed_at=now))
            db.flush()
            db.add(ListingLocationReviewRevision(listing_id=ids["listing"], version=1, action="synthetic_history", actor_id=ids["user"], snapshot={"synthetic_only": True, "public_precision": "hidden"}))
            db.add(PropertyManagementRecord(id=ids["task"], listing_id=ids["listing"], kind="task", title="Synthetic recurring task", status="completed", data={"due_date": "2026-10-31", "recurrence": "monthly"}, due_date=date(2026, 10, 31), version=1, created_by=ids["user"]))
            db.flush()
            db.add(PropertyManagementRecord(id=ids["next_task"], listing_id=ids["listing"], kind="task", title="Synthetic next task", status="open", data={"due_date": "2026-11-30", "recurrence": "monthly"}, due_date=date(2026, 11, 30), version=1, origin_record_id=ids["task"], recurrence_anchor_day=31, created_by=ids["user"]))
            db.flush()
            for key, revision_key in (("task", "task_revision"), ("next_task", "next_revision")):
                db.add(PropertyManagementRevision(id=ids[revision_key], record_id=ids[key], version=1, action="synthetic_history", snapshot={"record": {"id": str(ids[key]), "kind": "task", "version": 1}, "comment": "Synthetic retained history"}, actor_id=ids["user"]))
            db.commit()
        # ORM reads against migration-created tables also exercise model column
        # parity. No full-metadata create_all occurs after the baseline bootstrap.
        with Session(engine) as db:
            for model in (LeadDeliveryJob, LeadDeliveryWorkerState, ListingLocationReview, ListingLocationReviewRevision, PropertyManagementRecord, PropertyManagementRevision):
                case.assertGreater(len(db.scalars(select(model)).all()), 0)
        rejected("UPDATE lead_delivery_jobs SET status='invalid' WHERE id=:id", {"id": ids["job"]}, "ck_lead_delivery_status")
        rejected("UPDATE lead_delivery_jobs SET attempt_count=5 WHERE id=:id", {"id": ids["job"]}, "ck_lead_delivery_attempts")
        rejected("UPDATE lead_delivery_jobs SET attempt_count=-1 WHERE id=:id", {"id": ids["job"]}, "ck_lead_delivery_attempts")
        rejected("UPDATE lead_delivery_worker_state SET id=2 WHERE id=1", {}, "ck_lead_delivery_worker_singleton")
        rejected("UPDATE listing_location_reviews SET version=0 WHERE listing_id=:id", {"id": ids["listing"]}, "ck_location_review_version")
        rejected("UPDATE listing_location_reviews SET review_status='invalid' WHERE listing_id=:id", {"id": ids["listing"]}, "ck_location_review_status")
        rejected("UPDATE listing_location_reviews SET public_precision='invalid' WHERE listing_id=:id", {"id": ids["listing"]}, "ck_location_public_precision")
        rejected("UPDATE listing_location_reviews SET latitude=91 WHERE listing_id=:id", {"id": ids["listing"]}, "ck_location_review_coordinates")
        rejected("UPDATE listing_location_reviews SET latitude=-91 WHERE listing_id=:id", {"id": ids["listing"]}, "ck_location_review_coordinates")
        rejected("UPDATE listing_location_reviews SET longitude=181 WHERE listing_id=:id", {"id": ids["listing"]}, "ck_location_review_coordinates")
        rejected("UPDATE listing_location_reviews SET longitude=-181 WHERE listing_id=:id", {"id": ids["listing"]}, "ck_location_review_coordinates")
        rejected("UPDATE listing_location_reviews SET longitude=NULL WHERE listing_id=:id", {"id": ids["listing"]}, "ck_location_review_coordinates")
        rejected("UPDATE listing_location_reviews SET latitude=NULL WHERE listing_id=:id", {"id": ids["listing"]}, "ck_location_review_coordinates")
        rejected("UPDATE listing_location_review_revisions SET version=0 WHERE listing_id=:id", {"id": ids["listing"]}, "ck_location_revision_version")
        rejected("UPDATE property_management_records SET version=0 WHERE id=:id", {"id": ids["task"]}, "ck_management_record_version")
        rejected("UPDATE property_management_records SET kind='invalid' WHERE id=:id", {"id": ids["task"]}, "ck_management_record_kind")
        rejected("INSERT INTO lead_delivery_jobs (id,lead_id,step,provider,payload,payload_sha256) VALUES (:id,:lead,'synthetic_history','synthetic','{}'::jsonb,:digest)", {"id": uuid4(), "lead": ids["lead"], "digest": "0" * 64}, "uq_lead_delivery_step")
        rejected("INSERT INTO property_management_revisions (id,record_id,version,action,snapshot) VALUES (:id,:record,1,'synthetic_duplicate','{}'::jsonb)", {"id": uuid4(), "record": ids["task"]}, "uq_management_revision_version")
        rejected("INSERT INTO property_management_records (id,listing_id,kind,title,status,data,origin_record_id) VALUES (:id,:listing,'task','Synthetic duplicate','open','{}'::jsonb,:origin)", {"id": uuid4(), "listing": ids["listing"], "origin": ids["task"]}, "uq_management_recurring_origin")
        history = snapshot(NEW_TABLES)
        check(snapshot(LEGACY_TABLES), old_rows)
        private_profile_unchanged()
        command.upgrade(cfg, "head")
        version(HEAD)
        check(snapshot(NEW_TABLES), history)
        command.downgrade(cfg, BASELINE)
        version(BASELINE)
        check(snapshot(NEW_TABLES), history, "Downgrade must retain delivery, location and management evidence")
        check(snapshot(LEGACY_TABLES), old_rows)
        private_profile_unchanged()
        verify_privacy()
        command.upgrade(cfg, "head")
        version(HEAD)
        verify_schema_and_models()
        verify_privacy()
        check(snapshot(NEW_TABLES), history)
        check(snapshot(LEGACY_TABLES), old_rows)
        private_profile_unchanged()
        print("PLATFORM_MIGRATION_RECEIPT=" + json.dumps({"baseline": BASELINE, "head": HEAD, "new_tables": sorted(NEW_TABLES), "legacy_tables_unchanged": len(LEGACY_TABLES), "no_backfill": True, "model_parity": True, "rls_and_grants": True, "existing_data_api_roles_checked": roles_checked, "history_survives_downgrade": True}, sort_keys=True))
    finally:
        engine.dispose()
        imported_engine.dispose()


if __name__ == "__main__":
    if sys.argv[1:] == ["--platform-migration-child"]:
        _child_run()
    else:
        unittest.main()
