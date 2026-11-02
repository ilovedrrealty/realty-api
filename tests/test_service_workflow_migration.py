"""Targeted real service revision DDL/retention contracts, not full-chain acceptance."""
import importlib.util
import os
import re
import unittest
from pathlib import Path
from uuid import uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-service-migration-only",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
                  LEAD_DELIVERY_WORKER_ENABLED="false", CUSTOMER_MESSAGES_ENABLED="false",
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
from models.property_network import NetworkProperty
from models.service_workflow import (NetworkServicePreference as Preference, NetworkServiceRequest as ServiceRequest,
    NetworkServiceRecipient as Recipient, NetworkServiceProposal as Proposal, NetworkServiceAction as Action)

TABLES = ("network_service_preferences", "network_service_requests", "network_service_recipients",
          "network_service_proposals", "network_service_actions")


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class PrivateServiceMigration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host != "127.0.0.1" or url.port != 55449 or url.database != "ildr_platform_test_20261025" or url.get_backend_name() != "postgresql":
            raise ValueError("Use the fixed disposable loopback Postgres only")
        cls.schema = "service_migration_test_" + uuid4().hex
        cls.bootstrap = create_engine(url)
        cls.addClassCleanup(cls.cleanup_owned_schema)
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema + " -clock_timeout=5000 -cstatement_timeout=10000"})
        cls.revisions = {}
        for filename in ("d1e3f7a9b521_private_property_network.py", "f3a5b9c1d723_private_property_registry.py", "a4b6c0d2e834_private_service_workflow.py"):
            path = Path(__file__).resolve().parents[1] / "alembic/versions" / filename
            spec = importlib.util.spec_from_file_location("service_revision_" + filename[:-3], path)
            revision = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(revision)
            cls.revisions[revision.revision] = revision

    @classmethod
    def cleanup_owned_schema(cls):
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        assert re.fullmatch(r"service_migration_test_[0-9a-f]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE'))
        finally:
            cls.bootstrap.dispose()

    def setUp(self):
        self.reset_owned_schema()

    def reset_owned_schema(self):
        assert re.fullmatch(r"service_migration_test_[0-9a-f]{32}", self.schema)
        with self.bootstrap.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))

    def revision(self, name="a4b6c0d2e834", direction="upgrade"):
        revision = self.revisions[name]
        with self.engine.begin() as connection:
            revision.op = Operations(MigrationContext.configure(connection))
            getattr(revision, direction)()

    def build(self, fresh=True):
        Base.metadata.create_all(self.engine, **({} if fresh else {"tables": [User.__table__, Listing.__table__]}))
        self.revision("d1e3f7a9b521")
        self.revision("f3a5b9c1d723")
        if fresh:
            self.revision()

    def shape(self):
        result = {}
        with self.engine.connect() as connection:
            for table in TABLES:
                columns = connection.execute(text("""SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid)
                    FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                    WHERE a.attrelid=to_regclass(:table) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum"""), {"table": table}).all()
                constraints = connection.execute(text("SELECT conname,contype,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=to_regclass(:table) ORDER BY conname"), {"table": table}).all()
                indexes = connection.execute(text("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname=current_schema() AND tablename=:table ORDER BY indexname"), {"table": table}).all()
                result[table] = {"columns": [tuple(r) for r in columns], "constraints": [tuple(r) for r in constraints], "indexes": [tuple(r) for r in indexes]}
        return result

    def seed(self):
        from datetime import date
        with Session(self.engine) as db:
            owner, provider = uuid4(), uuid4()
            for identity, code in ((owner, 8001), (provider, 8002)):
                db.add(User(id=identity, user_code=code, email=f"{code}@example.com", display_name="Synthetic member",
                    role="owner" if identity == owner else "buyer", status="active", email_verified=True))
            db.flush()
            private = NetworkProperty(custodian_id=owner, submission_id=uuid4(), title="Private synthetic", location="Synthetic",
                address="", property_class="house_villa", relationship="owner", notes="", status="active", version=1,
                creation_sha256="a" * 64, authority_acknowledged=True)
            db.add(private); db.flush()
            preference = Preference(user_id=provider, service_opt_in=True, member_alias="Synthetic provider",
                category_ids=["pool_service"], property_class_ids=["house_villa"], version=1)
            request = ServiceRequest(property_id=private.id, custodian_id=owner, requester_id=owner, category_id="pool_service",
                property_class="house_villa", title="Work", brief="Shared scope", area_label="Coarse", requester_alias="Custodian",
                recipient_user_codes=[8002], scope_sha256="b" * 64, status="open", version=1)
            db.add_all([preference, request]); db.flush()
            recipient = Recipient(request_id=request.id, provider_id=provider, user_code=8002, member_alias="Synthetic provider", status="accepted", version=1)
            db.add(recipient); db.flush()
            proposal = Proposal(request_id=request.id, recipient_id=recipient.id, author_id=provider, revision_no=1,
                kind="provider_quote", status="submitted", version=1, amount="10.01", currency="USD", pricing_basis="fixed",
                scope_text="Work", exclusions="", valid_until=date(2030, 1, 1))
            db.add(proposal); db.flush()
            recipient.current_proposal_id = proposal.id; recipient.version += 1
            action = Action(actor_id=provider, submission_id=uuid4(), operation="provider_quote", target_id=recipient.id,
                request_id=request.id, recipient_id=recipient.id, proposal_id=proposal.id, creation_sha256="c" * 64,
                result_versions={"recipient_version": 2, "proposal_version": 1})
            db.add(action); db.commit()
            return {"owner": owner, "provider": provider, "property": private.id, "request": request.id,
                "recipient": recipient.id, "proposal": proposal.id, "action": action.id}

    def test_fresh_and_real_prior_revision_upgrade_have_identical_raw_five_table_shape(self):
        self.build()
        expected = self.shape()
        self.assertEqual(len(expected), 5)
        self.reset_owned_schema()
        self.build(fresh=False)
        with self.engine.connect() as connection:
            self.assertTrue(all(connection.execute(text("SELECT to_regclass(:name)"), {"name": name}).scalar_one() is None for name in TABLES))
        self.revision()
        self.assertEqual(self.shape(), expected)  # No sorted-column normalization.
        self.revision()
        self.assertEqual(self.shape(), expected)

    def test_all_five_rls_and_role_revokes_are_nonvacuous_and_functions_are_invoker_only(self):
        self.build()
        with self.engine.begin() as connection:
            roles = connection.execute(text("""SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolreplication,rolbypassrls
                FROM pg_roles WHERE rolname IN ('anon','authenticated') ORDER BY rolname""")).all()
            self.assertEqual([tuple(r) for r in roles], [("anon", False, False, False, False, False, False, False),
                ("authenticated", False, False, False, False, False, False, False)])
            for table in TABLES:
                connection.execute(text(f'GRANT ALL ON "{self.schema}"."{table}" TO anon,authenticated'))
                for role in ("anon", "authenticated"):
                    self.assertTrue(connection.execute(text("SELECT has_table_privilege(:role,:table,'SELECT')"), {"role": role, "table": table}).scalar_one())
        self.revision()
        with self.engine.connect() as connection:
            for table in TABLES:
                self.assertTrue(connection.execute(text("SELECT relrowsecurity FROM pg_class WHERE oid=to_regclass(:table)"), {"table": table}).scalar_one())
                self.assertEqual(connection.execute(text("SELECT count(*) FROM pg_policy WHERE polrelid=to_regclass(:table)"), {"table": table}).scalar_one(), 0)
                for role in ("anon", "authenticated"):
                    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                        self.assertFalse(connection.execute(text("SELECT has_table_privilege(:role,:table,:privilege)"), {"role": role, "table": table, "privilege": privilege}).scalar_one())
            functions = connection.execute(text("""SELECT p.proname,p.prosecdef,
                EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee=0)
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=current_schema()
                AND p.proname LIKE 'network_guard_service_%' ORDER BY p.proname""")).all()
            self.assertEqual([tuple(r) for r in functions], [("network_guard_service_catalog", False, False),
                ("network_guard_service_identity", False, False), ("network_guard_service_links", False, False)])

    def test_database_immutable_source_terms_actions_and_all_five_delete_guards(self):
        self.build(); ids = self.seed()
        attempts = (
            ("UPDATE network_service_preferences SET user_id=:value,version=version+1 WHERE user_id=:id", ids["provider"], ids["owner"]),
            ("UPDATE network_service_requests SET brief='Different',version=version+1 WHERE id=:id", ids["request"], None),
            ("UPDATE network_service_recipients SET provider_id=:value,version=version+1 WHERE id=:id", ids["recipient"], ids["owner"]),
            ("UPDATE network_service_proposals SET amount=20,version=version+1 WHERE id=:id", ids["proposal"], None),
            ("UPDATE network_service_actions SET creation_sha256=:value WHERE id=:id", ids["action"], "d" * 64))
        for statement, identity, value in attempts:
            with self.subTest(statement=statement), self.assertRaises(DBAPIError), self.engine.begin() as connection:
                connection.execute(text(statement), {"id": identity, "value": value})
        for table in TABLES:
            with self.subTest(table=table), self.assertRaises(DBAPIError), self.engine.begin() as connection:
                connection.execute(text(f"DELETE FROM {table}"))
        with self.engine.connect() as connection:
            self.assertEqual([connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() for table in TABLES], [1, 1, 1, 1, 1])

    def test_catalog_consent_bounds_unique_and_cross_scope_links_reject_invalid_rows(self):
        self.build(); ids = self.seed()
        # Real foreign-scope rows distinguish composite scope enforcement from
        # an ordinary missing-FK rejection. No extra accounts/schema are used.
        with Session(self.engine) as db:
            other_request = ServiceRequest(property_id=ids["property"], custodian_id=ids["owner"], requester_id=ids["owner"],
                category_id="pool_service", property_class="house_villa", title="Other work", brief="Other scope",
                area_label="", requester_alias="Custodian", recipient_user_codes=[8002], scope_sha256="e" * 64, status="open", version=1)
            db.add(other_request); db.flush()
            other_recipient = Recipient(request_id=other_request.id, provider_id=ids["provider"], user_code=8002,
                member_alias="Synthetic provider", status="accepted", version=1)
            db.add(other_recipient); db.flush()
            other_proposal = Proposal(request_id=other_request.id, recipient_id=other_recipient.id, author_id=ids["provider"],
                revision_no=1, kind="provider_quote", status="submitted", version=1, amount="20.02", currency="USD",
                pricing_basis="fixed", scope_text="Other work", exclusions="", valid_until=db.get(Proposal, ids["proposal"]).valid_until)
            db.add(other_proposal); db.flush()
            other_recipient.current_proposal_id = other_proposal.id; other_recipient.version += 1
            db.commit()
            foreign = {"request": other_request.id, "recipient": other_recipient.id, "proposal": other_proposal.id}
            self.assertNotEqual(foreign["request"], ids["request"])
            self.assertEqual(db.get(Proposal, foreign["proposal"]).recipient_id, foreign["recipient"])
            self.assertEqual(db.get(Recipient, foreign["recipient"]).request_id, foreign["request"])
        invalid_updates = (
            ("UPDATE network_service_preferences SET category_ids='[\"invented\"]',version=version+1 WHERE user_id=:id", ids["provider"]),
            ("UPDATE network_service_preferences SET category_ids='[\"pool_service\",\"pool_service\"]',version=version+1 WHERE user_id=:id", ids["provider"]),
            ("UPDATE network_service_preferences SET member_alias='',version=version+1 WHERE user_id=:id", ids["provider"]),
            ("UPDATE network_service_recipients SET current_proposal_id=:foreign,version=version+1 WHERE id=:id", ids["recipient"]))
        for statement, identity in invalid_updates:
            with self.subTest(statement=statement), self.assertRaises(DBAPIError), self.engine.begin() as connection:
                connection.execute(text(statement), {"id": identity, "foreign": uuid4()})
        base = {"request": ids["request"], "recipient": ids["recipient"], "author": ids["provider"], "parent": ids["proposal"], "revision": 2,
            "amount": "1.00", "currency": "USD", "kind": "provider_quote"}
        sql = """INSERT INTO network_service_proposals(id,request_id,recipient_id,author_id,parent_proposal_id,revision_no,kind,status,version,
            amount,currency,pricing_basis,scope_text,exclusions,valid_until) VALUES(:id,:request,:recipient,:author,:parent,:revision,:kind,'submitted',1,
            :amount,:currency,'fixed','Scope','',DATE '2030-01-01')"""
        for change in ({"amount": "0"}, {"currency": "EUR"}, {"revision": 1}, {"request": uuid4()},
                {"parent": None}, {"author": ids["owner"]}, {"kind": "invented"}):
            with self.subTest(fields=tuple(change)), self.assertRaises(DBAPIError), self.engine.begin() as connection:
                connection.execute(text(sql), {**base, **change, "id": uuid4()})
        with self.assertRaises(DBAPIError) as caught, self.engine.begin() as connection:
            connection.execute(text("UPDATE network_service_recipients SET current_proposal_id=:foreign,version=version+1 WHERE id=:id"),
                {"foreign": foreign["proposal"], "id": ids["recipient"]})
        self.assertEqual(caught.exception.orig.pgcode, "23514")
        for change in ({"request": foreign["request"]}, {"recipient": foreign["recipient"]}, {"parent": foreign["proposal"]}):
            with self.subTest(existing_foreign_fields=tuple(change)), self.assertRaises(DBAPIError) as caught, self.engine.begin() as connection:
                connection.execute(text(sql), {**base, **change, "id": uuid4()})
            self.assertEqual(caught.exception.orig.pgcode, "23514")
        with self.assertRaises(DBAPIError) as caught, self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO network_service_actions(id,actor_id,submission_id,operation,target_id,request_id,
                recipient_id,proposal_id,creation_sha256,result_versions) VALUES(:id,:actor,:key,'provider_quote',:recipient,:request,
                :recipient,:proposal,:hash,'{}'::jsonb)"""), {"id": uuid4(), "actor": ids["provider"], "key": uuid4(),
                "recipient": ids["recipient"], "request": foreign["request"], "proposal": ids["proposal"], "hash": "f" * 64})
        self.assertEqual(caught.exception.orig.pgcode, "23514")

    def test_empty_only_downgrade_reupgrade_and_populated_agreement_retention(self):
        self.build()
        expected = self.shape()
        self.revision(direction="downgrade")
        with self.engine.connect() as connection:
            self.assertTrue(all(connection.execute(text("SELECT to_regclass(:name)"), {"name": name}).scalar_one() is None for name in TABLES))
            self.assertIsNotNone(connection.execute(text("SELECT to_regclass('network_properties')")).scalar_one())
        self.revision()
        self.assertEqual(self.shape(), expected)
        self.seed()
        with self.engine.connect() as connection:
            before = {table: connection.execute(text(f"SELECT to_jsonb(t) FROM {table} t")).scalars().all() for table in TABLES}
        with self.assertRaises(DBAPIError) as caught:
            self.revision(direction="downgrade")
        self.assertEqual(caught.exception.orig.pgcode, "55000")
        self.assertEqual(self.shape(), expected)
        with self.engine.connect() as connection:
            after = {table: connection.execute(text(f"SELECT to_jsonb(t) FROM {table} t")).scalars().all() for table in TABLES}
        self.assertEqual(after, before)
