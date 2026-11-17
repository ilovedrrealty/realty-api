"""Private shortlist contracts against a disposable loopback PostgreSQL schema."""

from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-shortlist-test",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
                  SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")

import jwt
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from config import settings
from database import Base, get_db
from models.listing import Listing
from models.property_network import NetworkBlock
from models.research_workspace import ResearchWorkspace
from models.saved_home import SavedHome
from models.user import User
from routes import shortlists as routes
from services import shortlist


START = datetime(2031, 1, 15, tzinfo=timezone.utc)
NOTE = "Explicitly shared note"
PREFERENCES = "Explicitly shared preferences"


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires owned loopback PostgreSQL")
class ShortlistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if (url.get_backend_name() != "postgresql" or url.host != "127.0.0.1" or
                url.port != 55449 or url.database != "ildr_platform_test_20261025"):
            raise ValueError("Use the fixed disposable loopback PostgreSQL database")
        cls.schema = "shortlist_test_" + uuid4().hex
        cls.bootstrap = create_engine(url)
        cls.addClassCleanup(cls.cleanup_schema)
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        options = f"-csearch_path={cls.schema} -clock_timeout=5000 -cstatement_timeout=10000"
        cls.engine = create_engine(url, connect_args={"options": options})
        Base.metadata.create_all(cls.engine, tables=(User.__table__, Listing.__table__, NetworkBlock.__table__,
                                                    SavedHome.__table__, ResearchWorkspace.__table__))
        path = Path(__file__).resolve().parents[1] / "alembic/versions/9f65b4e21ca0_private_client_shortlists.py"
        spec = spec_from_file_location("shortlist_revision", path)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        with cls.engine.begin() as connection:
            module.op = Operations(MigrationContext.configure(connection))
            module.upgrade()

        def local_db():
            with Session(cls.engine) as session:
                yield session

        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(app)
        cls.addClassCleanup(cls.client.close)

    @classmethod
    def cleanup_schema(cls):
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        assert re.fullmatch(r"shortlist_test_[0-9a-f]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE'))
        finally:
            cls.bootstrap.dispose()

    def setUp(self):
        self.users, self.codes, self.tokens, self.listings = {}, {}, {}, {}
        with Session(self.engine) as db:
            for name, role in (("agent", "realtor"), ("other_agent", "realtor"), ("buyer", "buyer"),
                               ("other_buyer", "buyer"), ("unassigned", "buyer"), ("admin", "admin"),
                               ("owner", "owner"), ("unverified", "buyer"), ("suspended", "buyer")):
                actor = User(id=uuid4(), email=f"{uuid4()}@example.invalid", display_name="Synthetic account",
                             role=role, status="suspended" if name == "suspended" else "active",
                             email_verified=name != "unverified")
                db.add(actor)
                self.users[name] = actor
                self.tokens[name] = jwt.encode({"sub": str(actor.id)}, settings.jwt_secret, algorithm="HS256")
            db.flush()
            self.ids = {name: actor.id for name, actor in self.users.items()}
            self.codes = {name: actor.user_code for name, actor in self.users.items()}
            for name in ("buyer", "other_buyer", "unverified", "suspended"):
                self.users[name].assigned_realtor_id = self.ids["agent"]
            for name, status in (("first", "active"), ("second", "active"), ("hidden", "archived")):
                identifier = uuid4()
                db.add(Listing(id=identifier, title=("PRIVATE_ARCHIVED" if name == "hidden" else f"Listing {name}"),
                               type="villa", transaction="sale", price=10, location="Synthetic area",
                               submitted_by=self.ids["agent"], status=status,
                               co_listing_agent_email="private-listing@example.invalid", co_listing_notes="PRIVATE_LISTING"))
                self.listings[name] = str(identifier)
            db.flush()
            db.add(SavedHome(user_id=self.ids["buyer"], listing_id=UUID(self.listings["hidden"])))
            db.add(ResearchWorkspace(user_id=self.ids["buyer"], title="Private workspace",
                                     data={"notes": "PRIVATE_WORKSPACE_SENTINEL"}))
            db.commit()
        clock = patch.object(shortlist, "now", return_value=START)
        self.now = clock.start()
        self.addCleanup(clock.stop)

    def request(self, actor, method, path, status, *, body=None, headers=None, **kwargs):
        headers = dict(headers or {})
        if actor is not None:
            headers["Authorization"] = "Bearer " + self.tokens[actor]
        if body is not None:
            kwargs["json"] = body
        response = self.client.request(method, "/shortlists" + path, headers=headers, **kwargs)
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["pragma"], "no-cache")
        self.assertEqual(response.headers["vary"], "Cookie, Authorization")
        self.assertNotIn("PRIVATE_", response.text)
        self.assertNotIn("example.invalid", response.text)
        return None if status == 204 else response.json()

    def fields(self, selected=("first", "second")):
        return {"title": "Visit options", "shared_notes": NOTE, "shared_preferences": PREFERENCES,
                "listing_ids": [self.listings[name] for name in selected]}

    def create(self, owner="agent", selected=("first", "second")):
        return self.request(owner, "POST", "/owned", 201, body=self.fields(selected))

    def share(self, owner, doc, client, status=200, days=7):
        return self.request(owner, "POST", f"/owned/{doc}/share", status,
                            body={"recipient_user_code": self.codes[client], "expires_in_days": days})

    def test_deliberate_role_scope_and_minimal_payloads(self):
        for actor, code in ((None, 401), ("owner", 403), ("admin", 403), ("unverified", 403), ("suspended", 403)):
            self.request(actor, "GET", "/owned", code)
            self.request(actor, "GET", "/received", code)
        document = self.create()
        self.assertIsNone(document["share"])
        self.assertEqual(document["version"], 1)
        for actor in ("buyer", "other_agent"):
            self.request(actor, "GET", f"/owned/{document['id']}", 404)
        for target in ("agent", "unassigned", "unverified", "suspended", "other_agent", "admin"):
            self.share("agent", document["id"], target, 404)

        owned = self.share("agent", document["id"], "buyer")
        grant = owned["share"]["id"]
        self.assertEqual(owned["share"]["state"], "active")
        self.assertEqual(self.share("agent", document["id"], "buyer")["share"]["id"], grant)
        self.share("agent", document["id"], "buyer", 409, days=30)
        summary = self.request("buyer", "GET", "/received", 200)["items"]
        self.assertEqual(summary, [{"id": grant, "title": owned["title"], "expires_at": owned["share"]["expires_at"]}])
        self.assertEqual(self.request("agent", "GET", "/owned", 200)["items"][0]["id"], document["id"])
        self.assertEqual(self.request("other_buyer", "GET", "/received", 200)["items"], [])
        for actor in ("agent", "other_buyer", "other_agent"):
            self.request(actor, "GET", f"/received/{grant}", 404)
        for actor, code in ((None, 401), ("admin", 403), ("owner", 403)):
            self.request(actor, "GET", f"/received/{grant}", code)
        self.request("buyer", "GET", f"/received/{document['id']}", 404)

        received = self.request("buyer", "GET", f"/received/{grant}", 200)
        self.assertEqual(set(received), {"id", "version", "title", "shared_notes", "shared_preferences", "items", "expires_at"})
        self.assertEqual((received["shared_notes"], received["shared_preferences"]), (NOTE, PREFERENCES))
        self.assertEqual(set(received["items"][0]), {"id", "title", "location", "transaction"})
        self.assertEqual([item["id"] for item in received["items"]], [self.listings["first"], self.listings["second"]])

    def test_collaboration_cas_and_current_listings(self):
        doc = self.create()["id"]
        grant = self.share("agent", doc, "buyer")["share"]["id"]
        with Session(self.engine) as db:
            db.get(Listing, UUID(self.listings["second"])).status = "archived"
            db.commit()
        guest = self.request("buyer", "GET", f"/received/{grant}", 200)
        self.assertEqual([item["id"] for item in guest["items"]], [self.listings["first"]])
        update = {"expected_version": 1, "title": "Client revision", "shared_notes": "New shared text",
                  "shared_preferences": "New preferences"}
        changed = self.request("buyer", "PUT", f"/received/{grant}", 200, body=update)
        self.assertEqual(changed["version"], 2)
        self.assertEqual(changed["shared_preferences"], "New preferences")
        self.request("agent", "PUT", f"/owned/{doc}", 409, body=update)
        update["expected_version"] = 2
        self.request("agent", "PUT", f"/owned/{doc}", 404,
                     body={**update, "listing_ids": [self.listings["second"]]})

        with Session(self.engine) as db:
            db.get(Listing, UUID(self.listings["second"])).status = "active"
            db.commit()
        mine = self.request("agent", "GET", f"/owned/{doc}", 200)
        self.assertEqual([item["id"] for item in mine["items"]], [self.listings["first"], self.listings["second"]])
        changed = self.request("buyer", "PUT", f"/received/{grant}", 200,
                               body={**update, "listing_ids": [self.listings["second"]]})
        self.assertEqual([item["id"] for item in changed["items"]], [self.listings["second"]])
        owner_changed = self.request("agent", "PUT", f"/owned/{doc}", 200,
                                     body={**update, "expected_version": 3, "listing_ids": []})
        self.assertEqual(owner_changed["items"], [])
        self.request("agent", "POST", "/owned", 404, body=self.fields(("hidden",)))

    def test_expiry_revoke_fresh_id_and_cross_client_boundary(self):
        doc = self.create()["id"]
        old_grant = self.share("agent", doc, "buyer")["share"]["id"]
        self.now.return_value = START + timedelta(days=7)
        self.request("buyer", "GET", f"/received/{old_grant}", 404)
        self.assertEqual(self.request("agent", "GET", f"/owned/{doc}", 200)["share"]["state"], "expired")
        fresh = self.share("agent", doc, "buyer")["share"]["id"]
        self.assertNotEqual(old_grant, fresh)
        self.request("buyer", "GET", f"/received/{old_grant}", 404)
        revoked = self.request("agent", "POST", f"/owned/{doc}/shares/{fresh}/revoke", 200, body={})
        self.assertEqual(revoked["share"]["state"], "revoked")
        self.request("buyer", "GET", f"/received/{fresh}", 404)
        self.request("buyer", "PUT", f"/received/{fresh}", 404, body={**self.fields(), "expected_version": 1})
        self.share("agent", doc, "other_buyer", 409)
        next_grant = self.share("agent", doc, "buyer")["share"]["id"]
        self.request("agent", "POST", f"/owned/{doc}/archive", 409, body={"expected_version": 2})
        self.request("agent", "POST", f"/owned/{doc}/archive", 204, body={"expected_version": 1})
        self.request("agent", "GET", f"/owned/{doc}", 404)
        self.request("buyer", "GET", f"/received/{next_grant}", 404)
        self.assertEqual(self.request("buyer", "GET", "/received", 200)["items"], [])

    def test_assignment_switch_back_and_bilateral_block(self):
        doc = self.create("buyer")["id"]
        old = self.share("buyer", doc, "agent")["share"]["id"]
        for agent in ("other_agent", "agent"):
            with Session(self.engine) as db:
                db.get(User, self.ids["buyer"]).assigned_realtor_id = self.ids[agent]
                db.commit()
            self.request("agent", "GET", f"/received/{old}", 404)
        self.assertEqual(self.request("buyer", "GET", f"/owned/{doc}", 200)["share"]["state"], "unavailable")
        fresh = self.share("buyer", doc, "agent")["share"]["id"]
        self.assertNotEqual(fresh, old)
        self.request("agent", "GET", f"/received/{fresh}", 200)

        with Session(self.engine) as db:
            db.add(NetworkBlock(blocker_id=self.ids["agent"], blocked_id=self.ids["buyer"]))
            db.commit()
        self.request("agent", "GET", f"/received/{fresh}", 404)
        self.request("agent", "PUT", f"/received/{fresh}", 404, body={**self.fields(), "expected_version": 1})
        self.share("buyer", doc, "agent", 404)

    def test_quotas_validation_and_privacy(self):
        with patch.object(shortlist, "MAX_OWNED", 1):
            doc = self.create(selected=())["id"]
            self.request("agent", "POST", "/owned", 409, body=self.fields())
        second = self.create()["id"]
        with patch.object(shortlist, "MAX_RECEIVED", 1):
            self.share("agent", doc, "buyer")
            self.share("agent", second, "buyer", 409)
        for changes in ({"title": " "}, {"title": "PRIVATE_" + "x" * 100},
                        {"listing_ids": [self.listings["first"]] * 2},
                        {"listing_ids": None}, {"shared_notes": "PRIVATE_\x00"},
                        {"shared_preferences": "PRIVATE_" + "x" * 2000},
                        {"data": {"notes": "PRIVATE_WORKSPACE"}}):
            self.request("buyer", "POST", "/owned", 422, body={**self.fields(), **changes})
        self.request("agent", "POST", f"/owned/{second}/share", 422,
                     body={"recipient_user_code": True, "expires_in_days": 7})
        self.request("agent", "POST", f"/owned/{second}/share", 422,
                     body={"recipient_user_code": self.codes["buyer"], "expires_in_days": 3})
        self.request("buyer", "GET", "/received/PRIVATE_INVALID", 422)
        self.request("agent", "POST", "/owned", 413, headers={"Content-Type": "application/json"},
                     content="x" * (routes.MAX_BODY + 1))
        cookie = {"Cookie": "ildr_token=" + self.tokens["agent"]}
        self.request(None, "POST", "/owned", 403, headers={**cookie, "Origin": "https://foreign.invalid"},
                     body=self.fields())
        with patch.object(shortlist, "owned", side_effect=RuntimeError("PRIVATE_DRIVER_SENTINEL")):
            self.request("agent", "GET", "/owned", 503)

    def test_migration_does_not_grant_manual_provider_roles(self):
        with self.engine.connect() as db:
            for table in ("client_shortlists", "client_shortlist_items", "client_shortlist_grants"):
                self.assertTrue(db.execute(text(
                    "SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(:table)"
                ), {"table": table}).scalar_one())
                self.assertFalse(db.execute(text("""
                    SELECT EXISTS (SELECT 1 FROM pg_class, aclexplode(coalesce(relacl, acldefault('r', relowner))) acl
                                   WHERE oid = to_regclass(:table) AND acl.grantee = 0)
                """), {"table": table}).scalar_one())
                for role in ("anon", "authenticated", "service_role"):
                    allowed = db.execute(text("""
                        SELECT has_table_privilege(oid, :table, 'SELECT, INSERT, UPDATE, DELETE')
                        FROM pg_roles WHERE rolname = :role
                    """), {"table": table, "role": role}).scalar_one_or_none()
                    self.assertNotEqual(allowed, True)
