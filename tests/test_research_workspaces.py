"""Private research isolation and optimistic updates against disposable Postgres."""
import os
import unittest
import importlib.util
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-research-tests-only", GHL_ENABLED="false", RESEND_API_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="", SUPABASE_SERVICE_KEY="")
import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from alembic.migration import MigrationContext
from alembic.operations import Operations
from config import settings
from database import Base, get_db
import models
from models.user import User
from models.research_workspace import ResearchWorkspace
from routes import research_workspaces
from utils.limiter import limiter


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires disposable loopback Postgres")
class ResearchWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host not in ("127.0.0.1", "localhost", "::1") or url.get_backend_name() != "postgresql":
            raise ValueError("Tests require disposable loopback-only Postgres")
        cls.schema = "research_test_" + uuid4().hex
        bootstrap = create_engine(url)
        with bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        bootstrap.dispose()
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema}, pool_size=8)
        Base.metadata.create_all(cls.engine)
        app = FastAPI()
        app.include_router(research_workspaces.router)
        def local_db():
            with Session(cls.engine) as db:
                yield db
        app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(app)
        cls.old_limiter = limiter.enabled
        limiter.enabled = False
        cls.headers = {}
        cls.ids = {}
        with Session(cls.engine) as db:
            for name, role, verified, status in [("buyer", "buyer", True, "active"), ("other", "buyer", True, "active"), ("owner", "owner", True, "active"), ("realtor", "realtor", True, "active"), ("admin", "admin", True, "active"), ("unverified", "buyer", False, "active"), ("suspended", "buyer", True, "suspended")]:
                user = User(email=name+"@example.com", display_name="Synthetic "+name, role=role, email_verified=verified, status=status)
                db.add(user); db.flush()
                cls.ids[name] = user.id
                cls.headers[name] = {"Authorization": "Bearer " + jwt.encode({"sub": str(user.id), "exp": datetime.now(timezone.utc)+timedelta(hours=1)}, settings.jwt_secret, algorithm="HS256")}
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        limiter.enabled = cls.old_limiter
        with cls.engine.begin() as connection:
            connection.execute(text('SET search_path TO public'))
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.engine.dispose()

    def setUp(self):
        with Session(self.engine) as db:
            db.query(ResearchWorkspace).delete(); db.commit()

    def call(self, method, path="", actor="buyer", body=None, status=200):
        response = self.client.request(method, "/research-workspaces"+path, json=body, headers=self.headers.get(actor, {}))
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        return response.json()

    def test_all_roles_save_and_return_after_a_new_request_without_role_changes(self):
        for actor in ("buyer", "owner", "realtor", "admin"):
            row = self.call("POST", actor=actor, body={"title": "Review "+actor, "reference": "Property ref", "checks": {"survey": True}, "item_notes": {"survey": "Private next step"}, "links": [{"title": "Official source", "url": "https://example.com/document"}]}, status=201)
            loaded = self.call("GET", "/"+row["id"], actor)
            self.assertEqual(loaded, row)
            listed = self.call("GET", actor=actor)
            self.assertEqual(len(listed), 1)
            self.assertNotIn("data", listed[0])
            with Session(self.engine) as db:
                self.assertEqual(db.get(User, self.ids[actor]).role, actor)

    def test_owner_isolation_applies_to_admin_and_guessed_ids_and_saves(self):
        row = self.call("POST", body={"title": "Private buyer review", "notes": "Private notes"}, status=201)
        for actor in ("other", "owner", "realtor", "admin"):
            self.assertEqual(self.call("GET", actor=actor), [])
            self.call("GET", "/"+row["id"], actor, status=404)
            self.call("PUT", "/"+row["id"], actor, {"title": "Attempt", "expected_version": 1}, status=404)
        self.call("GET", "/"+str(uuid4()), status=404)
        self.assertEqual(self.call("GET", "/"+row["id"])["data"]["notes"], "Private notes")

    def test_authentication_and_verified_active_account_required(self):
        for actor, status in ((None, 401), ("unverified", 403), ("suspended", 403)):
            self.call("GET", actor=actor, status=status)
            self.call("POST", actor=actor, body={"title": "Attempt"}, status=status)

    def test_stale_version_never_overwrites_existing_review(self):
        row = self.call("POST", body={"title": "Original"}, status=201)
        saved = self.call("PUT", "/"+row["id"], body={"title": "Saved in tab one", "expected_version": 1, "checks": {"review": True}})
        self.assertEqual(saved["version"], 2)
        self.call("PUT", "/"+row["id"], body={"title": "Stale tab", "expected_version": 1}, status=409)
        self.assertEqual(self.call("GET", "/"+row["id"]), saved)

    def test_bounded_schema_links_and_private_validation_errors(self):
        for body in ({"title": ""}, {"title": "x", "checks": {"invented": True}}, {"title": "x", "item_notes": {"review": "x"*1001}}, {"title": "x", "notes": "x"*3001}, {"title": "x", "user_id": str(self.ids['other'])}, {"title": "x", "links": [{"title": "secret", "url": "https://user:secret@example.com"}]}, {"title": "x", "links": [{"title": "unsafe", "url": "javascript:alert(1)"}]}):
            response = self.call("POST", body=body, status=422)
            self.assertNotIn("secret", str(response))
        response = self.client.post("/research-workspaces", content=b"x"*33000, headers={**self.headers["buyer"], "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_creation_limit_does_not_hide_saved_cases(self):
        with Session(self.engine) as db:
            db.add_all([ResearchWorkspace(user_id=self.ids["buyer"], title="Case "+str(i), data={"title": "Case "+str(i)}) for i in range(100)])
            db.commit()
        self.assertEqual(len(self.call("GET")), 100)
        self.call("POST", body={"title": "Extra"}, status=409)

    def test_migration_creates_private_table_and_retains_notes_on_rollback(self):
        migration_path = Path(__file__).resolve().parents[1]/'alembic/versions/52b2edc810ad_private_research_workspaces.py'
        spec = importlib.util.spec_from_file_location('research_migration', migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with self.engine.begin() as connection:
            connection.execute(text('DROP TABLE research_workspaces'))
            with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                migration.upgrade()
                migration.upgrade()
                secure = connection.execute(text("SELECT relrowsecurity FROM pg_class JOIN pg_namespace n ON n.oid=relnamespace WHERE relname='research_workspaces' AND n.nspname=:schema"), {'schema':self.schema}).scalar_one()
                self.assertTrue(secure)
                for role in ('anon', 'authenticated'):
                    if connection.execute(text('SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)'), {'role':role}).scalar():
                        self.assertFalse(connection.execute(text("SELECT has_table_privilege(:role, :table, 'SELECT')"), {'role':role,'table':self.schema+'.research_workspaces'}).scalar())
                migration.downgrade()
                self.assertIsNotNone(connection.execute(text("SELECT to_regclass(:table)"), {'table':self.schema+'.research_workspaces'}).scalar())

    def test_migration_on_bootstrapped_current_models_preserves_saved_reviews(self):
        row = self.call("POST", body={"title": "Retain this review", "notes": "Existing saved notes"}, status=201)
        migration_path = Path(__file__).resolve().parents[1]/'alembic/versions/52b2edc810ad_private_research_workspaces.py'
        spec = importlib.util.spec_from_file_location('research_bootstrap_migration', migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with self.engine.begin() as connection:
            with patch.object(migration, 'op', Operations(MigrationContext.configure(connection))):
                migration.upgrade()
                migration.downgrade()
        self.assertEqual(self.call("GET", "/"+row["id"]), row)


if __name__ == "__main__":
    unittest.main()
