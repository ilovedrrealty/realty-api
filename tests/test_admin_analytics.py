"""Actual aggregates, UTC boundaries and private API checks on synthetic Postgres.

Never connects to production: the explicit test URL must be a loopback Postgres
database. Every run uses its own random schema and performs no provider calls.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from unittest import TestCase, skipUnless
from unittest.mock import patch
from uuid import UUID, uuid4

os.environ.update(
    DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-analytics-only",
    GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
    SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="",
)

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from config import settings
from database import Base, get_db
import models
from models.booking import Booking
from models.lead import Lead
from models.listing import Listing
from models.user import User
from routes import analytics
from services.analytics import build_admin_analytics


NOW = datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)
START = datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)


@skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires synthetic loopback Postgres")
class AdminAnalyticsTests(TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.get_backend_name() != "postgresql" or url.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Analytics tests require disposable loopback Postgres")
        cls.schema = "analytics_test_" + uuid4().hex
        bootstrap = create_engine(url)
        with bootstrap.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        bootstrap.dispose()
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema})
        Base.metadata.create_all(cls.engine)
        app = FastAPI()
        app.include_router(analytics.router)

        def local_db():
            with Session(cls.engine) as db:
                yield db

        app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(app)
        cls.headers = {}
        cls.ids = {}
        with Session(cls.engine) as db:
            for n, (name, role, verified, status) in enumerate([
                ("admin", "admin", True, "active"),
                ("buyer", "buyer", True, "active"),
                ("owner", "owner", True, "active"),
                ("realtor", "realtor", True, "active"),
                ("unverified", "buyer", False, "active"),
                ("suspended", "buyer", True, "suspended"),
            ], 100):
                uid = UUID(int=n)
                db.add(User(id=uid, email=name + "@example.invalid", display_name="Synthetic " + name,
                            role=role, email_verified=verified, status=status,
                            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))
                cls.ids[name] = uid
                cls.headers[name] = {"Authorization": "Bearer " + jwt.encode(
                    {"sub": str(uid), "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                    settings.jwt_secret, algorithm="HS256")}
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        with cls.engine.begin() as conn:
            conn.execute(text("SET search_path TO public"))
            assert cls.schema.startswith("analytics_test_") and len(cls.schema) == 47
            conn.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.engine.dispose()

    def setUp(self):
        with Session(self.engine) as db:
            db.query(Booking).delete()
            db.query(Lead).delete()
            db.query(Listing).delete()
            db.query(User).filter(User.id.notin_(list(self.ids.values()))).delete(synchronize_session=False)
            db.commit()

    def report(self, days=7, now=NOW):
        with Session(self.engine) as db:
            return build_admin_analytics(db, days, now=now).model_dump(mode="json")

    def populate(self):
        with Session(self.engine) as db:
            for n, role, when in [(201, "buyer", START), (202, "owner", NOW - timedelta(hours=1)), (203, "realtor", NOW)]:
                db.add(User(id=UUID(int=n), email=f"synthetic-private-{n}@example.invalid", display_name="Synthetic private user",
                            role=role, email_verified=True, status="active", created_at=when))
            fixtures = [
                (301, "active", "villa", "sale", "USD", START),
                (302, "active", "apartment", "rent", "DOP", NOW - timedelta(days=1)),
                (303, "active", "condo", "both", "EUR", NOW - timedelta(days=1)),
                (304, "pending_approval", "villa", "sale", "USD", NOW - timedelta(days=1)),
                (305, "draft", "land", "sale", "DOP", datetime(2020, 1, 1, tzinfo=timezone.utc)),
                (306, "archived", "commercial", "sale", "USD", datetime(2020, 1, 1, tzinfo=timezone.utc)),
            ]
            for n, status, kind, purpose, currency, when in fixtures:
                db.add(Listing(id=UUID(int=n), title="Synthetic private property", type=kind,
                               transaction=purpose, price=999999, location="Synthetic location",
                               currency=currency, submitted_by=self.ids["admin"], status=status,
                               images=[], features=[], created_at=when))
            db.flush()
            lead_fixtures = [
                (401, "buyer_interest", "new", START, None, None, None, None),
                (402, "seller_interest", "assigned", START + timedelta(days=1), {"internal_test": False}, START + timedelta(days=2), "synthetic-contact-id", None),
                (403, "rental_interest", "contacted", START + timedelta(days=1), {"internal_test": False}, None, None, "synthetic-private-provider-error"),
                (404, "property_inquiry", "closed", NOW - timedelta(hours=5), {"internal_test": False}, NOW - timedelta(hours=4), None, None),
                (405, "buyer_interest", "new", START + timedelta(days=2), {"internal_test": True}, None, None, None),
                (406, "booking", "new", START - timedelta(microseconds=1), None, None, None, None),
                (407, "buyer_interest", "new", NOW, {"internal_test": False}, None, None, None),
                (408, "unrecognized_legacy", "new", START + timedelta(days=1), {"internal_test": "true"}, None, None, None),
                (409, "buyer_interest", "new", START + timedelta(days=2), {"internal_test": False}, NOW + timedelta(days=1), None, None),
            ]
            for n, kind, status, when, tracking, contacted, contact_id, error in lead_fixtures:
                db.add(Lead(id=UUID(int=n), type=kind, status=status, created_at=when,
                            name="Synthetic private lead", email="synthetic-private@example.invalid",
                            message="Synthetic private customer message", tracking=tracking,
                            contacted_at=contacted, ghl_contact_id=contact_id, ghl_sync_error=error))
            for n, status, when in [(501, "pending", START), (502, "confirmed", START + timedelta(days=1)),
                                    (503, "cancelled", NOW - timedelta(hours=1)),
                                    (504, "confirmed", START - timedelta(microseconds=1)),
                                    (505, "confirmed", NOW)]:
                db.add(Booking(id=UUID(int=n), listing_id=UUID(int=301), status=status,
                               created_at=when, check_in="2027-01-01", check_out="2027-01-02",
                               total_price=999999, payment_status="captured", paypal_capture_id="synthetic-private-capture"))
            db.commit()

    def test_empty_data_is_real_zeroes_and_every_day_is_present(self):
        data = self.report()
        self.assertEqual(data["snapshot"]["accounts"], 6)
        self.assertEqual(data["snapshot"]["active_accounts"], 5)
        self.assertEqual(data["snapshot"]["active_listings"], 0)
        self.assertEqual(data["period"]["inquiries"]["received"], 0)
        self.assertEqual(len(data["daily"]), 7)
        self.assertEqual(data["daily"][0]["date"], "2026-10-19")
        self.assertEqual(data["daily"][-1]["date"], "2026-10-25")
        self.assertTrue(all(day["accounts_created"] == day["inquiries_received"] == day["booking_records_created"] == 0 for day in data["daily"]))

    def test_snapshot_and_cohort_are_distinct_and_no_money_is_combined(self):
        self.populate()
        data = self.report()
        snapshot, period = data["snapshot"], data["period"]
        self.assertEqual(snapshot["accounts"], 9)
        self.assertEqual(snapshot["active_accounts"], 8)
        self.assertEqual(snapshot["accounts_by_role"], {"admin": 1, "buyer": 4, "owner": 2, "realtor": 2, "other": 0})
        self.assertEqual(snapshot["listing_records"], 6)
        self.assertEqual(snapshot["active_listings"], 3)
        self.assertEqual(snapshot["active_listings_by_purpose"], {"sale": 1, "rent": 1, "both": 1, "other": 0})
        self.assertEqual(sum(snapshot["listings_by_status"].values()), 6)
        self.assertEqual(period["accounts_created"], 2)
        self.assertEqual(period["listings_created"], 4)
        self.assertEqual(period["booking_records_created"], 3)
        self.assertFalse(data["financial_values_reported"])
        self.assertFalse({"revenue", "monthly_revenue", "total_price", "average_price", "days_on_market"} & set(data))
        self.assertNotIn("999999", json.dumps(data))

    def test_explicit_boolean_internal_tests_and_legacy_unknowns_are_honest(self):
        self.populate()
        inquiries = self.report()["period"]["inquiries"]
        self.assertEqual(inquiries["received"], 6)
        self.assertEqual(inquiries["excluded_internal_tests"], 1)
        self.assertEqual(inquiries["unknown_tracking"], 2)
        self.assertEqual(inquiries["by_type"], {"buyer_interest": 2, "seller_interest": 1, "rental_interest": 1, "property_inquiry": 1, "booking": 0, "other": 1})
        self.assertEqual(inquiries["current_status"], {"new": 3, "assigned": 1, "contacted": 1, "closed": 1, "other": 0})
        self.assertEqual(inquiries["contact_recorded"], 2)
        self.assertEqual(inquiries["crm_linked"], 1)
        self.assertEqual(inquiries["crm_errors"], 1)

    def test_daily_counts_match_period_and_utc_bounds_exclude_end(self):
        self.populate()
        data = self.report()
        self.assertEqual(sum(d["accounts_created"] for d in data["daily"]), data["period"]["accounts_created"])
        self.assertEqual(sum(d["inquiries_received"] for d in data["daily"]), data["period"]["inquiries"]["received"])
        self.assertEqual(sum(d["booking_records_created"] for d in data["daily"]), data["period"]["booking_records_created"])
        self.assertEqual(data["daily"][0]["inquiries_received"], 1)
        self.assertEqual(data["window"]["start_at"], "2026-10-19T00:00:00Z")
        self.assertEqual(data["window"]["end_at"], "2026-10-25T15:00:00Z")

    def test_session_timezone_cannot_move_utc_buckets(self):
        with Session(self.engine) as db:
            db.add(Lead(id=UUID(int=601), type="buyer_interest", name="Synthetic edge", phone="+18095550101",
                        created_at=datetime(2026, 10, 20, 0, 30, tzinfo=timezone.utc)))
            db.commit()
            db.execute(text("SET TIME ZONE 'America/Santo_Domingo'"))
            data = build_admin_analytics(db, 7, now=NOW).model_dump(mode="json")
            self.assertEqual(data["daily"][0]["inquiries_received"], 0)
            self.assertEqual(data["daily"][1]["inquiries_received"], 1)
            db.execute(text("SET TIME ZONE 'UTC'"))

    def test_supported_windows_include_leap_and_year_boundaries(self):
        for days, now, first in [
            (7, datetime(2024, 3, 2, tzinfo=timezone.utc), "2024-02-25"),
            (30, datetime(2027, 1, 10, tzinfo=timezone.utc), "2026-12-12"),
            (90, datetime(2026, 10, 25, tzinfo=timezone.utc), "2026-07-28"),
        ]:
            data = self.report(days, now)
            self.assertEqual(len(data["daily"]), days)
            self.assertEqual(data["daily"][0]["date"], first)
        with self.assertRaises(ValueError): self.report(8)
        with self.assertRaises(ValueError): self.report(now=datetime(2026, 10, 25))

    def test_report_is_one_statement_and_never_selects_customer_fields(self):
        self.populate()
        statements = []
        def capture(_conn, _cursor, statement, _parameters, _context, _many): statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            data = self.report()
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements[0].lstrip().startswith("WITH inquiry_window AS"))
        serialized = json.dumps(data)
        for secret in ("example.invalid", "Synthetic private", "synthetic-private", "phone", "guest_email", "capture_id"):
            self.assertNotIn(secret, serialized)

    def test_admin_endpoint_is_private_and_real_non_admins_are_denied(self):
        for name, status in [(None, 401), ("buyer", 403), ("owner", 403), ("realtor", 403), ("unverified", 403), ("suspended", 403), ("admin", 200)]:
            response = self.client.get("/admin/analytics?days=7", headers=self.headers.get(name, {}))
            self.assertEqual(response.status_code, status, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["pragma"], "no-cache")
            self.assertIn("Authorization", response.headers["vary"])
        for days in (7, 30, 90):
            self.assertEqual(self.client.get(f"/admin/analytics?days={days}", headers=self.headers["admin"]).status_code, 200)

    def test_bad_windows_and_database_failure_never_echo_private_inputs_or_fake_zeroes(self):
        for query in ("days=8", "days=0", "days=91", "days=synthetic-private@example.invalid", "days=7.5"):
            response = self.client.get("/admin/analytics?" + query, headers=self.headers["admin"])
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("synthetic-private", response.text)
            self.assertNotIn("snapshot", response.json())
        with self.assertLogs("routes.analytics", level="WARNING") as logs, patch.object(analytics, "build_admin_analytics", side_effect=RuntimeError("synthetic-private-database-value")):
            response = self.client.get("/admin/analytics", headers=self.headers["admin"])
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json(), {"detail": "Analytics are temporarily unavailable"})
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("synthetic-private", " ".join(logs.output))
