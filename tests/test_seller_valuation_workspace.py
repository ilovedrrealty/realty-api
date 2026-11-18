"""Seller valuation contracts in a disposable loopback PostgreSQL schema."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import re
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-seller-valuation-workspace-test",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
                  SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from config import settings
from database import Base, get_db
from models.lead import Lead
from models.listing import Listing
from models.seller_valuation import SellerValuationIntake
from models.user import User
from routes import seller_valuation as routes
from schemas.seller_valuation import SellerValuationSubject


SUBJECT = {"property_type": "villa", "location": "Synthetic neighborhood", "area_m2": "125.75",
           "bedrooms": 3, "bathrooms": "2.5", "condition": "good"}
INTAKE = SellerValuationSubject.model_validate({**SUBJECT, "location": "Synthetic original area"}).model_dump(mode="json")
RANGE = {"outcome": "range", "notes": "Reviewed synthetic comparable", "price_low": "249000.00",
         "price_high": "285000.00", "currency": "USD"}
SOLD = {"property_type": "villa", "location": "Synthetic sale area", "price": "260000.00", "currency": "USD",
        "closed_on": "2024-02-12", "source_label": "Synthetic registry", "source_reference": "Fixture deed 1042",
        "area_m2": "120.00", "bedrooms": 3, "bathrooms": "2.5"}
MUTATION_OK = (200, 201)


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires owned loopback PostgreSQL")
class SellerValuationWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if (url.get_backend_name() != "postgresql" or url.host != "127.0.0.1" or
                url.port != 55449 or url.database != "ildr_platform_test_20261025"):
            raise ValueError("Use the fixed disposable loopback PostgreSQL database")
        cls.schema = "seller_valuation_test_" + uuid4().hex
        cls.bootstrap = create_engine(url)
        cls.addClassCleanup(cls.cleanup_schema)
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        options = f"-csearch_path={cls.schema} -clock_timeout=5000 -cstatement_timeout=10000"
        cls.engine = create_engine(url, connect_args={"options": options})
        Base.metadata.create_all(cls.engine, tables=(User.__table__, Listing.__table__, Lead.__table__))
        versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
        with cls.engine.begin() as connection:
            operation = Operations(MigrationContext.configure(connection))
            for filename in ("064b4d131f47_private_seller_valuation_intake.py",
                             "17d8b2c40aa6_private_seller_valuation_workspace.py"):
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
        app.include_router(routes.router)
        app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(app)
        cls.addClassCleanup(cls.client.close)

    @classmethod
    def cleanup_schema(cls):
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        assert re.fullmatch(r"seller_valuation_test_[0-9a-f]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE'))
        finally:
            cls.bootstrap.dispose()

    def setUp(self):
        self.ids, self.tokens, self.leads, self.listings = {}, {}, {}, {}
        with Session(self.engine) as db:
            for name, role, status, verified in (
                ("admin", "admin", "active", True), ("assigned", "realtor", "active", True),
                ("other", "realtor", "active", True), ("owner", "owner", "active", True),
                ("buyer", "buyer", "active", True), ("incomplete", "admin", "active", False),
                ("suspended", "admin", "suspended", True),
            ):
                user = User(id=uuid4(), email=f"{uuid4()}@example.invalid", display_name=f"Synthetic {name}",
                            role=role, status=status, email_verified=verified, phone="PRIVATE_USER_PHONE")
                self.ids[name] = user.id
                self.tokens[name] = jwt.encode({"sub": str(user.id)}, settings.jwt_secret, algorithm="HS256")
                db.add(user)
            db.flush()
            for name, lead_type, assigned in (
                ("seller", "seller_interest", "assigned"), ("historical", "seller_interest", "assigned"),
                ("other_seller", "seller_interest", "other"), ("unassigned", "seller_interest", None),
                ("buyer", "buyer_interest", "assigned"),
            ):
                lead = Lead(id=uuid4(), type=lead_type, name="PRIVATE_LEAD_NAME", email="lead@example.invalid",
                            phone="PRIVATE_LEAD_PHONE", message="PRIVATE_LEAD_MESSAGE", status="assigned",
                            assigned_realtor_id=self.ids.get(assigned), ghl_contact_id="PRIVATE_GHL_CONTACT")
                self.leads[name] = lead.id
                db.add(lead)
            db.flush()
            db.add(SellerValuationIntake(lead_id=self.leads["seller"], subject=INTAKE))
            # Asking candidates may include active listings assigned to a different Realtor.
            for name, purpose, listing_status in (
                ("sale", "sale", "active"), ("both", "both", "active"), ("rental", "rent", "active"),
                ("archived", "sale", "archived"), ("draft", "sale", "draft"),
            ):
                listing = Listing(id=uuid4(), title=f"Synthetic {name} {uuid4().hex[:8]}", type="villa",
                                  transaction=purpose, status=listing_status, price=Decimal("275000.00"),
                                  currency="USD", location=SUBJECT["location"], area_sqft=1350,
                                  bedrooms=3, bathrooms=Decimal("2.5"), submitted_by=self.ids["other"],
                                  owner_id=self.ids["owner"], assigned_realtor_id=self.ids["other"],
                                  co_listing_notes="PRIVATE_LISTING_NOTES", co_listing_agent_email="agent@example.invalid")
                self.listings[name] = listing.id
                db.add(listing)
            db.commit()
        # TestClient uses an ASGI transport, so a newly introduced outward HTTP call fails the test.
        for name in ("httpx.HTTPTransport.handle_request", "httpx.AsyncHTTPTransport.handle_async_request"):
            guard = patch(name, side_effect=AssertionError("Seller valuation must stay internal"))
            transport = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(transport.assert_not_called)

    def request(self, actor, method, suffix="", expected=200, *, lead="seller", body=None, **kwargs):
        lead_id = self.leads[lead] if isinstance(lead, str) else lead
        if actor is not None:
            kwargs["headers"] = {"Authorization": "Bearer " + self.tokens[actor]}
        if body is not None:
            kwargs["json"] = body
        return self.checked(self.client.request(method, f"/staff/leads/{lead_id}/valuation{suffix}", **kwargs), expected)

    def checked(self, response, expected):
        statuses = (expected,) if isinstance(expected, int) else expected
        self.assertIn(response.status_code, statuses, response.text)
        self.assertNotIn("PRIVATE_", response.text)
        self.assertNotIn("example.invalid", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()

    def draft(self, version=0, subject=SUBJECT, *, lead="seller", actor="assigned", expected=MUTATION_OK):
        return self.request(actor, "PUT", "/draft", expected, lead=lead,
                            body={"expected_version": version, "subject": subject})

    def comparable(self, kind, version, fields, *, lead="seller", expected=MUTATION_OK):
        return self.request("assigned", "POST", f"/comparables/{kind}", expected, lead=lead,
                            body={"expected_version": version, **fields})

    def review(self, version, fields=RANGE, *, lead="seller", expected=MUTATION_OK):
        return self.request("assigned", "POST", "/review", expected, lead=lead,
                            body={"expected_version": version, **fields})

    def test_workspace_follows_current_assignment_and_excludes_non_sellers(self):
        self.request(None, "GET", expected=401)
        missing = self.request("admin", "GET", lead=uuid4(), expected=404)
        for actor in ("owner", "buyer"):
            self.assertEqual(self.request(actor, "GET", expected=404), missing)
        for actor in ("incomplete", "suspended"):
            self.request(actor, "GET", expected=403)
        self.request("other", "GET", expected=404)
        self.request("assigned", "GET", lead="other_seller", expected=404)
        self.request("assigned", "GET", lead="unassigned", expected=404)
        self.request("admin", "GET", lead="buyer", expected=404)
        self.assertEqual(self.request("admin", "GET")["intake_subject"], INTAKE)
        self.assertEqual(self.draft()["version"], 1)

        with Session(self.engine) as db:
            db.get(Lead, self.leads["seller"]).assigned_realtor_id = self.ids["other"]
            db.commit()
        self.request("assigned", "GET", expected=404)
        self.request("assigned", "GET", "/asking-candidates", 404)
        self.draft(1, expected=404)
        self.assertEqual(self.request("other", "GET")["version"], 1)
        self.assertEqual(self.request("admin", "GET")["version"], 1)

    def test_intake_remains_original_and_historical_seller_can_start_blank(self):
        initial = self.request("assigned", "GET")
        self.assertEqual(initial["version"], 0)
        self.assertFalse(initial["started"])
        self.assertEqual(initial["intake_subject"], INTAKE)
        before = self.request("assigned", "GET", lead="historical")
        self.assertEqual(before["version"], 0)
        self.assertFalse(before["started"])
        self.assertIsNone(before["intake_subject"])
        self.assertTrue(all(value is None for value in (before["subject"] or {}).values()))

        history = self.draft(subject={}, lead="historical")
        self.assertEqual(history["version"], 1)
        self.assertTrue(history["started"])
        self.assertIsNone(history["intake_subject"])
        self.assertTrue(all(value is None for value in (history["subject"] or {}).values()))
        self.review(1, lead="historical", expected=422)
        self.assertEqual(self.request("assigned", "GET", lead="historical")["version"], 1)

        edited = self.draft(subject={**SUBJECT, "location": "  Updated synthetic area  "})
        self.assertEqual(edited["subject"]["location"], "Updated synthetic area")
        self.assertEqual(edited["intake_subject"], INTAKE)
        with Session(self.engine) as db:
            lead = db.get(Lead, self.leads["seller"])
            self.assertEqual((lead.status, lead.phone, lead.ghl_contact_id),
                             ("assigned", "PRIVATE_LEAD_PHONE", "PRIVATE_GHL_CONTACT"))
            self.assertEqual(db.get(SellerValuationIntake, lead.id).subject, INTAKE)
            self.assertIsNone(db.get(SellerValuationIntake, self.leads["historical"]))

    def test_asking_evidence_is_a_snapshot_of_an_active_sale_listing(self):
        self.draft()
        candidates = self.request("assigned", "GET", "/asking-candidates")["items"]
        ids = {item["listing_id"] for item in candidates}
        self.assertTrue({str(self.listings[key]) for key in ("sale", "both")} <= ids)
        self.assertTrue(ids.isdisjoint(str(self.listings[key]) for key in ("rental", "archived", "draft")))
        self.comparable("asking", 1, {"listing_id": str(self.listings["rental"])}, expected=(404, 422))
        captured = self.comparable("asking", 1, {"listing_id": str(self.listings["sale"])})
        self.assertEqual(captured["version"], 2)
        [asking] = captured["comparables"]
        self.assertEqual(asking["kind"], "asking")
        self.assertEqual(asking["listing_id"], str(self.listings["sale"]))
        self.assertEqual(asking["price"], "275000.00")
        self.assertEqual(asking["currency"], "USD")

        with Session(self.engine) as db:
            listing = db.get(Listing, self.listings["sale"])
            listing.price = Decimal("310000.00")
            listing.status = "archived"
            db.commit()
        reread = self.request("assigned", "GET")
        self.assertEqual(reread["comparables"], [asking])
        self.assertEqual(self.review(2)["review"]["status"], "current")

    def test_manual_sold_evidence_requires_verification_and_expiration_is_visible(self):
        self.draft()
        submitted = self.comparable("sold", 1, SOLD)
        [sold] = submitted["comparables"]
        self.assertEqual(sold["kind"], "sold")
        self.assertEqual(sold["source_reference"], SOLD["source_reference"])
        self.assertEqual(Decimal(sold["price"]), Decimal(SOLD["price"]))
        self.review(2, expected=422)
        self.assertEqual(self.request("assigned", "GET")["version"], 2)

        path = f"/comparables/{sold['id']}/sold-review"
        self.draft(lead="historical")
        self.request("assigned", "PUT", path, 404, lead="historical",
                     body={"expected_version": 1, "decision": "verified", "review_note": "Different workspace"})
        rejected = self.request("assigned", "PUT", path, MUTATION_OK,
                                body={"expected_version": 2, "decision": "rejected", "review_note": "Source unclear"})
        self.assertEqual(rejected["comparables"][0]["sold_review"]["decision"], "rejected")
        self.review(3, expected=422)
        verified = self.request("admin", "PUT", path, MUTATION_OK,
                                body={"expected_version": 3, "decision": "verified", "review_note": "Checked fixture deed"})
        evidence = verified["comparables"][0]["sold_review"]
        self.assertEqual((evidence["decision"], evidence["review_note"], evidence["reviewed_by"]),
                         ("verified", "Checked fixture deed", str(self.ids["admin"])))
        self.assertTrue(evidence["reviewed_at"])
        reversed_bounds = {**RANGE, "price_low": "300000.00"}
        self.review(4, reversed_bounds, expected=422)
        current = self.review(4)
        self.assertEqual(current["version"], 5)
        self.assertEqual(current["review"]["status"], "current")
        self.assertEqual(Decimal(current["review"]["price_low"]), Decimal(RANGE["price_low"]))

        path = f"/comparables/{sold['id']}"
        self.request("assigned", "DELETE", path, 409, params={"expected_version": 4})
        removed = self.request("assigned", "DELETE", path, MUTATION_OK, params={"expected_version": 5})
        self.assertEqual(removed["version"], 6)
        self.assertEqual(removed["comparables"], [])
        self.assertEqual(removed["review"]["status"], "expired")
        self.assertEqual(self.request("assigned", "GET")["review"]["status"], "expired")

    def test_follow_up_completion_is_local_and_does_not_expire_the_review(self):
        self.draft()
        insufficient = {"outcome": "insufficient_evidence", "notes": "Need evidence from the owner"}
        self.assertEqual(self.review(1, insufficient)["review"]["status"], "current")
        follow_up = {"expected_version": 2, "follow_up_on": (date.today() + timedelta(days=7)).isoformat(),
                     "note": "Ask about documents at next meeting"}
        added = self.request("assigned", "POST", "/follow-ups", MUTATION_OK, body=follow_up)
        self.assertEqual(added["version"], 3)
        self.assertEqual(added["review"]["status"], "current")
        [task] = added["follow_ups"]
        self.assertEqual((task["follow_up_on"], task["note"]), (follow_up["follow_up_on"], follow_up["note"]))
        self.assertTrue(task["created_at"])
        self.assertIsNone(task["completed_at"])
        path = f"/follow-ups/{task['id']}/complete"
        self.request("other", "POST", path, 404, body={"expected_version": 3})
        self.request("assigned", "POST", path, 409, body={"expected_version": 2})
        completed = self.request("assigned", "POST", path, MUTATION_OK, body={"expected_version": 3})
        self.assertEqual(completed["version"], 4)
        self.assertEqual(completed["review"]["status"], "current")
        self.assertTrue(completed["follow_ups"][0]["completed_at"])
        self.assertEqual(self.request("assigned", "GET")["follow_ups"], completed["follow_ups"])
        self.assertEqual(self.draft(4)["version"], 4)
        edited = self.draft(4, {**SUBJECT, "location": "Another synthetic neighborhood"})
        self.assertEqual(edited["version"], 5)
        self.assertEqual(edited["review"]["status"], "expired")

    def test_simultaneous_writers_cannot_create_two_drafts_for_the_same_version(self):
        ready = Barrier(2, timeout=10)
        names = ("Synthetic first neighborhood", "Synthetic second neighborhood")

        def submit(name):
            ready.wait()
            return self.client.put(f"/staff/leads/{self.leads['seller']}/valuation/draft",
                                   headers={"Authorization": "Bearer " + self.tokens["assigned"]},
                                   json={"expected_version": 0, "subject": {**SUBJECT, "location": name}})

        with ThreadPoolExecutor(max_workers=2) as workers:
            responses = list(workers.map(submit, names))
        winners = [response for response in responses if response.is_success]
        self.assertEqual(len(winners), 1, [response.status_code for response in responses])
        [winner] = winners
        saved = self.checked(winner, MUTATION_OK)
        self.checked(next(response for response in responses if not response.is_success), 409)
        final = self.request("assigned", "GET")
        self.assertEqual(final["version"], 1)
        self.assertEqual(final["subject"]["location"], saved["subject"]["location"])
        self.assertIn(final["subject"]["location"], names)
