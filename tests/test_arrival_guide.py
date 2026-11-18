"""Arrival-guide contracts using an owned disposable loopback schema."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from uuid import uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-arrival-guide-test",
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
from models.booking import Booking
from models.listing import Listing
from models.user import User
from routes import arrival_guide as guide_routes
from services import arrival_guide


def instant(day, hour=4):
    return datetime(2031, 1, day, hour, tzinfo=timezone.utc)


def content(message="Synthetic arrival directions"):
    return {
        "en": {"directions": message, "check_in": "", "check_out": "", "house_notes": ""},
        "es": {"directions": "Indicaciones de prueba", "check_in": "", "check_out": "", "house_notes": ""},
    }


class ArrivalWindowTests(unittest.TestCase):
    def test_dr_boundaries_and_strict_legacy_date_text(self):
        self.assertEqual(arrival_guide.stay_window("2031-01-03", "2031-01-05"), (instant(1), instant(6)))
        for start, end in ((None, "2031-01-05"), ("20310103", "2031-01-05"),
                           ("2031-01-31", "2031-01-05"), ("2031-02-30", "2031-03-03"),
                           ("2031-01-05", "2031-01-05"), ("9999-12-30", "9999-12-31")):
            with self.subTest(start=start, end=end):
                self.assertIsNone(arrival_guide.stay_window(start, end))


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires owned loopback PostgreSQL")
class ArrivalGuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if (url.get_backend_name() != "postgresql" or url.host != "127.0.0.1" or
                url.port != 55449 or url.database != "ildr_platform_test_20261025"):
            raise ValueError("Use the fixed disposable loopback PostgreSQL database")
        cls.schema = "arrival_guide_test_" + uuid4().hex
        cls.bootstrap = create_engine(url)
        cls.addClassCleanup(cls.cleanup_owned_schema)
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        options = f"-csearch_path={cls.schema} -clock_timeout=5000 -cstatement_timeout=10000"
        cls.engine = create_engine(url, connect_args={"options": options})
        Base.metadata.create_all(cls.engine, tables=(User.__table__, Listing.__table__, Booking.__table__))
        path = Path(__file__).resolve().parents[1] / "alembic/versions/87e9c4a1d620_private_stay_arrival_guides.py"
        spec = spec_from_file_location("arrival_guide_revision", path)
        migration = module_from_spec(spec)
        spec.loader.exec_module(migration)
        with cls.engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()

        def local_db():
            with Session(cls.engine) as session:
                yield session

        app = FastAPI()
        app.include_router(guide_routes.router)
        app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(app)
        cls.addClassCleanup(cls.client.close)

    @classmethod
    def cleanup_owned_schema(cls):
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        assert re.fullmatch(r"arrival_guide_test_[0-9a-f]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE'))
        finally:
            cls.bootstrap.dispose()

    def setUp(self):
        self.actors, self.tokens = {}, {}
        with Session(self.engine) as db:
            for name, role in (("owner", "owner"), ("other", "owner"), ("buyer", "buyer"),
                               ("realtor", "realtor"), ("admin", "admin"), ("unverified", "buyer")):
                actor = User(id=uuid4(), email=f"{uuid4()}@example.invalid", display_name="Synthetic actor",
                             role=role, status="active", email_verified=name != "unverified")
                db.add(actor)
                self.actors[name] = actor.id
                self.tokens[name] = jwt.encode({"sub": str(actor.id)}, settings.jwt_secret, algorithm="HS256")
            db.flush()
            self.listing = self.add_listing(db, "owner", "rent")
            self.sale = self.add_listing(db, "owner", "sale")
            self.both = self.add_listing(db, "owner", "both")
            self.unowned = self.add_listing(db, None, "rent")
            db.commit()
        self.booking = self.make_booking()
        self.owner_url = f"/bookings/listings/{self.listing}/arrival-guide"
        self.guest_url = f"/bookings/{self.booking}/arrival-guide"
        self.clock = patch.object(arrival_guide, "utc_now", return_value=instant(3))
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)

    def add_listing(self, db, owner, transaction):
        listing = Listing(id=uuid4(), title="Synthetic rental", type="villa", transaction=transaction,
                          price=100, location="Synthetic area", submitted_by=self.actors["owner"],
                          assigned_realtor_id=self.actors["realtor"], owner_id=self.actors.get(owner), status="active")
        db.add(listing)
        return listing.id

    def make_booking(self, holder="buyer", state="confirmed", check_in="2031-01-03", check_out="2031-01-05"):
        booking_id = uuid4()
        with Session(self.engine) as db:
            db.add(Booking(id=booking_id, listing_id=self.listing, buyer_id=self.actors.get(holder),
                           check_in=check_in, check_out=check_out, status=state, payment_status="unpaid",
                           guest_name="PRIVATE_GUEST_NAME", guest_email="private-guest@example.invalid",
                           notes="PRIVATE_BOOKING_NOTES"))
            db.commit()
        return booking_id

    def request(self, method, url, actor, status, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        if actor:
            headers["Authorization"] = "Bearer " + self.tokens[actor]
        response = self.client.request(method, url, headers=headers, **kwargs)
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["pragma"], "no-cache")
        self.assertEqual(response.headers["vary"], "Cookie, Authorization")
        self.assertNotIn("PRIVATE_", response.text)
        self.assertNotIn("private-guest@example.invalid", response.text)
        return response.json()

    def write(self, version, guide, status=200, actor="owner"):
        return self.request("PUT", self.owner_url, actor, status, json={"expected_version": version, "content": guide})

    def test_current_owner_only_and_compare_and_swap_clear(self):
        self.assertEqual(self.request("GET", self.owner_url, "owner", 200), {"version": 0, "content": None})
        for actor, status in ((None, 401), ("admin", 403), ("realtor", 403), ("buyer", 403), ("other", 404)):
            self.request("GET", self.owner_url, actor, status)
            self.write(0, content(), status, actor)
        for listing in (self.sale, self.both, self.unowned, uuid4()):
            self.request("GET", f"/bookings/listings/{listing}/arrival-guide", "owner", 404)

        self.assertEqual(self.write(0, content()), {"version": 1, "content": content()})
        self.write(0, content("An older edit"), 409)
        self.assertEqual(self.write(1, None), {"version": 2, "content": None})
        self.assertEqual(self.request("GET", self.owner_url, "owner", 200), {"version": 2, "content": None})
        self.request("GET", self.guest_url, "buyer", 404)

    def test_two_first_writes_accept_exactly_one(self):
        def save(message):
            return self.client.put(self.owner_url, headers={"Authorization": "Bearer " + self.tokens["owner"]},
                                   json={"expected_version": 0, "content": content(message)}).status_code
        with ThreadPoolExecutor(max_workers=2) as workers:
            self.assertEqual(sorted(workers.map(save, ("First draft", "Second draft"))), [200, 409])
        self.assertEqual(self.request("GET", self.owner_url, "owner", 200)["version"], 1)

    def test_booking_access_uses_real_scope_and_dr_window(self):
        self.write(0, content())
        missing = self.request("GET", f"/bookings/{uuid4()}/arrival-guide", "buyer", 404)
        self.assertEqual(self.request("GET", self.guest_url, "other", 404), missing)
        self.request("GET", self.guest_url, None, 401)
        self.request("GET", self.guest_url, "unverified", 403)
        for holder, state, date in ((None, "confirmed", "2031-01-03"), ("buyer", "pending", "2031-01-03"),
                                    ("buyer", "cancelled", "2031-01-03"), ("buyer", "confirmed", "bad-date")):
            booking = self.make_booking(holder, state, date)
            self.assertEqual(self.request("GET", f"/bookings/{booking}/arrival-guide", "buyer", 404), missing)

        for value, expected in ((instant(1, 3), 404), (instant(1), 200), (instant(6, 3), 200), (instant(6), 404)):
            self.now.return_value = value
            result = self.request("GET", self.guest_url, "buyer", expected)
            if expected == 200:
                self.assertEqual(set(result), {"content", "available_until"})
                self.assertEqual(result["content"], content())
                self.assertEqual(datetime.fromisoformat(result["available_until"]), instant(6))
        self.now.return_value = instant(3)
        realtor_stay = self.make_booking("realtor")
        self.request("GET", f"/bookings/{realtor_stay}/arrival-guide", "realtor", 200)
        with Session(self.engine) as db:
            db.get(Listing, self.listing).status = "archived"
            db.commit()
            db.get(Booking, self.booking).status = "cancelled"
            db.commit()
        self.request("GET", f"/bookings/{realtor_stay}/arrival-guide", "realtor", 200)
        self.request("GET", self.guest_url, "buyer", 404)

    def test_owner_transfer_clears_guide_even_if_listing_returns(self):
        self.write(0, content())
        for owner, version in (("other", 2), ("owner", 3)):
            with Session(self.engine) as db:
                db.get(Listing, self.listing).owner_id = self.actors[owner]
                db.commit()
            self.assertEqual(self.request("GET", self.owner_url, owner, 200), {"version": version, "content": None})
            self.request("GET", self.guest_url, "buyer", 404)
            self.write(1, content("An older owner's draft"), 409, owner)
        self.assertEqual(self.write(3, content("New instructions"))["version"], 4)

    def test_validation_failures_and_cookie_origin_are_private(self):
        blank = {language: {key: " " for key in fields} for language, fields in content().items()}
        for body in ({"expected_version": True, "content": None}, {"expected_version": 0},
                     {"expected_version": 0, "content": blank},
                     {"expected_version": 0, "content": content("PRIVATE_" + "a" * 1500)},
                     {"expected_version": 0, "content": content("PRIVATE_\x00")},
                     {"expected_version": 0, "content": None, "extra": "PRIVATE_BODY_SENTINEL"}):
            self.request("PUT", self.owner_url, "owner", 422, json=body)
        self.request("GET", "/bookings/PRIVATE_INVALID_ID/arrival-guide", "buyer", 422)
        cookie = {"Cookie": "ildr_token=" + self.tokens["owner"]}
        self.request("PUT", self.owner_url, None, 403, headers=cookie,
                     json={"expected_version": 0, "content": None})
        self.request("PUT", self.owner_url, None, 403, headers={**cookie, "Origin": "https://untrusted.invalid"},
                     json={"expected_version": 0, "content": None})
        self.request("PUT", self.owner_url, None, 200, headers={**cookie, "Origin": "http://testserver"},
                     json={"expected_version": 0, "content": content()})
        self.request("PUT", self.owner_url, "owner", 415, headers={"Content-Type": "text/plain"}, content="PRIVATE_RAW")
        self.request("PUT", self.owner_url, "owner", 413, headers={"Content-Type": "application/json"},
                     content="x" * (guide_routes.MAX_GUIDE_BODY + 1))
        with patch.object(arrival_guide, "read_guest_guide", side_effect=RuntimeError("PRIVATE_DRIVER_SENTINEL")):
            self.request("GET", self.guest_url, "buyer", 503)

    def test_migration_denies_direct_public_roles(self):
        with self.engine.connect() as db:
            self.assertTrue(db.execute(text(
                "SELECT relrowsecurity FROM pg_class WHERE oid = 'listing_arrival_guides'::regclass")).scalar_one())
            self.assertFalse(db.execute(text("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_class, aclexplode(coalesce(relacl, acldefault('r', relowner))) acl
                    WHERE oid = 'listing_arrival_guides'::regclass AND acl.grantee = 0)
            """)).scalar_one())
            for role in ("anon", "authenticated", "service_role"):
                allowed = db.execute(text("""
                    SELECT has_table_privilege(oid, 'listing_arrival_guides', 'SELECT, INSERT, UPDATE, DELETE')
                    FROM pg_roles WHERE rolname = :role
                """), {"role": role}).scalar_one_or_none()
                self.assertNotEqual(allowed, True)
