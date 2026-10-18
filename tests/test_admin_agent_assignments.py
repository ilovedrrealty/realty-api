"""Active, verified admin accounts can work as agents without changing roles."""

import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

os.environ.update(
    DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-agent-assignment-only",
    GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
    SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="",
)

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from config import settings
from database import Base, get_db
from models.lead import Lead
from models.listing import Listing
from models.user import User
from routes import admin, auth, leads, listings


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires disposable local Postgres")
class AdminAgentAssignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host not in ("127.0.0.1", "localhost", "::1") or url.get_backend_name() != "postgresql":
            raise ValueError("Tests require a disposable loopback-only Postgres")
        cls.schema = "admin_agent_test_" + uuid4().hex
        cls.engine = create_engine(url)
        cls.connection = cls.engine.connect()
        cls.connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.connection.execute(text(f'SET search_path TO "{cls.schema}"'))
        Base.metadata.create_all(cls.connection)
        cls.connection.commit()
        cls.db = Session(cls.connection)
        cls.users, cls.headers = {}, {}
        for name, role, verified, status in (
            ("admin_a", "admin", True, "active"), ("admin_b", "admin", True, "active"),
            ("realtor", "realtor", True, "active"), ("buyer", "buyer", True, "active"),
            ("owner", "owner", True, "active"), ("suspended", "admin", True, "suspended"),
            ("unverified", "admin", False, "active"), ("inactive_realtor", "realtor", True, "suspended"),
        ):
            user = User(email=name + "@example.com", display_name="Synthetic " + name, role=role, email_verified=verified, status=status)
            cls.db.add(user)
            cls.db.flush()
            cls.users[name] = user
            cls.headers[name] = {"Authorization": "Bearer " + jwt.encode({"sub": str(user.id), "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, settings.jwt_secret, algorithm="HS256")}
        cls.property = Listing(title="Synthetic assignment listing", location="Sosúa", type="villa", transaction="sale", currency="USD", price=200000, images=[], features=[], submitted_by=cls.users["realtor"].id, status="active")
        cls.db.add(cls.property)
        cls.db.flush()
        cls.owner_lead = Lead(type="buyer_interest", name="Synthetic owner inquiry", email="owner@example.com", property_id=cls.property.id, from_user_id=cls.users["owner"].id)
        cls.admin_lead = Lead(type="buyer_interest", name="Synthetic admin inquiry", email="admin_a@example.com", property_id=cls.property.id, from_user_id=cls.users["admin_a"].id)
        cls.db.add_all([cls.owner_lead, cls.admin_lead])
        cls.db.commit()
        cls.app = FastAPI()
        for module in (admin, auth, leads, listings):
            cls.app.include_router(module.router)
        cls.app.dependency_overrides[get_db] = lambda: cls.db
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.db.close()
        cls.connection.rollback()
        assert re.fullmatch(r"admin_agent_test_[0-9a-f]{32}", cls.schema)
        cls.connection.execute(text("SET search_path TO public"))
        cls.connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.connection.commit()
        cls.connection.close()
        cls.engine.dispose()

    def setUp(self):
        self.db.rollback()
        self.property.assigned_realtor_id = None
        for lead in (self.owner_lead, self.admin_lead):
            lead.assigned_realtor_id = None
            lead.assigned_at = None
            lead.status = "new"
        for name in ("admin_a", "admin_b", "owner", "buyer"):
            self.users[name].assigned_realtor_id = None
        self.db.commit()
        self.notifications = patch.multiple(leads, send_realtor_lead_assigned_email=unittest.mock.DEFAULT, send_upgrade_approved_email=unittest.mock.DEFAULT, send_realtor_assigned_owner_email=unittest.mock.DEFAULT)
        self.notifications.start()
        self.addCleanup(self.notifications.stop)

    def assign(self, kind, agent_name, actor="admin_a", expected=204):
        paths = {
            "listing": f"/admin/listings/{self.property.id}/assign",
            "owner": f"/admin/users/{self.users['owner'].id}/assign-realtor",
            "lead": f"/admin/leads/{self.owner_lead.id}/assign",
        }
        response = self.client.put(paths[kind], headers=self.headers[actor], json={"realtor_id": str(self.users[agent_name].id)})
        self.assertEqual(response.status_code, expected, response.text)
        return response

    def test_agent_choices_include_two_admins_and_realtor_only_and_no_private_profile(self):
        for actor in ("admin_a", "admin_b"):
            response = self.client.get("/admin/agents", headers=self.headers[actor])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual({item["id"] for item in response.json()}, {str(self.users[name].id) for name in ("admin_a", "admin_b", "realtor")})
            self.assertTrue(all(set(item) == {"id", "display_name", "email", "role"} for item in response.json()))
        small = self.client.get("/admin/agents?limit=1", headers=self.headers["admin_a"])
        self.assertEqual(len(small.json()), 1)
        self.assertEqual(self.client.get("/admin/agents?limit=501", headers=self.headers["admin_a"]).status_code, 422)
        self.assertEqual(self.client.get("/admin/agents?q=%25", headers=self.headers["admin_a"]).json(), [])
        strict_roles = self.client.get("/admin/users?role=realtor", headers=self.headers["admin_a"])
        self.assertTrue(all(item["role"] == "realtor" for item in strict_roles.json()))
        self.assertNotIn(str(self.users["admin_a"].id), {item["id"] for item in strict_roles.json()})

    def test_both_admins_can_assign_self_and_enter_own_pipeline_without_role_change(self):
        for name in ("admin_a", "admin_b"):
            self.assign("listing", name, actor=name)
            self.assign("owner", name, actor=name)
            self.assign("lead", name, actor=name)
            pipeline = self.client.get("/realtor/leads", headers=self.headers[name])
            self.assertEqual(pipeline.status_code, 200, pipeline.text)
            self.assertIn(str(self.owner_lead.id), {item["id"] for item in pipeline.json()})
            other = "admin_b" if name == "admin_a" else "admin_a"
            other_pipeline = self.client.get("/realtor/leads", headers=self.headers[other])
            self.assertNotIn(str(self.owner_lead.id), {item["id"] for item in other_pipeline.json()})
            me = self.client.get("/auth/me", headers=self.headers[name]).json()
            self.assertEqual(me["role"], "admin")
            my_listings = self.client.get("/listings/mine", headers=self.headers[name]).json()
            self.assertIn(str(self.property.id), {item["id"] for item in my_listings})
        owner_agent = self.client.get("/auth/me/agent", headers=self.headers["owner"]).json()
        self.assertEqual(owner_agent["realtor_id"], str(self.users["admin_b"].id))

    def test_admin_owner_workspace_can_have_an_agent_without_changing_admin_role(self):
        response = self.client.put(f"/admin/users/{self.users['admin_a'].id}/assign-realtor", headers=self.headers["admin_b"], json={"realtor_id": str(self.users["admin_b"].id)})
        self.assertEqual(response.status_code, 204, response.text)
        my_agent = self.client.get("/auth/me/agent", headers=self.headers["admin_a"]).json()
        self.assertEqual(my_agent["realtor_id"], str(self.users["admin_b"].id))
        self.assertEqual(self.users["admin_a"].role, "admin")
        invalid_target = self.client.put(f"/admin/users/{self.users['buyer'].id}/assign-realtor", headers=self.headers["admin_a"], json={"realtor_id": str(self.users["admin_a"].id)})
        self.assertEqual(invalid_target.status_code, 400)

    def test_admin_submitted_lead_mirrors_agent_but_never_downgrades_admin(self):
        response = self.client.put(f"/admin/leads/{self.admin_lead.id}/assign", headers=self.headers["admin_b"], json={"realtor_id": str(self.users["admin_b"].id)})
        self.assertEqual(response.status_code, 204, response.text)
        self.db.refresh(self.users["admin_a"])
        self.assertEqual(self.users["admin_a"].assigned_realtor_id, self.users["admin_b"].id)
        self.assertEqual(self.users["admin_a"].role, "admin")

    def test_ineligible_or_inactive_agents_cannot_be_selected(self):
        for candidate in ("buyer", "owner", "suspended", "unverified", "inactive_realtor"):
            for kind in ("listing", "owner", "lead"):
                with self.subTest(candidate=candidate, kind=kind):
                    self.assign(kind, candidate, expected=404)
        self.assertIsNone(self.db.get(Listing, self.property.id).assigned_realtor_id)
        self.assertIsNone(self.db.get(Lead, self.owner_lead.id).assigned_realtor_id)

    def test_non_admin_cannot_list_or_assign_and_anonymous_is_denied(self):
        for actor in ("buyer", "owner", "realtor", "suspended", "unverified"):
            self.assertEqual(self.client.get("/admin/agents", headers=self.headers[actor]).status_code, 403)
            for kind in ("listing", "owner", "lead"):
                self.assign(kind, "admin_a", actor=actor, expected=403)
        self.assertEqual(self.client.get("/admin/agents").status_code, 401)
        response = self.client.put(f"/admin/leads/{self.owner_lead.id}/assign", json={"realtor_id": str(self.users["admin_a"].id)})
        self.assertEqual(response.status_code, 401)

    def test_bad_agent_ids_are_validation_errors_and_supported_unassignments_still_work(self):
        self.assign("listing", "admin_a")
        self.assign("owner", "admin_a")
        for path in (f"/admin/listings/{self.property.id}/assign", f"/admin/users/{self.users['owner'].id}/assign-realtor"):
            response = self.client.put(path, headers=self.headers["admin_a"], json={"realtor_id": None})
            self.assertEqual(response.status_code, 204, response.text)
        for kind in ("listing", "owner", "lead"):
            path = {"listing": f"/admin/listings/{self.property.id}/assign", "owner": f"/admin/users/{self.users['owner'].id}/assign-realtor", "lead": f"/admin/leads/{self.owner_lead.id}/assign"}[kind]
            response = self.client.put(path, headers=self.headers["admin_a"], json={"realtor_id": "not-a-uuid"})
            self.assertEqual(response.status_code, 422, response.text)
