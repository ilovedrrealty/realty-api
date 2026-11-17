"""Viewing contracts on a disposable loopback PostgreSQL schema."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from uuid import uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-viewing-test",
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
from models.user import User
from routes import viewings as routes
from services import viewing


NOW = datetime(2031, 1, 1, tzinfo=timezone.utc)
START = "2031-01-03T09:00:00-04:00"
LATER = "2031-01-03T09:30:00-04:00"
OVERLAP = "2031-01-03T09:15:00-04:00"
MEET = "https://meet.google.com/abc-defg-hij"


class ViewingLinkTests(unittest.TestCase):
    def test_scheduling_and_join_links_have_separate_https_policies(self):
        schedule = (
            "https://calendly.com/synthetic/viewing",
            "https://calendar.app.google/synthetic",
            "https://calendar.google.com/calendar/appointments/schedules/synthetic",
        )
        for url in schedule:
            self.assertEqual(viewing.scheduling_url(url), url)
            self.assertIsNone(viewing.meeting_url(url))
        meetings = (MEET, "https://us02web.zoom.us/j/123456789", "https://teams.live.com/meet/synthetic")
        for url in meetings:
            self.assertEqual(viewing.meeting_url(url), url)
            self.assertIsNone(viewing.scheduling_url(url))
        for url in ("http://calendly.com/synthetic/viewing", "https://calendly.com.untrusted.invalid/synthetic",
                    "https://fake@calendly.com/synthetic", "https://meet.google.com:9000/abc-defg-hij",
                    "https://calendar.google.com/accounts/synthetic", "https://meet.google.com/\r\n",
                    "https://unknown.invalid/synthetic"):
            self.assertIsNone(viewing.scheduling_url(url))
            self.assertIsNone(viewing.meeting_url(url))


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires owned loopback PostgreSQL")
class PropertyViewingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if (url.get_backend_name() != "postgresql" or url.host != "127.0.0.1" or
                url.port != 55449 or url.database != "ildr_platform_test_20261025"):
            raise ValueError("Use the fixed disposable loopback PostgreSQL database")
        cls.schema = "property_viewing_test_" + uuid4().hex
        cls.bootstrap = create_engine(url)
        cls.addClassCleanup(cls.cleanup_schema)
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        options = f"-csearch_path={cls.schema} -clock_timeout=5000 -cstatement_timeout=10000"
        cls.engine = create_engine(url, connect_args={"options": options})
        Base.metadata.create_all(cls.engine, tables=(User.__table__, Listing.__table__))
        path = Path(__file__).resolve().parents[1] / "alembic/versions/a8f31b7c6d52_private_property_viewings.py"
        spec = spec_from_file_location("viewing_revision", path)
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
        assert re.fullmatch(r"property_viewing_test_[0-9a-f]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE'))
        finally:
            cls.bootstrap.dispose()

    def setUp(self):
        self.ids, self.tokens, self.listings = {}, {}, {}
        with Session(self.engine) as db:
            for name, role, calendar in (
                ("agent", "realtor", "https://calendly.com/synthetic/showing"),
                ("other_agent", "realtor", "https://calendar.app.google/synthetic"),
                ("assigned_admin", "admin", "https://calendar.google.com/calendar/appointments/schedules/synthetic"),
                ("unassigned_admin", "admin", None), ("owner", "owner", None),
                ("buyer", "buyer", None), ("other_buyer", "buyer", None), ("unverified", "buyer", None),
                ("unavailable_agent", "realtor", None),
            ):
                actor = User(id=uuid4(), email=f"{uuid4()}@example.invalid", display_name=f"Synthetic {name}",
                             role=role, status="suspended" if name == "unavailable_agent" else "active",
                             email_verified=name != "unverified", calendly_url=calendar,
                             phone="PRIVATE_PHONE")
                db.add(actor)
                self.ids[name] = actor.id
                self.tokens[name] = jwt.encode({"sub": str(actor.id)}, settings.jwt_secret, algorithm="HS256")
            db.flush()
            for key, agent, state in (("first", "agent", "active"), ("second", "other_agent", "active"),
                                      ("same_agent", "agent", "active"), ("admin_listing", "assigned_admin", "active"),
                                      ("unassigned", None, "active"), ("archived", "agent", "archived"),
                                      ("inactive_assignee", "unavailable_agent", "active")):
                identifier = uuid4()
                db.add(Listing(id=identifier, title=f"Rental {key}", type="villa", transaction="rent",
                               price=10, location="Synthetic area", owner_id=self.ids["owner"], status=state,
                               submitted_by=self.ids["agent"], assigned_realtor_id=self.ids.get(agent),
                               co_listing_notes="PRIVATE_LISTING", maps_url="PRIVATE_MAP"))
                self.listings[key] = identifier
            db.commit()
        clock = patch.object(viewing, "now", return_value=NOW)
        self.clock = clock.start()
        self.addCleanup(clock.stop)

    def request(self, actor, method, path, status, *, data=None, headers=None, **kwargs):
        headers = dict(headers or {})
        if actor is not None:
            headers["Authorization"] = "Bearer " + self.tokens[actor]
        if data is not None:
            kwargs["json"] = data
        result = self.client.request(method, "/viewings" + path, headers=headers, **kwargs)
        self.assertEqual(result.status_code, status, result.text)
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertEqual(result.headers["pragma"], "no-cache")
        self.assertEqual(result.headers["vary"], "Cookie, Authorization")
        self.assertNotIn("PRIVATE_", result.text)
        self.assertNotIn("example.invalid", result.text)
        return result.json()

    def stop(self, listing="first", *, mode="in_person", starts=START, note="Synthetic viewing request"):
        return {"listing_id": str(self.listings[listing]), "mode": mode, "starts_at": starts, "note": note}

    def itinerary(self, *stops):
        return {"request_id": str(uuid4()), "title": "Synthetic property tour", "stops": list(stops)}

    def request_stop(self, buyer="buyer", listing="first", **kwargs):
        return self.request(buyer, "POST", "", 201, data=self.itinerary(self.stop(listing, **kwargs)))["items"][0]

    def change(self, actor, stop, name, status=200, version=1, **values):
        return self.request(actor, "POST", f"/{stop['id']}/action", status,
                            data={"expected_version": version, "action": name, **values})

    def confirm(self, actor, stop, status=200, **fields):
        return self.change(actor, stop, "confirm", status, availability_checked=True,
                           meeting_details="Meet in the lobby", **fields)

    def test_contact_and_role_access_require_current_exact_assignment(self):
        path = f"/contact/{self.listings['first']}"
        for actor, status in ((None, 401), ("owner", 403), ("agent", 403), ("unverified", 403)):
            self.request(actor, "GET", path, status)
        info = self.request("buyer", "GET", path, 200)
        self.assertEqual(set(info), {"listing", "agent_name", "scheduling_url"})
        self.assertEqual(info["agent_name"], "Synthetic agent")
        self.assertEqual(info["scheduling_url"], "https://calendly.com/synthetic/showing")
        self.assertEqual(set(info["listing"]), {"id", "title", "location"})
        for listing in ("unassigned", "inactive_assignee", "archived"):
            self.request("buyer", "GET", f"/contact/{self.listings[listing]}", 404)
        with Session(self.engine) as db:
            db.get(User, self.ids["agent"]).calendly_url = "https://calendly.com.untrusted.invalid/synthetic"
            db.commit()
        self.assertIsNone(self.request("buyer", "GET", path, 200)["scheduling_url"])
        self.request("buyer", "GET", "/assigned", 403)
        self.request("agent", "GET", "/mine", 403)

    def test_named_itinerary_replay_and_agent_isolation(self):
        body = self.itinerary(self.stop("second", starts=LATER, note="Agent two only"), self.stop(),
                              self.stop("admin_listing", starts=OVERLAP))
        result = self.request("buyer", "POST", "", 201, data=body)
        self.assertEqual(set(result), {"id", "title", "items"})
        self.assertEqual(self.request("buyer", "POST", "", 201, data=body), result)
        self.request("buyer", "POST", "", 409, data={**body, "title": "Different request"})
        self.assertEqual(self.request("buyer", "GET", "/mine", 200)["items"], [result])
        self.assertEqual(self.request("other_buyer", "GET", "/mine", 200)["items"], [])
        self.assertEqual([item["starts_at"] for item in result["items"]], [START, OVERLAP, LATER])
        for name, index in (("agent", 0), ("assigned_admin", 1), ("other_agent", 2)):
            self.assertEqual(self.request(name, "GET", "/assigned", 200)["items"], [result["items"][index]])
        self.assertEqual(self.request("unassigned_admin", "GET", "/assigned", 200)["items"], [])
        own = result["items"][0]
        self.assertEqual(set(own), {"id", "version", "listing", "status", "mode", "starts_at", "timezone",
                                   "agent_name", "attendee_name", "note", "meeting_details", "meeting_url", "scheduling_url"})
        self.assertEqual((own["status"], own["timezone"], own["version"]), ("requested", "America/Santo_Domingo", 1))
        self.change("unassigned_admin", own, "decline", 404)
        self.change("other_buyer", own, "cancel", 404)
        self.request("buyer", "POST", "", 404, data=self.itinerary(self.stop(), self.stop("unassigned")))
        self.assertEqual(self.request("buyer", "GET", "/mine", 200)["items"], [result])
        assigned_admin_stop = self.confirm("assigned_admin", result["items"][1])
        self.assertEqual(assigned_admin_stop["status"], "confirmed")

    def test_proposal_confirmation_and_meeting_inputs(self):
        in_person = self.request_stop()
        self.change("buyer", in_person, "confirm", 409, availability_checked=True, meeting_details="Lobby")
        self.change("agent", in_person, "confirm", 422, availability_checked=True)
        self.confirm("agent", in_person, 409, starts_at=LATER)
        done = self.confirm("agent", in_person)
        self.assertEqual((done["status"], done["version"], done["meeting_details"]), ("confirmed", 2, "Meet in the lobby"))
        self.change("buyer", in_person, "cancel", 409)
        closed = self.change("buyer", in_person, "cancel", version=2)
        self.assertEqual((closed["status"], closed["meeting_details"]), ("cancelled", None))

        virtual = self.request_stop(listing="second", mode="virtual", starts=LATER)
        self.change("other_agent", virtual, "propose", 422, starts_at=OVERLAP, availability_checked=False, meeting_url=MEET)
        self.change("other_agent", virtual, "propose", 422, starts_at=OVERLAP, availability_checked=True,
                    meeting_url="https://calendly.com/synthetic/showing")
        proposed = self.change("other_agent", virtual, "propose", starts_at=OVERLAP,
                               availability_checked=True, meeting_url=MEET)
        self.assertEqual((proposed["status"], proposed["starts_at"]), ("proposed", OVERLAP))
        self.change("other_agent", virtual, "accept", 409, version=2)
        received = self.change("buyer", virtual, "accept", version=2)
        self.assertEqual((received["status"], received["meeting_url"], received["version"]), ("confirmed", MEET, 3))

    def test_overlap_and_adjacency_for_buyer_and_agent(self):
        first = self.request_stop()
        self.confirm("agent", first)
        same_buyer = self.request_stop(listing="second", starts=OVERLAP)
        self.confirm("other_agent", same_buyer, 409)
        same_agent = self.request_stop(buyer="other_buyer", listing="same_agent", starts=OVERLAP)
        self.confirm("agent", same_agent, 409)
        next_slot = self.request_stop(listing="same_agent", starts=LATER)
        self.assertEqual(self.confirm("agent", next_slot)["starts_at"], LATER)

    def test_concurrent_confirmation_accepts_exactly_one_overlapping_visit(self):
        first = self.request_stop()
        second = self.request_stop(buyer="other_buyer", listing="same_agent")

        def confirm(stop):
            return self.client.post(f"/viewings/{stop['id']}/action",
                                    headers={"Authorization": f"Bearer {self.tokens['agent']}"},
                                    json={"expected_version": 1, "action": "confirm",
                                          "availability_checked": True, "meeting_details": "Lobby"}).status_code

        with ThreadPoolExecutor(max_workers=2) as workers:
            self.assertEqual(sorted(workers.map(confirm, (first, second))), [200, 409])

    def test_reassignment_and_archive_do_not_revive_old_viewings(self):
        first = self.request_stop()
        self.confirm("agent", first)
        second = self.request_stop(listing="second")

        with Session(self.engine) as db:
            db.get(Listing, self.listings["first"]).assigned_realtor_id = self.ids["other_agent"]
            db.get(Listing, self.listings["second"]).status = "archived"
            db.commit()
            db.get(Listing, self.listings["first"]).assigned_realtor_id = self.ids["agent"]
            db.get(Listing, self.listings["second"]).status = "active"
            db.commit()

        for actor in ("agent", "other_agent"):
            self.assertEqual(self.request(actor, "GET", "/assigned", 200)["items"], [])
        for itinerary in self.request("buyer", "GET", "/mine", 200)["items"]:
            item, = itinerary["items"]
            self.assertEqual((item["status"], item["meeting_details"], item["scheduling_url"]), ("unavailable", None, None))
        self.change("agent", first, "cancel", 404, version=3)
        self.change("buyer", second, "cancel", 404, version=2)
        self.assertEqual(self.request_stop()["status"], "requested")

    def test_validation_errors_and_origin_are_private(self):
        for changes in ({"starts_at": "2031-01-03T09:00:00"}, {"starts_at": "2031-01-03T09:00:00Z"},
                        {"starts_at": "2031-01-03T09:00:00-05:00"}, {"starts_at": "2030-12-01T09:00:00-04:00"},
                        {"starts_at": "9999-12-31T23:59:59-04:00"}, {"note": "PRIVATE_\x00"},
                        {"note": "PRIVATE_" + "x" * 1000}, {"agent_id": "PRIVATE_FAKE_AGENT"}):
            self.request("buyer", "POST", "", 422, data=self.itinerary({**self.stop(), **changes}))
        self.request("buyer", "POST", "", 422, data=self.itinerary(self.stop(), self.stop()))
        self.request("buyer", "POST", "", 422, data=self.itinerary())
        self.request("buyer", "POST", "", 413, headers={"Content-Type": "application/json"},
                     content="x" * (routes.MAX_BODY + 1))
        self.request("buyer", "GET", "/contact/PRIVATE_BAD_ID", 422)
        cookie = {"Cookie": "ildr_token=" + self.tokens["buyer"]}
        self.request(None, "POST", "", 403, headers={**cookie, "Origin": "https://untrusted.invalid"},
                     data=self.itinerary(self.stop()))
        with patch.object(viewing, "mine", side_effect=RuntimeError("PRIVATE_DRIVER_SENTINEL")):
            self.request("buyer", "GET", "/mine", 503)

    def test_migration_keeps_provider_roles_out(self):
        with self.engine.connect() as db:
            for table in ("viewing_itineraries", "property_viewings"):
                self.assertTrue(db.execute(text(
                    "SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(:table)"
                ), {"table": table}).scalar_one())
                self.assertFalse(db.execute(text("""
                    SELECT EXISTS (SELECT 1 FROM pg_class, aclexplode(coalesce(relacl, acldefault('r', relowner))) acl
                                   WHERE oid = to_regclass(:table) AND acl.grantee = 0)
                """), {"table": table}).scalar_one())
                for role in ("anon", "authenticated", "service_role"):
                    allowed = db.execute(text("""
                        SELECT has_table_privilege(oid, :table, 'SELECT,INSERT,UPDATE,DELETE')
                        FROM pg_roles WHERE rolname = :role
                    """), {"table": table, "role": role}).scalar_one_or_none()
                    self.assertNotEqual(allowed, True)
