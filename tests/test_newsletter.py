"""Isolated native opt-in and unsubscribe checks with a fake mail provider."""

from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, select, text
from sqlalchemy.schema import CreateSchema

from config import settings
from database import SessionLocal, engine
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
        self.send_confirmation_implementation = newsletter.send_confirmation
        self.mail = patch.object(newsletter, "send_confirmation").start()

    def call(self, action, data):
        response = self.client.post(f"/newsletter/{action}", json=data)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        return response

    def read(self):
        with SessionLocal() as db:
            return db.scalar(select(NewsletterSubscription).where(NewsletterSubscription.email == self.email))

    def test_model_created_table_matches_migrated_columns_and_constraints(self):
        # Frozen bootstrap currently leaves this table to Alembic. Exercise
        # model-first creation independently so future bootstrap registration
        # cannot silently lose the migration's defaults or consent checks.
        def catalog(connection, schema):
            columns = connection.execute(text("""SELECT column_name,ordinal_position,
                data_type,udt_name,is_nullable,column_default
                FROM information_schema.columns
                WHERE table_schema=:schema AND table_name='newsletter_subscriptions'
                ORDER BY ordinal_position"""), {"schema": schema}).all()
            constraints = connection.execute(text("""SELECT c.conname,c.contype,
                pg_get_constraintdef(c.oid) FROM pg_constraint c
                JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
                WHERE n.nspname=:schema AND t.relname='newsletter_subscriptions'
                ORDER BY c.conname"""), {"schema": schema}).all()
            return columns, constraints

        schema = f"newsletter_model_{uuid4().hex}"
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(CreateSchema(schema))
                fresh = NewsletterSubscription.__table__.to_metadata(MetaData(), schema=schema)
                fresh.create(connection)
                self.assertEqual(catalog(connection, schema), catalog(connection, "public"))
            finally:
                # PostgreSQL DDL is transactional; this removes only the
                # synthetic schema without touching migrated application data.
                transaction.rollback()

    def test_consent_requires_specific_explicit_confirmation_and_unsubscribe(self):
        fields = {"email": self.email, "language": "es", "consent": True}
        self.assertEqual(self.call("subscribe", dict(fields, consent=1)).status_code, 400)
        self.assertIsNone(self.read())
        self.assertEqual(self.call("subscribe", fields).json(), {"status": "accepted"})
        self.assertEqual(self.read().status, "pending")
        self.assertEqual(self.call("subscribe", fields).status_code, 202)
        self.assertEqual(self.mail.call_count, 1)
        payload, nonce = self.mail.call_args.args
        row_id = self.read().id
        self.assertEqual(payload["subject"], newsletter.COPY["es"][0])
        self.assertIn("http://127.0.0.1:8917/newsletter/#confirm=", payload["html"])
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
        self.assertEqual(self.call("subscribe", values).status_code, 503)
        self.assertEqual(self.mail.call_count, 1)
        self.now += timedelta(minutes=6)
        self.mail.side_effect = None
        settings.landing_url = "http://localhost:7777"
        sender = patch.object(settings, "email_from", "changed@example.com")
        sender.start()
        self.addCleanup(sender.stop)
        self.assertEqual(self.call("subscribe", dict(values, language="es")).status_code, 202)
        self.assertEqual(self.mail.call_args.args, first)
        _, nonce = first
        row_id = self.read().id
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

    def test_email_contains_exact_localized_actions_and_stable_provider_key(self):
        implementation = self.send_confirmation_implementation
        identifier, nonce = uuid4(), uuid4()
        response = SimpleNamespace(is_success=True, json=lambda: {"id": "synthetic-accepted"})
        with patch.object(newsletter.httpx, "post", return_value=response) as provider:
            for locale in ("en", "es"):
                nonce = uuid4()
                message = newsletter.confirmation_message(
                    self.email, locale, identifier, nonce, "https://example.com/newsletter/",
                )
                implementation(message, nonce)
                request = provider.call_args
                self.assertEqual(request.args, ("https://api.resend.com/emails",))
                self.assertEqual(request.kwargs["headers"]["Idempotency-Key"], f"ildr-newsletter-confirm-{nonce}")
                payload = request.kwargs["json"]
                self.assertEqual(payload["to"], [self.email])
                self.assertEqual(payload["subject"], newsletter.COPY[locale][0])
                self.assertIn("#confirm=" + newsletter.action_token(identifier, "confirm", nonce), payload["html"])
                self.assertIn("#unsubscribe=" + newsletter.action_token(identifier, "unsubscribe"), payload["html"])
            provider.return_value = SimpleNamespace(is_success=True, json=lambda: {})
            with self.assertRaises(newsletter.ConfirmationUnavailable):
                implementation(message, nonce)

    def test_uncertain_http_outcome_replays_same_key_and_stored_payload(self):
        self.mail.side_effect = self.send_confirmation_implementation
        accepted = SimpleNamespace(is_success=True, json=lambda: {"id": "synthetic-accepted"})
        values = {"email": self.email, "language": "en", "consent": True}
        with patch.object(newsletter.httpx, "post", side_effect=httpx.ReadTimeout("synthetic timeout")) as provider:
            self.assertEqual(self.call("subscribe", values).status_code, 503)
            first = provider.call_args.kwargs
            self.assertIsNone(self.read().provider_accepted_at)
            self.now += timedelta(minutes=6)
            settings.landing_url = "http://localhost:7777"
            sender = patch.object(settings, "email_from", "changed@example.com")
            sender.start()
            self.addCleanup(sender.stop)
            provider.side_effect = None
            provider.return_value = accepted
            self.assertEqual(self.call("subscribe", dict(values, language="es")).status_code, 202)
            retried = provider.call_args.kwargs
            self.assertEqual(retried["headers"]["Idempotency-Key"], first["headers"]["Idempotency-Key"])
            self.assertEqual(retried["json"], first["json"])
            self.assertEqual(self.read().status, "pending")
            self.assertIsNotNone(self.read().provider_accepted_at)
