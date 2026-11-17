"""Exercise private transaction rooms on an owned synthetic PostgreSQL fixture."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import re
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-transaction-room-test",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
                  SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from config import settings
from database import Base, get_db
from models.lead import Lead
from models.listing import Listing
from models.transaction_room import TransactionRoom
from models.user import User
from routes import transaction_room as routes
from services import transaction_room as service


MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/6c942ea8385d_private_transaction_rooms.py"


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires owned loopback PostgreSQL")
class TransactionRoomFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if (cls.url.get_backend_name() != "postgresql" or cls.url.host != "127.0.0.1" or
                cls.url.port != 55449 or cls.url.database != "ildr_platform_test_20261025"):
            raise ValueError("Use the fixed disposable loopback PostgreSQL database")
        cls.bootstrap = create_engine(cls.url)
        cls.addClassCleanup(cls.bootstrap.dispose)
        cls.engine = cls.new_schema(prebuild=False)
        cls.addClassCleanup(cls.drop_schema, cls.engine)

        def local_db():
            with Session(cls.engine) as session:
                yield session

        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(app)
        cls.addClassCleanup(cls.client.close)

    @classmethod
    def new_schema(cls, prebuild):
        schema = "transaction_room_test_" + uuid4().hex
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        options = f"-csearch_path={schema} -clock_timeout=5000 -cstatement_timeout=10000"
        engine = create_engine(cls.url, connect_args={"options": options})
        engine.update_execution_options(fixture_schema=schema)
        try:
            spec = spec_from_file_location(MIGRATION.stem, MIGRATION)
            migration = module_from_spec(spec)
            spec.loader.exec_module(migration)
            table_names = ("users", "listings", "leads")
            if prebuild:
                table_names += migration.TABLES
            tables = [Base.metadata.tables[name] for name in table_names]
            Base.metadata.create_all(engine, tables=tables)
            with engine.begin() as connection:
                migration.op = Operations(MigrationContext.configure(connection))
                migration.upgrade()
        except Exception:
            cls.drop_schema(engine)
            raise
        return engine

    @classmethod
    def drop_schema(cls, engine):
        schema = engine.get_execution_options()["fixture_schema"]
        engine.dispose()
        assert re.fullmatch(r"transaction_room_test_[0-9a-f]{32}", schema)
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))

    def setUp(self):
        self.users, self.tokens, self.codes, self.leads = {}, {}, {}, {}
        with Session(self.engine) as db:
            for name, role, verified, status in (
                ("admin", "admin", True, "active"), ("buyer", "buyer", True, "active"),
                ("other_buyer", "buyer", True, "active"), ("realtor", "realtor", True, "active"),
                ("other_realtor", "realtor", True, "active"), ("professional", "owner", True, "active"),
                ("suspended", "owner", True, "suspended"), ("unverified", "owner", False, "active"),
            ):
                user = User(id=uuid4(), email=f"{uuid4()}@example.invalid", display_name=f"Synthetic {name}",
                            role=role, status=status, email_verified=verified, phone="PRIVATE_CONTACT")
                db.add(user)
                db.flush()
                self.users[name], self.codes[name] = user.id, user.user_code
                self.tokens[name] = jwt.encode({"sub": str(user.id)}, settings.jwt_secret, algorithm="HS256")
            listing = Listing(id=uuid4(), title="Synthetic sale", type="villa", transaction="sale", status="active",
                              price=100000, location="Synthetic area", submitted_by=self.users["admin"])
            rental = Listing(id=uuid4(), title="Synthetic rent", type="villa", transaction="rent", status="active",
                             price=1000, location="Synthetic area", submitted_by=self.users["admin"])
            db.add_all((listing, rental))
            db.flush()
            for name, kind, buyer, realtor, property_id in (
                ("sale", "buyer_interest", "buyer", "realtor", listing.id),
                ("inquiry", "property_inquiry", "buyer", "realtor", listing.id),
                ("seller", "seller_interest", "buyer", "realtor", listing.id),
                ("rental", "property_inquiry", "buyer", "realtor", rental.id),
                ("guest", "property_inquiry", None, "realtor", listing.id),
                ("unassigned", "buyer_interest", "buyer", None, listing.id),
            ):
                lead = Lead(id=uuid4(), type=kind, name="PRIVATE_LEAD_NAME", email="lead@example.invalid",
                            phone="PRIVATE_LEAD_PHONE", message="PRIVATE_LEAD_MESSAGE", property_id=property_id,
                            from_user_id=self.users.get(buyer), assigned_realtor_id=self.users.get(realtor))
                db.add(lead)
                self.leads[name] = lead.id
            db.commit()
        for name in ("httpx.HTTPTransport.handle_request", "httpx.AsyncHTTPTransport.handle_async_request"):
            guard = patch(name, side_effect=AssertionError("Transaction rooms must stay internal"))
            transport = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(transport.assert_not_called)

    def request(self, actor, method, path="", status=200, body=None, **kwargs):
        headers = kwargs.pop("headers", {})
        if actor is not None:
            headers["Authorization"] = "Bearer " + self.tokens[actor]
        if body is not None:
            kwargs["json"] = body
        response = self.client.request(method, "/transactions" + path, headers=headers, **kwargs)
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertIn("Authorization", response.headers["Vary"])
        for private_value in ("PRIVATE_", "example.invalid"):
            self.assertNotIn(private_value, response.text)
        return response.json()

    def make_room(self, lead="sale", actor="admin"):
        return self.request(actor, "POST", f"/leads/{self.leads[lead]}", 201, {"title": "Synthetic closing"})

    def invite(self, room, recipient="professional"):
        result = self.request("realtor", "POST", f"/{room['id']}/invitations", body={
            "member_code": self.codes[recipient], "expected_version": room["version"],
        })
        grant = next(member["grant_id"] for member in result["members"]
                     if member["state"] == "pending" and member["member_code"] == self.codes[recipient])
        return result, grant


class TransactionRoomTests(TransactionRoomFixture):
    def test_create_from_exact_scoped_lead_and_room_listing(self):
        lead = f"/leads/{self.leads['sale']}"
        self.assertEqual(self.request("realtor", "GET", lead), {"room": None})
        self.request("other_realtor", "GET", lead, 404)
        self.request("buyer", "POST", lead, 404, {"title": "Synthetic"})
        room = self.make_room(actor="realtor")
        path = f"/{room['id']}"
        self.assertEqual((room["version"], room["status"], room["role"]), (1, "open", "realtor"))
        self.assertEqual(self.request("admin", "GET", lead)["room"]["id"], room["id"])
        self.assertEqual(self.request("buyer", "GET", path)["role"], "buyer")
        self.assertEqual(self.request("buyer", "GET")["items"][0]["id"], room["id"])
        self.assertEqual(self.request("other_buyer", "GET"), {"items": []})
        self.request("other_buyer", "GET", path, 404)
        self.request("other_realtor", "GET", path + "/history", 404)
        self.request(None, "GET", path, 401)
        self.request("admin", "POST", lead, 409, {"title": "Duplicate"})
        self.assertEqual(self.request("buyer", "GET", path + "/history")["items"][0]["action"], "room_created")

    def test_ineligible_leads_and_account_states_cannot_enter(self):
        for lead in ("seller", "rental", "guest", "unassigned"):
            with self.subTest(lead=lead):
                path = f"/leads/{self.leads[lead]}"
                self.request("admin", "GET", path, 404)
                self.request("admin", "POST", path, 404, {"title": "Synthetic denied case"})
        room = self.make_room("inquiry")
        for actor in ("suspended", "unverified"):
            self.request(actor, "GET", f"/{room['id']}", 403)
        for actor in ("suspended", "unverified", "buyer", "realtor", "admin"):
            self.request("admin", "POST", f"/{room['id']}/invitations", 404,
                         {"expected_version": room["version"], "member_code": self.codes[actor]})

    def test_invitation_requires_explicit_acceptance_and_fresh_grant_after_revoke(self):
        room = self.make_room()
        path = f"/{room['id']}"
        invited, grant = self.invite(room)
        pending = self.request("professional", "GET", "/invitations")["items"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(set(pending[0]), {"id", "invited_at"})
        self.assertEqual(pending[0]["id"], grant)
        self.assertEqual(self.request("other_buyer", "GET", "/invitations"), {"items": []})
        self.request("professional", "GET", path, 404)
        self.request("other_buyer", "POST", f"/invitations/{grant}/accept", 404, {})
        self.assertNotIn("professional_invited", {
            row["action"] for row in self.request("buyer", "GET", path + "/history")["items"]
        })
        accepted = self.request("professional", "POST", f"/invitations/{grant}/accept", body={})
        self.assertEqual((accepted["version"], accepted["role"]), (invited["version"] + 1, "professional"))
        self.assertTrue(all(row["member_code"] is None and row["grant_id"] is None for row in accepted["members"]))
        self.assertEqual(self.request("professional", "GET", "/invitations"), {"items": []})
        self.request("professional", "POST", f"/invitations/{grant}/accept", 404, {})
        revoked = self.request("admin", "POST", path + f"/members/{grant}/revoke", body={
            "expected_version": accepted["version"],
        })
        self.request("professional", "GET", path, 404)
        self.request("professional", "GET", path + "/history", 404)
        renewed, new_grant = self.invite(revoked)
        self.assertNotEqual(grant, new_grant)
        self.assertEqual(renewed["version"], revoked["version"] + 1)
        self.request("professional", "POST", f"/invitations/{grant}/accept", 404, {})

    def test_reassignment_revokes_old_role_and_preexisting_grant_without_resurrection(self):
        room = self.make_room()
        path = f"/{room['id']}"
        _, grant = self.invite(room, "other_realtor")
        accepted = self.request("other_realtor", "POST", f"/invitations/{grant}/accept", body={})
        self.assertEqual(accepted["role"], "professional")
        self._reassign("other_realtor")
        self.request("realtor", "GET", path, 404)
        reassigned = self.request("other_realtor", "GET", path)
        self.assertEqual((reassigned["role"], reassigned["version"]), ("realtor", accepted["version"] + 1))
        self._reassign("realtor")
        self.request("other_realtor", "GET", path, 404)
        self.request("other_realtor", "POST", f"/invitations/{grant}/accept", 404, {})
        names = [event["action"] for event in self.request("realtor", "GET", path + "/history")["items"]]
        self.assertEqual(names[:2], ["assignment_changed", "assignment_changed"])

    def _reassign(self, to):
        with Session(self.engine) as db:
            db.query(Lead).filter(Lead.id == self.leads["sale"]).update({Lead.assigned_realtor_id: self.users[to]})
            db.commit()

    def test_rebinding_retires_room_permanently_and_limits_history_to_admin(self):
        room = self.make_room()
        path = f"/{room['id']}"
        self.invite(room)
        for name in ("other_buyer", "buyer"):
            with Session(self.engine) as db:
                db.query(Lead).filter(Lead.id == self.leads["sale"]).update({Lead.from_user_id: self.users[name]})
                db.commit()
            self.request("buyer", "GET", path, 404)
            self.request("realtor", "GET", path, 404)
            self.request("other_buyer", "GET", path, 404)
            self.assertEqual(self.request("professional", "GET", "/invitations"), {"items": []})
        retired = self.request("admin", "GET", path)
        self.assertEqual((retired["status"], retired["members"]), ("retired", []))
        self.assertEqual(self.request("admin", "GET", path + "/history")["items"][0]["action"], "room_retired")
        self.request("admin", "POST", path + "/close", 409, {"expected_version": retired["version"]})

    def test_task_ownership_milestone_dates_and_close_are_versioned(self):
        room = self.make_room()
        path = f"/{room['id']}"
        payload = {"expected_version": room["version"], "title": "Synthetic identity review",
                   "assignee_user_id": str(self.users["buyer"]), "due_on": "2026-11-28"}
        self.request("buyer", "POST", path + "/tasks", 403, payload)
        self.request("admin", "POST", path + "/tasks", 422,
                     {**payload, "assignee_user_id": str(self.users["admin"])})
        result = self.request("admin", "POST", path + "/tasks", 201, payload)
        task_path = path + f"/tasks/{result['tasks'][0]['id']}"
        self.request("buyer", "PATCH", task_path, 409, {"expected_version": 1, "status": "done"})
        self.request("buyer", "PATCH", task_path, 403, {"expected_version": result["version"], "title": "Changed"})
        done = self.request("buyer", "PATCH", task_path, body={"expected_version": result["version"], "status": "done"})
        self.assertEqual((done["tasks"][0]["status"], done["tasks"][0]["version"]), ("done", 2))
        self.request("buyer", "PATCH", task_path, 403, {"expected_version": done["version"], "status": "open"})
        opened = self.request("realtor", "PATCH", task_path, body={"expected_version": done["version"], "status": "open"})
        payload = {"expected_version": opened["version"], "title": "Synthetic deed meeting",
                   "target_on": "2026-11-20", "actual_on": "2026-11-18"}
        with patch.object(service, "today_dr", return_value=date(2026, 11, 17)):
            self.request("admin", "POST", path + "/milestones", 422, payload)
            milestone = self.request("admin", "POST", path + "/milestones", 201, {**payload, "actual_on": None})
            edited = self.request("admin", "PUT", path + f"/milestones/{milestone['milestones'][0]['id']}", body={
                **payload, "expected_version": milestone["version"], "actual_on": "2026-11-17",
            })
        self.assertEqual((edited["milestones"][0]["actual_on"], edited["milestones"][0]["version"]), ("2026-11-17", 2))
        closed = self.request("realtor", "POST", path + "/close", body={"expected_version": edited["version"]})
        self.assertEqual(self.request("buyer", "GET", path)["status"], "closed")
        self.request("realtor", "PATCH", task_path, 409, {"expected_version": closed["version"], "status": "done"})

    def test_metadata_history_filters_private_actions_and_input_errors(self):
        room = self.make_room()
        path = f"/{room['id']}"
        with Session(self.engine) as db:
            actor = db.get(User, self.users["admin"])
            stored, role = service.room_for(db, actor, UUID(room["id"]), permission="manage")
            service.record_event(db, stored, actor, role, "document_version_submitted", uuid4())
            db.commit()
        staff = self.request("admin", "GET", path + "/history")["items"]
        history = self.request("buyer", "GET", path + "/history")["items"]
        self.assertEqual(staff[0]["action"], "document_version_submitted")
        self.assertEqual([event["action"] for event in history], ["room_created"])
        self.assertEqual(set(history[0]), {"sequence", "action", "actor_role", "target_id", "created_at"})
        self.request("realtor", "POST", path + "/tasks", 422,
                     {"expected_version": 2, "title": "Synthetic", "unexpected": "PRIVATE_INVALID_INPUT"})
        self.request("realtor", "POST", path + "/tasks", 415, content="PRIVATE_INVALID_TYPE")
        self.request("realtor", "POST", path + "/tasks", 413,
                     {"expected_version": 2, "title": "X" * (routes.MAX_BODY + 1)})

    def test_competing_first_create_has_one_room(self):
        barrier = Barrier(2)
        lead = f"/transactions/leads/{self.leads['sale']}"

        def send(actor):
            barrier.wait(timeout=3)
            return self.client.post(lead, json={"title": "Synthetic concurrent closing"},
                                    headers={"Authorization": "Bearer " + self.tokens[actor]}).status_code

        with ThreadPoolExecutor(max_workers=2) as workers:
            self.assertEqual(sorted(workers.map(send, ("admin", "realtor"))), [201, 409])
        self.assertEqual(len(self.request("buyer", "GET")["items"]), 1)

    def test_rls_immutable_history_and_fresh_orm_upgrade(self):
        room = self.make_room()
        with self.engine.begin() as connection:
            protection = connection.execute(text("""
                SELECT relname, relrowsecurity FROM pg_class
                WHERE relnamespace = current_schema()::regnamespace AND relname LIKE 'transaction_room%'
                  AND relkind = 'r'
            """)).all()
            self.assertEqual(len(protection), 5)
            self.assertTrue(all(row.relrowsecurity for row in protection))
            with self.assertRaises(DBAPIError) as caught, connection.begin_nested():
                connection.execute(text("UPDATE transaction_room_events SET action='changed' WHERE room_id=:id"),
                                   {"id": room["id"]})
            self.assertEqual(caught.exception.orig.pgcode, "55000")
            grants = connection.execute(text("""
                SELECT count(*) FROM pg_class rel
                JOIN pg_roles role ON role.rolname IN ('anon','authenticated','service_role')
                WHERE rel.relnamespace = current_schema()::regnamespace AND rel.relname LIKE 'transaction_room%'
                  AND rel.relkind='r' AND role.rolname <> current_user
                  AND has_table_privilege(role.oid, rel.oid, 'SELECT,INSERT,UPDATE,DELETE')
            """)).scalar_one()
            self.assertEqual(grants, 0)
        prebuilt = self.new_schema(prebuild=True)
        self.addCleanup(self.drop_schema, prebuilt)
        with prebuilt.begin() as connection:
            enabled = connection.execute(text("""
                SELECT relrowsecurity FROM pg_class WHERE oid='transaction_rooms'::regclass
            """)).scalar_one()
            self.assertTrue(enabled)
            self.assertEqual(connection.execute(text("SELECT count(*) FROM transaction_room_events")).scalar_one(), 0)
        self.assertEqual(TransactionRoom.__table__.name, "transaction_rooms")
