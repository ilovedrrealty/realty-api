"""Actual registry revision contracts in one disposable owned schema.

This is targeted fresh/legacy-revision coverage, not the full Alembic-chain gate.
"""
import importlib.util
import os
import re
import unittest
from pathlib import Path
from uuid import uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-registry-migration-only",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
                  SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
import models
from database import Base
from models.user import User
from models.listing import Listing


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class PrivateRegistryMigration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host != "127.0.0.1" or url.port != 55449 or url.database != "ildr_platform_test_20261025" or url.get_backend_name() != "postgresql":
            raise ValueError("Use the fixed disposable loopback Postgres only")
        cls.schema = "registry_migration_test_" + uuid4().hex
        cls.bootstrap = create_engine(url)
        cls.addClassCleanup(cls.cleanup_owned_schema)
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema + " -clock_timeout=5000 -cstatement_timeout=10000"})
        cls.revisions = {}
        for filename in ("d1e3f7a9b521_private_property_network.py", "f3a5b9c1d723_private_property_registry.py"):
            path = Path(__file__).resolve().parents[1] / "alembic/versions" / filename
            spec = importlib.util.spec_from_file_location("registry_revision_" + filename[:-3], path)
            revision = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(revision)
            cls.revisions[revision.revision] = revision

    @classmethod
    def cleanup_owned_schema(cls):
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        assert re.fullmatch(r"registry_migration_test_[0-9a-f]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE'))
        finally:
            cls.bootstrap.dispose()

    def setUp(self):
        self.reset_owned_schema()

    def reset_owned_schema(self):
        # The same controlled nonce is reused sequentially, never another schema.
        assert re.fullmatch(r"registry_migration_test_[0-9a-f]{32}", self.schema)
        with self.bootstrap.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))

    def revision(self, name, direction="upgrade"):
        module = self.revisions[name]
        with self.engine.begin() as connection:
            module.op = Operations(MigrationContext.configure(connection))
            getattr(module, direction)()

    def build_schema(self, fresh):
        if fresh:
            Base.metadata.create_all(self.engine)
        else:
            Base.metadata.create_all(self.engine, tables=[User.__table__, Listing.__table__])
        self.revision("d1e3f7a9b521")
        if fresh:
            self.revision("f3a5b9c1d723")

    def seed_legacy_project(self):
        owner, listing, project = uuid4(), uuid4(), uuid4()
        with Session(self.engine) as db:
            db.add(User(id=owner, user_code=9001, email="registry-migration@example.com", display_name="Synthetic custodian",
                        role="owner", status="active", email_verified=True))
            db.flush()
            db.add(Listing(id=listing, title="Synthetic legacy listing", type="villa", transaction="sale", price=10,
                           location="Synthetic", submitted_by=owner, owner_id=owner, status="active"))
            db.flush()
            db.execute(text("""INSERT INTO network_projects(id,listing_id,owner_id,title,summary,property_class,service_category_ids,status,version)
                               VALUES(:id,:listing,:owner,'Legacy work','Retained','house_villa','[]'::jsonb,'open',1)"""),
                       {"id": project, "listing": listing, "owner": owner})
            db.commit()
        return owner, listing, project

    def shape(self):
        result = {}
        with self.engine.connect() as connection:
            for table in ("network_properties", "network_projects"):
                columns = connection.execute(text("""SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
                        pg_get_expr(d.adbin,d.adrelid) FROM pg_attribute a
                        LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                        WHERE a.attrelid=to_regclass(:table) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum"""), {"table": table}).all()
                constraints = connection.execute(text("""SELECT conname,contype,pg_get_constraintdef(oid)
                        FROM pg_constraint WHERE conrelid=to_regclass(:table) ORDER BY conname"""), {"table": table}).all()
                indexes = connection.execute(text("""SELECT indexname,indexdef FROM pg_indexes
                        WHERE schemaname=current_schema() AND tablename=:table ORDER BY indexname"""), {"table": table}).all()
                result[table] = {"columns": [tuple(r) for r in columns], "constraints": [tuple(r) for r in constraints],
                                 "indexes": [tuple(r) for r in indexes]}
        return result

    def insert_property(self, custodian, **changes):
        values = {"id": uuid4(), "owner": custodian, "submission": uuid4(), "title": "Synthetic private record", "location": "Synthetic area",
                  "address": "", "class": "house_villa", "relationship": "owner", "notes": "", "status": "active", "version": 1,
                  "hash": "a" * 64, "authority": True, **changes}
        with self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO network_properties(id,custodian_id,submission_id,title,location,address,property_class,
                relationship,notes,status,version,creation_sha256,authority_acknowledged)
                VALUES(:id,:owner,:submission,:title,:location,:address,:class,:relationship,:notes,:status,:version,:hash,:authority)"""), values)
        return values

    def test_fresh_and_actual_legacy_upgrade_match_raw_column_order_constraints_and_indexes(self):
        self.build_schema(fresh=True)
        expected = self.shape()
        self.assertEqual([r[0] for r in expected["network_projects"]["columns"]][-4:], ["property_id", "created_by_id", "submission_id", "creation_sha256"])
        self.reset_owned_schema()
        self.build_schema(fresh=False)
        owner, listing, project = self.seed_legacy_project()
        self.revision("f3a5b9c1d723")
        self.assertEqual(self.shape(), expected)  # Actual ordinal order is intentionally NOT normalized away.
        with self.engine.connect() as connection:
            row = connection.execute(text("SELECT listing_id,owner_id,title,property_id,created_by_id,submission_id,creation_sha256 FROM network_projects WHERE id=:id"), {"id": project}).one()
        self.assertEqual(tuple(row), (listing, owner, "Legacy work", None, None, None, None))
        before = self.shape()
        self.revision("f3a5b9c1d723")  # Bootstrap create_all and repeated conditional DDL are both supported.
        self.assertEqual(self.shape(), before)

    def test_rls_direct_privileges_and_trigger_functions_stay_private_invoker_only(self):
        self.build_schema(fresh=True)
        # The reviewed controller supplies these two roles only inside its
        # captured, isolated CI cluster. Tests never create/alter global roles.
        with self.engine.begin() as connection:
            roles = connection.execute(text("""SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,
                rolinherit,rolreplication,rolbypassrls FROM pg_roles
                WHERE rolname IN ('anon','authenticated') ORDER BY rolname""")).all()
            self.assertEqual([tuple(row) for row in roles], [
                ("anon", False, False, False, False, False, False, False),
                ("authenticated", False, False, False, False, False, False, False)])
            assert re.fullmatch(r"registry_migration_test_[0-9a-f]{32}", self.schema)
            connection.execute(text(f'GRANT ALL ON TABLE "{self.schema}".network_properties TO anon, authenticated'))
            for role in ("anon", "authenticated"):
                self.assertTrue(connection.execute(text("SELECT has_table_privilege(:role,'network_properties','SELECT')"),
                                                   {"role": role}).scalar_one())
        # Removing either role-specific REVOKE from the actual revision now
        # leaves a seeded privilege behind and fails the assertions below.
        self.revision("f3a5b9c1d723")
        with self.engine.connect() as connection:
            self.assertTrue(connection.execute(text("SELECT relrowsecurity FROM pg_class WHERE oid='network_properties'::regclass")).scalar_one())
            self.assertEqual(connection.execute(text("""SELECT count(*) FROM pg_policy WHERE polrelid='network_properties'::regclass""")).scalar_one(), 0)
            self.assertEqual(connection.execute(text("""SELECT count(*) FROM pg_class c,
                LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
                WHERE c.oid='network_properties'::regclass AND a.grantee=0""")).scalar_one(), 0)
            for role in ("anon", "authenticated"):
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                    self.assertFalse(connection.execute(text("SELECT has_table_privilege(:role,'network_properties',:privilege)"), {"role": role, "privilege": privilege}).scalar_one())
            functions = connection.execute(text("""SELECT p.proname,p.prosecdef,
                EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee=0)
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname=current_schema() AND p.proname IN ('network_guard_registry_property','network_guard_project_creation_identity') ORDER BY p.proname""")).all()
            self.assertEqual([tuple(r) for r in functions], [("network_guard_project_creation_identity", False, False), ("network_guard_registry_property", False, False)])

    def test_database_xor_unique_foreign_keys_bounds_and_creation_identities_refuse_invalid_changes(self):
        self.build_schema(fresh=False)
        owner, listing, project = self.seed_legacy_project()
        self.revision("f3a5b9c1d723")
        private = self.insert_property(owner)
        invalid = ({"authority": False}, {"class": "invented"}, {"version": 0}, {"status": "public"},
                   {"relationship": "viewer"}, {"location": ""}, {"address": "x" * 501}, {"hash": "invalid"},
                   {"owner": uuid4()}, {"submission": private["submission"]})
        for change in invalid:
            with self.subTest(fields=tuple(change)), self.assertRaises(DBAPIError):
                self.insert_property(owner, **change)
        for statement, values in (
            ("UPDATE network_properties SET custodian_id=:value WHERE id=:id", {"value": uuid4(), "id": private["id"]}),
            ("UPDATE network_properties SET creation_sha256=:value WHERE id=:id", {"value": "b" * 64, "id": private["id"]}),
            ("DELETE FROM network_properties WHERE id=:id", {"id": private["id"]}),
            ("UPDATE network_projects SET listing_id=NULL WHERE id=:id", {"id": project}),
            ("UPDATE network_projects SET property_id=:value WHERE id=:id", {"value": private["id"], "id": project}),
            ("DELETE FROM network_projects WHERE id=:id", {"id": project}),
        ):
            with self.subTest(statement=statement), self.assertRaises(DBAPIError), self.engine.begin() as connection:
                connection.execute(text(statement), values)
        # INSERT exercises CHECK/FK rules independently of the immutable UPDATE trigger.
        insert = """INSERT INTO network_projects(id,listing_id,property_id,owner_id,title,summary,property_class,service_category_ids,status,version,
                   created_by_id,submission_id,creation_sha256) VALUES(:id,:listing,:property,:owner,'Synthetic','', 'house_villa','[]'::jsonb,'open',1,:actor,:submission,:hash)"""
        base = {"listing": None, "property": private["id"], "owner": owner, "actor": owner, "submission": uuid4(), "hash": "c" * 64}
        for change in ({"listing": listing}, {"property": None}, {"property": uuid4()}, {"submission": None}, {"hash": None}):
            with self.subTest(fields=tuple(change)), self.assertRaises(DBAPIError), self.engine.begin() as connection:
                connection.execute(text(insert), {**base, **change, "id": uuid4()})
        with self.engine.begin() as connection:
            connection.execute(text(insert), {**base, "id": uuid4()})
        with self.assertRaises(DBAPIError), self.engine.begin() as connection:
            connection.execute(text(insert), {**base, "id": uuid4()})

    def test_downgrade_retains_legacy_rows_reupgrade_matches_and_populated_registry_refuses_loss(self):
        self.build_schema(fresh=False)
        owner, listing, project = self.seed_legacy_project()
        self.revision("f3a5b9c1d723")
        shape = self.shape()
        self.revision("f3a5b9c1d723", "downgrade")
        with self.engine.connect() as connection:
            self.assertIsNone(connection.execute(text("SELECT to_regclass('network_properties')")).scalar_one())
            self.assertEqual(tuple(connection.execute(text("SELECT listing_id,owner_id,title FROM network_projects WHERE id=:id"), {"id": project}).one()), (listing, owner, "Legacy work"))
        self.revision("f3a5b9c1d723")
        self.assertEqual(self.shape(), shape)
        self.insert_property(owner)
        with self.assertRaises(DBAPIError):
            self.revision("f3a5b9c1d723", "downgrade")
        self.assertEqual(self.shape(), shape)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT count(*) FROM network_properties")).scalar_one(), 1)
            self.assertEqual(connection.execute(text("SELECT count(*) FROM network_projects")).scalar_one(), 1)

    def test_downgrade_also_refuses_listing_creation_keys_without_any_registry_row(self):
        self.build_schema(fresh=False)
        owner, listing, _ = self.seed_legacy_project()
        self.revision("f3a5b9c1d723")
        with self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO network_projects(id,listing_id,owner_id,title,summary,property_class,service_category_ids,status,version,
                created_by_id,submission_id,creation_sha256) VALUES(:id,:listing,:owner,'Keyed legacy','', 'house_villa','[]'::jsonb,'open',1,:owner,:key,:hash)"""),
                {"id": uuid4(), "listing": listing, "owner": owner, "key": uuid4(), "hash": "d" * 64})
        with self.assertRaises(DBAPIError):
            self.revision("f3a5b9c1d723", "downgrade")
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT count(*) FROM network_properties")).scalar_one(), 0)
            self.assertEqual(connection.execute(text("SELECT count(*) FROM network_projects")).scalar_one(), 2)
