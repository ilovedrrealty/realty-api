"""Property-form intake in disposable loopback Postgres; no provider dispatch."""
import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID, uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-property-inquiry-tests",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
                  SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")
import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from config import settings
from database import Base, get_db
import models
from models.lead import Lead
from models.lead_delivery import LeadDeliveryJob
from models.listing import Listing
from models.site_settings import SiteSettings
from models.user import User
from routes import leads
from services import lead_delivery
from utils.limiter import limiter


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires disposable loopback Postgres")
class PropertyInquiryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host not in ("127.0.0.1", "localhost", "::1") or url.get_backend_name() != "postgresql":
            raise ValueError("Tests require disposable loopback Postgres")
        cls.schema = "property_inquiry_test_" + uuid4().hex
        bootstrap = create_engine(url)
        with bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        bootstrap.dispose()
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema})
        Base.metadata.create_all(cls.engine)
        app = FastAPI(); app.state.limiter = limiter; app.include_router(leads.router)
        def local_db():
            with Session(cls.engine) as db:
                yield db
        app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(app)
        cls.limiter_enabled = limiter.enabled; limiter.enabled = False
        cls.user_ids, cls.headers, cls.listings = {}, {}, {}
        with Session(cls.engine) as db:
            db.add(SiteSettings(id=1, data={"notify_email": "one@example.com,two@example.com"}))
            for role in ("buyer", "owner", "admin"):
                user = User(email=f"property-{role}@example.com", display_name="Synthetic actor", role=role,
                            status="active", email_verified=True)
                db.add(user); db.flush(); cls.user_ids[role] = user.id
                cls.headers[role] = {"Authorization": "Bearer " + jwt.encode({"sub": str(user.id),
                    "exp": datetime.now(timezone.utc)+timedelta(hours=1)}, settings.jwt_secret, algorithm="HS256")}
            for status in ("active", "draft", "archived", "pending_approval", "rejected"):
                listing = Listing(title="Synthetic private title " + status, type="condo", transaction="both", price=199000,
                                  location="Synthetic location", status=status, owner_id=cls.user_ids["owner"], submitted_by=cls.user_ids["owner"])
                db.add(listing); db.flush(); cls.listings[status] = listing.id
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.client.close(); limiter.enabled = cls.limiter_enabled
        assert re.fullmatch(r"property_inquiry_test_[a-f0-9]{32}", cls.schema)
        with cls.engine.begin() as connection:
            connection.execute(text("SET search_path TO public"))
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.engine.dispose()

    def setUp(self):
        with Session(self.engine) as db:
            db.query(LeadDeliveryJob).delete(); db.query(Lead).delete()
            db.get(Listing, self.listings["active"]).status = "active"
            db.commit()
        for target, name, value in ((lead_delivery, "enabled", True), (settings, "property_inquiry_require_consent", True), (settings, "ghl_enabled", True),
                                    (settings, "ghl_location_id", "synthetic-location")):
            patcher = patch.object(target, name, return_value=value) if target is lead_delivery else patch.object(target, name, value)
            patcher.start(); self.addCleanup(patcher.stop)
        for target, name in ((leads.ghl, "create_contact"), (leads, "send_lead_notification"), (lead_delivery, "_provider_result")):
            patcher = patch.object(target, name, side_effect=AssertionError("Intake must not call a provider"))
            patcher.start(); self.addCleanup(patcher.stop)

    def payload(self, **changes):
        return {"type": "property_inquiry", "name": " Synthetic visitor ", "phone": "+18095551234",
                "property_id": str(self.listings["active"]), "submission_id": str(uuid4()),
                "tracking": {"form": "property_inquiry", "page_path": f'/listing/{self.listings["active"]}/',
                             "locale": "en", "intent": "general", "contact_consent": True,
                             "measurement_consent": "denied"}, **changes}

    def counts(self):
        with Session(self.engine) as db:
            return db.query(Lead).count(), db.query(LeadDeliveryJob).count()

    def test_phone_email_and_both_keep_property_type_optional_comments_and_one_outbox(self):
        for contact in ({"phone": "+18095551234"}, {"phone": None, "email": "synthetic@example.com"},
                        {"phone": "+18095551234", "email": "synthetic@example.com"}):
            result = self.client.post("/leads", json=self.payload(**contact))
            self.assertEqual(result.status_code, 201, result.text)
            self.assertEqual(result.headers["cache-control"], "no-store")
            self.assertFalse(result.json()["measurement"]["eligible"])
            with Session(self.engine) as db:
                lead = db.get(Lead, UUID(result.json()["id"]))
                self.assertEqual((lead.type, lead.property_id, lead.name), ("property_inquiry", self.listings["active"], "Synthetic visitor"))
                self.assertEqual(lead.email, contact.get("email"))
                self.assertEqual(lead.phone, contact.get("phone"))
                self.assertIsNone(lead.message); self.assertIsNone(lead.from_user_id)
                self.assertEqual(db.query(LeadDeliveryJob).filter_by(lead_id=lead.id).count(), 6)
        self.assertEqual(self.counts(), (3, 18))

    def test_invalid_contacts_or_neither_do_not_store_or_queue(self):
        for change in ({"phone": None}, {"phone": "+1"}, {"phone": "invalid", "email": "valid@example.com"},
                       {"phone": "+18095551234", "email": "invalid"}, {"message": "x"*2001}):
            result = self.client.post("/leads", json=self.payload(**change))
            self.assertEqual(result.status_code, 422)
        self.assertEqual(self.counts(), (0, 0))

    def test_backend_first_email_compatibility_is_explicit_and_final_cutover_is_strict(self):
        legacy = self.payload(phone=None, email="legacy@example.com")
        legacy.pop("tracking")
        with patch.object(settings, "property_inquiry_require_consent", False):
            accepted = self.client.post("/leads", json=legacy)
        self.assertEqual(accepted.status_code, 201, accepted.text)
        self.assertEqual(accepted.json()["measurement"]["reason"], "legacy_submission")
        with Session(self.engine) as db:
            lead = db.get(Lead, UUID(accepted.json()["id"]))
            self.assertIsNone(lead.tracking)
        rejected = self.client.post("/leads", json={**legacy, "submission_id": str(uuid4())})
        self.assertEqual(rejected.status_code, 422)
        # Returning a prior accepted receipt does not create another new inquiry.
        recovered = self.client.post("/leads", json=legacy)
        self.assertEqual(recovered.status_code, 201)
        self.assertEqual(recovered.json()["id"], accepted.json()["id"])
        self.assertEqual(self.counts(), (1, 6))

    def test_capture_does_not_bypass_phone_only_or_tracked_contact_permission(self):
        with patch.object(settings, "property_inquiry_require_consent", False):
            for path in ("/leads", "/leads/capture"):
                phone_only = self.payload(); phone_only.pop("tracking")
                self.assertEqual(self.client.post(path, json=phone_only).status_code, 422)
                denied = self.payload(email="legacy@example.com"); denied["tracking"]["contact_consent"] = False
                self.assertEqual(self.client.post(path, json=denied).status_code, 422)
            legacy_capture = self.payload(phone=None, email="legacy@example.com"); legacy_capture.pop("tracking")
            accepted = self.client.post("/leads/capture", json=legacy_capture)
            self.assertEqual(accepted.status_code, 201, accepted.text)
        with Session(self.engine) as db:
            lead = db.get(Lead, UUID(accepted.json()["id"]))
            self.assertIsNone(lead.tracking); self.assertIsNone(lead.property_id)
        final = self.client.post("/leads/capture", json={**legacy_capture, "submission_id": str(uuid4())})
        self.assertEqual(final.status_code, 422)
        self.assertEqual(self.counts(), (1, 2))

    def test_contact_permission_is_explicit_and_measurement_choice_does_not_block_contact(self):
        missing = self.payload(); missing.pop("tracking")
        self.assertEqual(self.client.post("/leads", json=missing).status_code, 422)
        declined = self.payload(); declined["tracking"]["contact_consent"] = False
        self.assertEqual(self.client.post("/leads", json=declined).status_code, 422)
        self.assertEqual(self.counts(), (0, 0))
        for choice in ("unset", "denied", "granted"):
            body = self.payload(message="Synthetic optional comments")
            body["tracking"].update(measurement_consent=choice, attribution={"gclid": "synthetic-click", "utm_source": "google"})
            result = self.client.post("/leads", json=body)
            self.assertEqual(result.status_code, 201, result.text)
            self.assertFalse(result.json()["measurement"]["eligible"])
            self.assertEqual(result.json()["measurement"]["reason"], "other_form")
            with Session(self.engine) as db:
                lead = db.get(Lead, UUID(result.json()["id"]))
                self.assertEqual(lead.message, "Synthetic optional comments")
                self.assertEqual(bool(lead.tracking.get("attribution")), choice == "granted")
                self.assertNotIn("Synthetic", str(lead.tracking))

    def test_invalid_missing_private_and_unknown_property_references_are_not_accepted(self):
        for value in (None, "not-a-uuid"):
            result = self.client.post("/leads", json=self.payload(property_id=value))
            self.assertEqual(result.status_code, 422)
        bodies = []
        for listing_id in [self.listings[s] for s in ("draft", "archived", "pending_approval", "rejected")] + [uuid4()]:
            result = self.client.post("/leads", json=self.payload(property_id=str(listing_id)))
            self.assertEqual(result.status_code, 404)
            self.assertEqual(result.headers["cache-control"], "no-store")
            self.assertNotIn("Synthetic private title", result.text)
            bodies.append(result.json())
        self.assertTrue(all(value == bodies[0] for value in bodies))
        self.assertEqual(self.counts(), (0, 0))

    def test_unchanged_retry_is_one_event_even_after_listing_is_archived(self):
        payload = self.payload(message="Synthetic comments")
        first = self.client.post("/leads", json=payload)
        self.assertEqual(first.status_code, 201, first.text)
        with Session(self.engine) as db:
            db.get(Listing, self.listings["active"]).status = "archived"; db.commit()
        second = self.client.post("/leads", json=payload)
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(first.json()["id"], second.json()["id"])
        changed = self.client.post("/leads", json={**payload, "message": "Changed comments"})
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(self.counts(), (1, 6))

    def test_account_identity_is_never_inferred_from_contact_email_and_mine_is_private(self):
        anonymous = self.client.post("/leads", json=self.payload(email="property-buyer@example.com"))
        signed_in = self.client.post("/leads", json=self.payload(), headers=self.headers["buyer"])
        self.assertEqual(anonymous.status_code, 201); self.assertEqual(signed_in.status_code, 201)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(Lead, UUID(anonymous.json()["id"])).from_user_id)
            self.assertEqual(db.get(Lead, UUID(signed_in.json()["id"])).from_user_id, self.user_ids["buyer"])
            self.assertEqual(db.query(User).count(), 3)
        mine = self.client.get("/leads/mine", headers=self.headers["buyer"])
        self.assertEqual(mine.status_code, 200, mine.text)
        self.assertEqual(mine.headers["cache-control"], "no-store")
        self.assertEqual([row["id"] for row in mine.json()], [signed_in.json()["id"]])
        denied = self.client.get("/leads/mine")
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.headers["cache-control"], "no-store")

    def test_owner_change_account_requests_keep_nonpublic_property_scope_and_booking_requires_email(self):
        request = {"type": "seller_interest", "name": "Synthetic owner", "email": "property-owner@example.com",
                   "property_id": str(self.listings["draft"]), "message": "Synthetic change request"}
        result = self.client.post("/leads", json=request, headers=self.headers["owner"])
        self.assertEqual(result.status_code, 201, result.text)
        with Session(self.engine) as db:
            lead = db.get(Lead, UUID(result.json()["id"]))
            self.assertEqual((lead.type, lead.property_id, lead.from_user_id), ("seller_interest", self.listings["draft"], self.user_ids["owner"]))
            self.assertIsNone(lead.tracking)
        booking = self.client.post("/leads", json={"type": "booking", "name": "Synthetic guest", "phone": "+18095551234"})
        self.assertEqual(booking.status_code, 422)

    def test_mine_read_failure_is_private_and_not_presented_as_an_empty_feed(self):
        accepted = self.client.post("/leads", json=self.payload(), headers=self.headers["buyer"])
        self.assertEqual(accepted.status_code, 201, accepted.text)
        with patch.object(leads, "_build_response", side_effect=SQLAlchemyError("synthetic-private-contact")):
            failed = self.client.get("/leads/mine", headers=self.headers["buyer"])
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.headers["cache-control"], "no-store")
        self.assertEqual(failed.headers["vary"], "Cookie, Authorization")
        self.assertNotIn("synthetic-private-contact", failed.text)
        self.assertNotEqual(failed.json(), [])
        self.assertEqual(self.counts(), (1, 6))
