"""Isolated native opt-in and unsubscribe checks with a fake mail provider."""

from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from config import settings
from database import SessionLocal
from main import app
from models.newsletter import NewsletterSubscription
from services import newsletter


@unittest.skipUnless(os.environ.get("NEWSLETTER_FIXTURE") == "yes", "requires isolated PostgreSQL")
class NewsletterTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.email = f"newsletter-{uuid4()}@example.com"
        self.now = datetime.now(timezone.utc)
        self.addCleanup(self.client.close)
        for target, attribute, value in (
            (settings, "resend_api_key", "test-provider-only"),
            (settings, "landing_url", "http://127.0.0.1:8917"),
            (settings, "environment", "development"),
        ):
            setup = patch.object(target, attribute, value)
            setup.start()
            self.addCleanup(setup.stop)
        self.addCleanup(patch.stopall)
        self.clock = patch.object(newsletter, "now_utc", side_effect=lambda: self.now).start()
        self.mail = patch.object(newsletter, "send_confirmation").start()

    def call(self, action, data):
        response = self.client.post(f"/newsletter/{action}", json=data)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response

    def read(self):
        with SessionLocal() as db:
            return db.scalar(select(NewsletterSubscription).where(NewsletterSubscription.email == self.email))

    def test_consent_requires_specific_explicit_confirmation_and_unsubscribe(self):
        fields = {"email": self.email, "language": "es", "consent": True}
        self.assertEqual(self.call("subscribe", dict(fields, consent=1)).status_code, 400)
        self.assertIsNone(self.read())
        self.assertEqual(self.call("subscribe", fields).json(), {"status": "accepted"})
        self.assertEqual(self.read().status, "pending")
        self.assertEqual(self.call("subscribe", fields).status_code, 202)
        self.assertEqual(self.mail.call_count, 1)
        _, lang, row_id, nonce, page = self.mail.call_args.args
        self.assertEqual((lang, page), ("es", "http://127.0.0.1:8917/newsletter/"))
        confirm = newsletter.action_token(row_id, "confirm", nonce)
        leave = newsletter.action_token(row_id, "unsubscribe")
        self.assertEqual(len(confirm), 101)
        self.assertEqual(self.client.get("/newsletter/confirm").status_code, 405)
        self.assertEqual(self.call("confirm", {"token": leave}).status_code, 400)
        self.assertEqual(self.call("confirm", {"token": confirm}).json(), {"status": "subscribed"})
        self.assertEqual(self.read().status, "subscribed")
        self.assertEqual(self.call("unsubscribe", {"token": leave}).json(), {"status": "unsubscribed"})
        self.assertEqual(self.call("unsubscribe", {"token": leave}).json(), {"status": "unsubscribed"})
        self.assertEqual(self.call("confirm", {"token": confirm}).status_code, 400)
        self.assertEqual(self.read().status, "unsubscribed")

    def test_provider_failure_leaves_consent_pending_and_retry_reuses_exact_message(self):
        values = {"email": self.email, "language": "en", "consent": True}
        self.mail.side_effect = newsletter.ConfirmationUnavailable()
        self.assertEqual(self.call("subscribe", values).status_code, 503)
        first = self.mail.call_args.args
        self.assertEqual(self.read().status, "pending")
        self.now += timedelta(minutes=6)
        self.mail.side_effect = None
        self.assertEqual(self.call("subscribe", dict(values, language="es")).status_code, 202)
        self.assertEqual(self.mail.call_args.args, first)
        _, _, row_id, nonce, _ = first
        self.now += timedelta(hours=21)
        self.assertEqual(self.call("confirm", {"token": newsletter.action_token(row_id, "confirm", nonce)}).status_code, 400)

    def test_direct_data_api_roles_cannot_read_or_write(self):
        with SessionLocal() as db:
            protection = db.execute(text("""SELECT c.relrowsecurity, count(r.oid),
                bool_or(has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE'))
                FROM pg_class c CROSS JOIN pg_roles r
                WHERE c.oid='public.newsletter_subscriptions'::regclass
                AND r.rolname IN ('anon','authenticated','service_role')
                GROUP BY c.relrowsecurity""")).one()
        self.assertEqual(tuple(protection), (True, 3, False))
