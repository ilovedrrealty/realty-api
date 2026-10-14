"""Synthetic API/CRM/email regressions. No external services are contacted.

Run from the repository root: python -m unittest discover -s tests -v
"""

import json
import os
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

# Explicitly isolate configuration before importing any application modules.
os.environ.update(
    DATABASE_URL="sqlite+pysqlite:///:memory:",
    JWT_SECRET="synthetic-contact-tests-only",
    GHL_ENABLED="false",
    GHL_API_KEY="",
    GHL_LOCATION_ID="synthetic-location",
    RESEND_API_KEY="",
    NOTIFY_EMAIL="owner@example.com",
    PAYPAL_CLIENT_ID="",
    PAYPAL_CLIENT_SECRET="",
    SUPABASE_URL="",
    SUPABASE_SERVICE_KEY="",
)

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from config import settings
from database import get_db
from models.lead import Lead
from routes import bookings, leads
from schemas.booking import BookingCreate
from schemas.inquiry import InquiryCreate
from utils import email, ghl
from utils.auth import get_optional_user
from utils.limiter import limiter


INTEREST_TYPES = ("buyer_interest", "seller_interest", "rental_interest")
PHONE = "+18095551234"


class FakeDB:
    def __init__(self):
        self.saved = []
        self.commits = 0

    def add(self, lead):
        self.saved.append(lead)

    def commit(self):
        self.commits += 1

    def refresh(self, lead):
        lead.id = lead.id or uuid4()
        lead.created_at = datetime.now(timezone.utc)

    def query(self, *args):
        query = MagicMock()
        query.filter.return_value.first.return_value = SimpleNamespace(
            data={"notify_email": "owner@example.com"}
        )
        return query


class LeadContactTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.old_limiter_enabled = limiter.enabled
        limiter.enabled = False
        app = FastAPI()
        app.state.limiter = limiter
        app.include_router(leads.router)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_optional_user] = lambda: None
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        limiter.enabled = self.old_limiter_enabled

    def test_each_interest_accepts_phone_email_and_both(self):
        for intent in INTEREST_TYPES:
            for contact in (
                {"phone": "+1 (809) 555-1234"},
                {"email": " visitor@example.com "},
                {"email": "visitor@example.com", "phone": PHONE},
            ):
                with self.subTest(intent=intent, contact=contact), patch.object(
                    ghl, "create_contact"
                ) as crm, patch.object(email, "_send") as send:
                    response = self.client.post("/leads", json={
                        "type": intent, "name": " Visitor ",
                        "message": "Sosua, $1,500/month. [Get Started inquiry]",
                        **contact,
                    })
                    self.assertEqual(response.status_code, 201, response.text)
                    saved = self.db.saved[-1]
                    self.assertEqual(UUID(response.json()["id"]), saved.id)
                    self.assertEqual(response.json()["status"], "new")
                    self.assertEqual(saved.name, "Visitor")
                    self.assertEqual(saved.email, "visitor@example.com" if "email" in contact else None)
                    self.assertEqual(saved.phone, PHONE if "phone" in contact else None)
                    self.assertIn("Sosua", saved.message)
                    self.assertIs(crm.call_args.args[0], saved)
                    self.assertEqual(send.call_args.args[0], "owner@example.com")
                    self.assertIn("Sosua", send.call_args.args[2])

    def test_blank_optional_fields_are_absent(self):
        phone_only = leads.LeadCreate(type="buyer_interest", name="Visitor", email="  ", phone=PHONE)
        email_only = leads.LeadCreate(type="seller_interest", name="Visitor", email="v@example.com", phone="  ")
        self.assertIsNone(phone_only.email)
        self.assertIsNone(email_only.phone)

    def test_each_interest_rejects_neither_or_invalid_supplied_contact(self):
        invalid_cases = (
            {}, {"email": "  ", "phone": "  "},
            {"email": "invalid", "phone": PHONE},
            {"email": "visitor@example.com", "phone": "+12001230101"},
            {"email": "visitor@example.com", "phone": "8095551234"},
            {"email": "visitor@example.com", "phone": "+1"},
            {"email": "visitor@example.com", "phone": "+18095551234 ext 4"},
        )
        for intent in INTEREST_TYPES:
            for contact in invalid_cases:
                with self.subTest(intent=intent, contact=contact), patch.object(ghl, "create_contact") as crm, patch.object(email, "_send") as send:
                    response = self.client.post("/leads", json={"type": intent, "name": "Visitor", **contact})
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(self.db.saved, [])
                    crm.assert_not_called()
                    send.assert_not_called()

    def test_blank_name_rejected_even_with_valid_phone(self):
        response = self.client.post("/leads", json={"type": "rental_interest", "name": "  ", "phone": PHONE})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.db.saved, [])

    def test_international_country_code_preserved(self):
        body = leads.LeadCreate(type="buyer_interest", name="Visitor", phone="+44 20 8366 1177")
        self.assertEqual(body.phone, "+442083661177")

    def test_optional_comments_may_be_omitted(self):
        body = leads.LeadCreate(type="seller_interest", name="Visitor", phone=PHONE)
        self.assertIsNone(body.message)

    def test_property_inquiry_and_booking_still_require_email(self):
        for intent in ("property_inquiry", "booking"):
            with self.subTest(intent=intent):
                with self.assertRaises(ValidationError):
                    leads.LeadCreate(type=intent, name="Visitor", phone=PHONE)
                self.assertEqual(leads.LeadCreate(type=intent, name="Visitor", email="v@example.com").email, "v@example.com")
        with self.assertRaises(ValidationError):
            InquiryCreate(name="Visitor", phone=PHONE, message="Legacy inquiry")

    def test_guest_booking_rejects_missing_email_before_creating_anything(self):
        body = BookingCreate(
            listing_id=uuid4(), name="Visitor", phone=PHONE,
            check_in=date.today() + timedelta(days=2), check_out=date.today() + timedelta(days=4),
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(transaction="rent")
        with patch.object(bookings, "_dates_available", return_value=True), patch.object(bookings.paypal, "authorize_order") as payment:
            with self.assertRaises(HTTPException) as caught:
                bookings.create_booking(body, user=None, db=db)
            self.assertEqual(caught.exception.status_code, 422)
            db.add.assert_not_called()
            payment.assert_not_called()

    def test_capture_endpoint_retains_contact_validation(self):
        with patch.object(email, "_send") as send:
            accepted = self.client.post("/leads/capture", json={"type": "rental_interest", "name": "Visitor", "phone": PHONE})
            self.assertEqual(accepted.status_code, 201)
            self.assertIsNone(self.db.saved[-1].email)
            send.assert_called_once()
        rejected = self.client.post("/leads/capture", json={"type": "rental_interest", "name": "Visitor"})
        self.assertEqual(rejected.status_code, 422)

    def test_response_serializes_absent_email_without_fabrication(self):
        lead = Lead(id=uuid4(), type="buyer_interest", name="Visitor", phone=PHONE, status="new", created_at=datetime.now(timezone.utc))
        response = leads._build_response(lead, self.db)
        self.assertIsNone(response.model_dump()["email"])
        self.assertTrue(Lead.__table__.c.email.nullable)

    def test_owner_and_assigned_notifications_accept_phone_only(self):
        lead = SimpleNamespace(type="rental_interest", name="Visitor", email=None, phone=PHONE, message="<Location> & budget")
        with patch.object(email, "_send") as send:
            email.send_lead_notification(lead, notify_email="owner@example.com")
            email.send_realtor_lead_assigned_email("agent@example.com", "Agent", lead)
            self.assertEqual(send.call_count, 2)
            self.assertEqual([call.args[0] for call in send.call_args_list], ["owner@example.com", "agent@example.com"])
            for call in send.call_args_list:
                html = call.args[2]
                self.assertIn(PHONE, html)
                self.assertIn("Rental Interest", html)
                self.assertNotIn("mailto:None", html)
                self.assertIn("&lt;Location&gt; &amp; budget", html)

    def test_email_only_and_both_notifications_keep_real_email(self):
        for phone in (None, PHONE):
            with self.subTest(phone=phone), patch.object(email, "_send") as send:
                lead = SimpleNamespace(type="buyer_interest", name="Visitor", email="visitor@example.com", phone=phone, message=None)
                email.send_lead_notification(lead, notify_email="owner@example.com")
                email.send_realtor_lead_assigned_email("agent@example.com", "Agent", lead)
                for call in send.call_args_list:
                    self.assertIn("mailto:visitor@example.com", call.args[2])
                    if phone:
                        self.assertIn(PHONE, call.args[2])

    def test_ghl_creates_each_identifier_combination_for_each_interest(self):
        for intent in INTEREST_TYPES:
            for address, phone in ((None, PHONE), ("v@example.com", None), ("v@example.com", PHONE)):
                with self.subTest(intent=intent, email=address, phone=phone):
                    lead, calls = self._run_ghl(intent, address, phone)
                    payload = calls[0][1]
                    self.assertEqual(payload.get("email"), address)
                    self.assertEqual("email" in payload, address is not None)
                    self.assertEqual(payload.get("phone"), phone)
                    self.assertEqual("phone" in payload, phone is not None)
                    self.assertEqual(payload["tags"], [intent.replace("_", "-"), "website-lead"])
                    self.assertEqual(lead.ghl_contact_id, "synthetic-contact")
                    self.assertIsNone(lead.ghl_sync_error)
                    self.assertTrue(any(url.endswith("/tasks") for url, _ in calls))

    def test_ghl_duplicate_phone_contact_keeps_rental_tags_and_notes(self):
        lead, calls = self._run_ghl("rental_interest", None, "+1 (809) 555-1234", duplicate=True)
        self.assertEqual(calls[0][1]["phone"], PHONE)
        self.assertNotIn("email", calls[0][1])
        self.assertEqual(lead.ghl_contact_id, "synthetic-existing-contact")
        self.assertTrue(any(url.endswith("/tags") and body["tags"] == ["rental-interest", "website-lead"] for url, body in calls))
        self.assertTrue(any(url.endswith("/notes") and "&lt;Inquiry&gt;" in body["body"] for url, body in calls))
        self.assertTrue(any(url.endswith("/tasks") and "&lt;Inquiry&gt;" in body["body"] for url, body in calls))

    def _run_ghl(self, intent, address, phone, duplicate=False):
        calls = []
        lead = SimpleNamespace(id=uuid4(), type=intent, name="Synthetic Visitor", email=address, phone=phone, message="<Inquiry>", ghl_contact_id=None, ghl_sync_error=None)
        def respond(request):
            calls.append((str(request.url), json.loads(request.content)))
            if str(request.url).endswith("/contacts/"):
                if duplicate:
                    return httpx.Response(400, json={"meta": {"contactId": "synthetic-existing-contact"}}, request=request)
                return httpx.Response(201, json={"contact": {"id": "synthetic-contact"}}, request=request)
            return httpx.Response(201, json={}, request=request)
        original_client = httpx.Client
        with patch.object(settings, "ghl_enabled", True), patch.object(settings, "ghl_api_key", "synthetic-not-live"), patch.object(
            ghl.httpx, "Client", side_effect=lambda *args, **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs)
        ):
            ghl.create_contact(lead, None, self.db)
        return lead, calls


if __name__ == "__main__":
    unittest.main()
