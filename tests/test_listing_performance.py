"""Listing performance and view recording against disposable loopback PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import re
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-listing-performance-guarded-tests",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
                  SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from config import settings
from database import Base, get_db
from models.inquiry import Inquiry
from models.lead import Lead
from models.listing import Listing
from models.listing_event import ListingEvent
from models.listing_performance import ListingDailyView, ListingPerformanceEntry, ListingViewCoverage
from models.user import User
from models.viewing import PropertyViewing, ViewingItinerary
from routes import listing_performance, listings
from utils.limiter import limiter


TIME_ZONE = "America/Santo_Domingo"
ENTRY_OK = (200, 201)


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires owned loopback PostgreSQL")
class ListingPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if (url.get_backend_name() != "postgresql" or url.host != "127.0.0.1" or
                url.port != 55449 or url.database != "ildr_platform_test_20261025"):
            raise ValueError("Use the fixed disposable loopback PostgreSQL database")
        cls.schema = "listing_performance_test_" + uuid4().hex
        cls.bootstrap = create_engine(url)
        cls.addClassCleanup(cls.cleanup_schema)
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        options = f"-csearch_path={cls.schema} -clock_timeout=5000 -cstatement_timeout=10000"
        cls.engine = create_engine(url, connect_args={"options": options})
        Base.metadata.create_all(cls.engine, tables=(User.__table__, Listing.__table__, Lead.__table__,
                                                     Inquiry.__table__, ListingEvent.__table__,
                                                     ListingDailyView.__table__, ListingViewCoverage.__table__,
                                                     ListingPerformanceEntry.__table__))
        versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
        with cls.engine.begin() as connection:
            operation = Operations(MigrationContext.configure(connection))
            for filename in ("a8f31b7c6d52_private_property_viewings.py",
                             "5a4e7f921bc0_private_listing_performance.py"):
                path = versions / filename
                spec = spec_from_file_location(path.stem, path)
                migration = module_from_spec(spec)
                spec.loader.exec_module(migration)
                migration.op = operation
                migration.upgrade()

        def local_db():
            with Session(cls.engine) as session:
                yield session

        app = FastAPI()
        app.state.limiter = limiter
        app.include_router(listings.router)
        app.include_router(listing_performance.router)
        app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(app)
        cls.addClassCleanup(cls.client.close)
        disabled_limiter = patch.object(limiter, "enabled", False)
        disabled_limiter.start()
        cls.addClassCleanup(disabled_limiter.stop)

    @classmethod
    def cleanup_schema(cls):
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        assert re.fullmatch(r"listing_performance_test_[0-9a-f]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE'))
        finally:
            cls.bootstrap.dispose()

    def setUp(self):
        self.zone = ZoneInfo(TIME_ZONE)
        self.today = datetime.now(self.zone).date()
        self.users, self.tokens, self.listings = {}, {}, {}
        with Session(self.engine) as db:
            db.execute(delete(ListingViewCoverage))
            for name, role, status, verified in (
                ("admin", "admin", "active", True), ("owner", "owner", "active", True),
                ("previous_owner", "owner", "active", True), ("buyer", "buyer", "active", True),
                ("agent", "realtor", "active", True), ("other_agent", "realtor", "active", True),
                ("suspended", "admin", "suspended", True), ("unverified", "admin", "active", False),
            ):
                user = User(id=uuid4(), email=f"{uuid4()}@example.invalid", display_name=f"Synthetic {name}",
                            role=role, status=status, email_verified=verified, phone="PRIVATE_USER_PHONE")
                db.add(user)
                self.users[name] = user.id
                self.tokens[name] = jwt.encode({"sub": str(user.id)}, settings.jwt_secret, algorithm="HS256")
            db.flush()
            for name, owner, submitter, agent, status in (
                ("main", "owner", "previous_owner", "agent", "active"),
                ("legacy", None, "owner", None, "active"),
                ("archived", "owner", "owner", "agent", "archived"),
                ("foreign", "previous_owner", "previous_owner", "other_agent", "active"),
            ):
                listing = Listing(id=uuid4(), title=f"Synthetic {name}", type="villa", transaction="sale",
                                  price=275000, currency="USD", location="Synthetic listing location", status=status,
                                  owner_id=self.users.get(owner), submitted_by=self.users[submitter],
                                  assigned_realtor_id=self.users.get(agent), view_count=13,
                                  co_listing_notes="PRIVATE_LISTING_NOTE", maps_url="PRIVATE_COORDINATE")
                db.add(listing)
                self.listings[name] = listing.id
            db.commit()

    def headers(self, actor):
        return {"Authorization": "Bearer " + self.tokens[actor]} if actor is not None else {}

    def get_performance(self, actor, listing="main", expected=200, *, start=None, end=None):
        listing_id = self.listings[listing] if isinstance(listing, str) else listing
        params = {name: value.isoformat() for name, value in (("start", start), ("end", end)) if value is not None}
        response = self.client.get(f"/listings/{listing_id}/performance", params=params, headers=self.headers(actor))
        return self.checked(response, expected)

    def checked(self, response, expected):
        statuses = (expected,) if isinstance(expected, int) else expected
        self.assertIn(response.status_code, statuses, response.text)
        self.assertNotIn("PRIVATE_", response.text)
        self.assertNotIn("example.invalid", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()

    def entry(self, actor, kind, day, *, listing="main", expected=ENTRY_OK, request_id=None, **values):
        body = {"request_id": str(request_id or uuid4()), "kind": kind, "occurred_on": day.isoformat(), **values}
        response = self.client.post(f"/listings/{self.listings[listing]}/performance/entries",
                                    json=body, headers=self.headers(actor))
        return self.checked(response, expected)

    def accept_view(self, listing):
        listing_id = self.listings[listing] if isinstance(listing, str) else listing
        response = self.client.post(f"/listings/{listing_id}/view")
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(response.content, b"")

    def test_owner_fallback_staff_scope_and_transfer_are_immediate(self):
        self.get_performance(None, expected=401)
        for actor in ("suspended", "unverified"):
            self.get_performance(actor, expected=403)
        missing = self.get_performance("admin", uuid4(), expected=404)
        for actor, listing in (("previous_owner", "main"), ("owner", "foreign"), ("buyer", "main"),
                               ("other_agent", "main"), ("agent", "legacy")):
            self.assertEqual(self.get_performance(actor, listing, 404), missing)
        self.assertFalse(self.get_performance("owner")["can_record"])
        self.assertFalse(self.get_performance("owner", "legacy")["can_record"])
        self.assertTrue(self.get_performance("agent")["can_record"])
        self.assertTrue(self.get_performance("admin", "foreign")["can_record"])
        self.entry("owner", "completed_showing", self.today, expected=403)

        with Session(self.engine) as db:
            listing = db.get(Listing, self.listings["main"])
            listing.owner_id = self.users["previous_owner"]
            listing.assigned_realtor_id = self.users["other_agent"]
            db.commit()
        self.get_performance("owner", expected=404)
        self.get_performance("agent", expected=404)
        self.entry("agent", "completed_showing", self.today, expected=404)
        self.assertFalse(self.get_performance("previous_owner")["can_record"])
        self.assertTrue(self.get_performance("other_agent")["can_record"])

    def test_open_bounds_and_default_range_use_dominican_dates(self):
        default = self.get_performance("owner")["range"]
        self.assertEqual(default, {"start": (self.today - timedelta(days=29)).isoformat(),
                                   "end": self.today.isoformat(), "time_zone": TIME_ZONE})
        maximum = self.get_performance("owner", start=self.today - timedelta(days=365), end=self.today)
        self.assertEqual(len(maximum["views"]["days"]), 366)
        self.get_performance("owner", start=self.today - timedelta(days=366), end=self.today, expected=422)
        self.get_performance("owner", start=self.today, end=self.today - timedelta(days=1), expected=422)
        far = self.today + timedelta(days=366)
        future = self.get_performance("owner", start=far, end=far)["views"]
        self.assertIsNone(future["range_total"])
        self.assertEqual(future["days"], [{"date": far.isoformat(), "count": None, "coverage": "unavailable"}])
        self.get_performance("owner", start=far, end=far + timedelta(days=1), expected=422)

    def test_view_route_records_lifetime_and_dominican_daily_counts_atomically(self):
        self.accept_view("archived")
        self.accept_view(uuid4())
        cold = self.get_performance("owner", start=self.today, end=self.today)["views"]
        self.assertEqual((cold["lifetime"], cold["coverage"]), (13, "unavailable"))
        self.assertIsNone(cold["tracked_from"])
        self.assertIsNone(cold["range_total"])

        attempts = 6
        ready = Barrier(attempts, timeout=10)

        def submit(_):
            ready.wait()
            self.accept_view("main")

        with ThreadPoolExecutor(max_workers=attempts) as workers:
            list(workers.map(submit, range(attempts)))
        with Session(self.engine) as db:
            self.assertEqual(db.get(Listing, self.listings["main"]).view_count, 13 + attempts)
            self.assertEqual(db.get(Listing, self.listings["archived"]).view_count, 13)
            rows = db.scalars(select(ListingDailyView).where(ListingDailyView.listing_id == self.listings["main"])).all()
            self.assertEqual(sum(row.view_count for row in rows), attempts)
            [coverage] = db.scalars(select(ListingViewCoverage)).all()
            self.assertEqual(coverage.id, 1)
            tracking_day = coverage.started_at.astimezone(self.zone).date()
            self.assertTrue(all(row.view_date in (tracking_day, tracking_day + timedelta(days=1)) for row in rows))

        before, after = tracking_day - timedelta(days=1), tracking_day + timedelta(days=2)
        views = self.get_performance("owner", start=before, end=after)["views"]
        self.assertEqual((views["lifetime"], views["coverage"]), (13 + attempts, "partial"))
        self.assertTrue(views["tracked_from"])
        self.assertEqual(views["range_total"], attempts)
        self.assertEqual(views["days"][0], {"date": before.isoformat(), "count": None, "coverage": "unavailable"})
        self.assertEqual(views["days"][1]["coverage"], "partial")
        self.assertEqual(views["days"][-1], {"date": after.isoformat(), "count": None, "coverage": "unavailable"})

    def test_range_excludes_internal_tests_and_returns_only_public_milestone_fields(self):
        day = self.today - timedelta(days=2)
        boundary = datetime.combine(day, time.min, tzinfo=self.zone).astimezone(timezone.utc)
        next_day = boundary + timedelta(days=1)
        with Session(self.engine) as db:
            db.add(ListingViewCoverage(id=1, started_at=boundary - timedelta(days=2, hours=1)))
            db.add(ListingDailyView(listing_id=self.listings["main"], view_date=day - timedelta(days=1), view_count=4))
            for kind, tracking, instant, listing in (
                ("property_inquiry", None, boundary, "main"),
                ("property_inquiry", {"internal_test": False}, boundary, "main"),
                ("property_inquiry", {"internal_test": "true"}, boundary, "main"),
                ("property_inquiry", {"internal_test": True}, boundary, "main"),
                ("property_inquiry", None, boundary - timedelta(microseconds=1), "main"),
                ("property_inquiry", None, next_day, "main"),
                ("property_inquiry", None, boundary, "foreign"),
                ("buyer_interest", None, boundary, "main"),
            ):
                db.add(Lead(id=uuid4(), type=kind, tracking=tracking, name="PRIVATE_LEAD", email="lead@example.invalid",
                            phone="PRIVATE_LEAD_PHONE", message="PRIVATE_LEAD_MESSAGE",
                            property_id=self.listings[listing], created_at=instant))
            for listing, instant in (("main", boundary), ("main", next_day), ("foreign", boundary)):
                db.add(Inquiry(id=uuid4(), listing_id=self.listings[listing], created_at=instant,
                               name="PRIVATE_LEGACY_INQUIRY", email="fixture@example.invalid"))
            for status, instant in (("confirmed", boundary), ("cancelled", boundary), ("requested", boundary),
                                    ("confirmed", next_day), ("confirmed", boundary - timedelta(microseconds=1))):
                itinerary = ViewingItinerary(id=uuid4(), buyer_id=self.users["buyer"], request_id=uuid4(),
                                             request_sha256="a" * 64, title="PRIVATE_ITINERARY", created_at=boundary)
                db.add(itinerary)
                db.flush()
                db.add(PropertyViewing(id=uuid4(), itinerary_id=itinerary.id, listing_id=self.listings["main"],
                                       agent_id=self.users["agent"], listing_title="Synthetic main",
                                       listing_location="Synthetic location", agent_name="PRIVATE_AGENT",
                                       attendee_name="PRIVATE_ATTENDEE", note="PRIVATE_VIEWING_NOTE", status=status,
                                       mode="in_person", starts_at=instant, version=1,
                                       meeting_details="PRIVATE_MEETING" if status == "confirmed" else None,
                                       availability_checked_at=boundary if status == "confirmed" else None))
            for event_type in ("submitted", "approved", "archived", "rejected", "edit_approved", "edit_submitted"):
                db.add(ListingEvent(id=uuid4(), listing_id=self.listings["main"], event_type=event_type,
                                    actor_id=self.users["admin"], note="PRIVATE_AUDIT_NOTE", created_at=boundary,
                                    snapshot_before={"label": "PRIVATE_BEFORE"}, snapshot_after={"label": "PRIVATE_AFTER"}))
            db.commit()
        self.entry("agent", "completed_showing", day)
        workspace = self.get_performance("owner", start=day, end=day)
        self.assertEqual(workspace["listing_id"], str(self.listings["main"]))
        self.assertEqual(workspace["status"], "active")
        self.assertEqual(workspace["totals"], {"property_inquiries": 4, "confirmed_viewings": 1,
                                               "recorded_completed_showings": 1})
        self.assertEqual(workspace["views"]["range_total"], 0)
        self.assertEqual(workspace["views"]["coverage"], "complete")
        self.assertEqual(workspace["views"]["days"], [{"date": day.isoformat(), "count": 0, "coverage": "complete"}])
        self.assertCountEqual(workspace["milestones"], [
            {"kind": kind, "occurred_on": day.isoformat()}
            for kind in ("submitted", "approved", "archived", "rejected", "edit_approved")
        ])
        [showing] = workspace["entries"]
        self.assertEqual((showing["kind"], showing["occurred_on"], showing["text"]),
                         ("completed_showing", day.isoformat(), None))
        self.assertFalse(workspace["milestones_truncated"])
        self.assertFalse(workspace["entries_truncated"])

    def test_staff_entries_require_sharing_and_idempotent_request_payloads(self):
        day = self.today - timedelta(days=1)
        self.entry("agent", "completed_showing", day, expected=422, text="Completed reports are date-only")
        for kind in ("owner_feedback", "marketing_update"):
            self.entry("agent", kind, day, expected=422, text="Synthetic unshared content")
            self.entry("agent", kind, day, expected=422, text="Not an explicit Boolean", share_with_owner=1)
            self.entry("agent", kind, day, expected=422, text="x" * 1001, share_with_owner=True)
        self.entry("buyer", "owner_feedback", day, expected=404, text="Synthetic note", share_with_owner=True)
        request_id = uuid4()
        shared_note = {"text": "Published the updated brochure", "share_with_owner": True}
        self.entry("agent", "marketing_update", day, request_id=request_id, **shared_note)
        self.entry("agent", "marketing_update", day, request_id=request_id, **shared_note)
        self.entry("agent", "marketing_update", day, request_id=request_id, expected=409,
                   text="Changed the replayed note", share_with_owner=True)
        self.entry("admin", "owner_feedback", day, text="Owner requested a call", share_with_owner=True)

        report = self.get_performance("owner", start=day, end=day)
        self.assertCountEqual([(item["kind"], item["text"]) for item in report["entries"]],
                              [("marketing_update", "Published the updated brochure"),
                               ("owner_feedback", "Owner requested a call")])
        self.assertTrue(all(set(item) == {"id", "kind", "occurred_on", "text"} for item in report["entries"]))
        self.assertEqual(report["totals"]["recorded_completed_showings"], 0)
