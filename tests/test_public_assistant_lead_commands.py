"""Owned model-created PostgreSQL definitions; not migration or live delivery proof.

Run in a separate owned test process: the synthetic settings below intentionally
precede application imports. No HTTP route, live provider or assistant model runs.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import hashlib
import json
import os
import threading
import unittest
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-assistant-lead-test-only",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
                  SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from config import settings
from database import Base
import models
import models.trade_contact
from models.lead import Lead
from models.lead_delivery import LeadDeliveryJob
from models.listing import Listing
from models.site_settings import SiteSettings
from models.user import User
from routes import leads
from services import lead_delivery
from services import public_assistant_lead_commands as commands


def draft(**changes):
    return {"intent": "buy", "name": " Synthetic customer ", "email": "visitor@example.com",
            "phone": None, "message": "Please contact me about my search.", "contact_consent": True, **changes}


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires disposable loopback PostgreSQL")
class PublicAssistantLeadCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if (url.get_backend_name() != "postgresql" or url.host != "127.0.0.1" or url.port != 55449
                or url.database != "ildr_platform_test_20261025"):
            raise ValueError("Owned fixed loopback test database required")
        cls.schema = "assistant_lead_confirm_test_" + uuid4().hex
        bootstrap = create_engine(url)
        try:
            with bootstrap.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        finally:
            bootstrap.dispose()
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema}, pool_size=8)
        Base.metadata.create_all(cls.engine)
        cls.factory = sessionmaker(bind=cls.engine, autoflush=False)
        cls.old_flags = settings.lead_delivery_enabled, settings.customer_messages_enabled
        settings.lead_delivery_enabled = settings.customer_messages_enabled = True

    @classmethod
    def tearDownClass(cls):
        settings.lead_delivery_enabled, settings.customer_messages_enabled = cls.old_flags
        try:
            with cls.engine.begin() as connection:
                connection.execute(text("SET search_path TO public"))
                assert cls.schema.startswith("assistant_lead_confirm_test_")
                assert len(cls.schema) == len("assistant_lead_confirm_test_") + 32
                UUID(cls.schema[-32:])
                connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        finally:
            cls.engine.dispose()

    def setUp(self):
        # All model tables are in this unique owned schema; include both new
        # assignment tables so RESTRICT fixture dependencies cannot leak cases.
        names = [table.name for table in Base.metadata.sorted_tables]
        self.assertTrue(all(name.replace("_", "").isalnum() for name in names))
        with self.engine.begin() as connection:
            connection.execute(text("TRUNCATE " + ",".join('"' + name + '"' for name in names)))
        with self.factory() as db:
            user = User(email="synthetic-owner@example.com", display_name="Synthetic owner",
                        role="owner", status="active", email_verified=True)
            db.add(user)
            db.flush()
            listing = Listing(title="Synthetic public home", type="villa", transaction="sale", price=100000,
                              location="Synthetic town", status="active", submitted_by=user.id)
            db.add_all([listing, SiteSettings(id=1, data={"notify_email": "staff@example.com"})])
            db.commit()
            self.listing_id = listing.id
        self.context = commands.ServerLeadContext("synthetic_customer_session_" + "a" * 32,
                                                 b"synthetic-signing-material-only-" + b"k" * 32, True)
        self.backend = self.enterContext(patch.object(commands, "_backend",
            return_value=(leads.LeadCreate, leads._persist_submission, self.factory)))
        self.clock = self.enterContext(patch.object(commands, "_now", return_value=1800000000))
        self.inline_crm = self.enterContext(patch.object(leads.ghl, "create_contact", side_effect=AssertionError("inline_provider_forbidden")))
        self.inline_email = self.enterContext(patch.object(leads, "send_lead_notification", side_effect=AssertionError("inline_provider_forbidden")))
        self.worker = self.enterContext(patch.object(lead_delivery, "worker_loop", new=Mock(side_effect=AssertionError("worker_forbidden"))))
        self.http_sync = self.enterContext(patch.object(httpx.Client, "send", side_effect=AssertionError("real_http_forbidden")))
        self.http_async = self.enterContext(patch.object(httpx.AsyncClient, "send", side_effect=AssertionError("real_http_forbidden")))
        self.addCleanup(self._no_provider_dispatch)

    def _no_provider_dispatch(self):
        self.inline_crm.assert_not_called()
        self.inline_email.assert_not_called()
        self.worker.assert_not_called()
        self.http_sync.assert_not_called()
        self.http_async.assert_not_called()

    def prepare(self, value=None, locale="en"):
        return commands.prepare_contact_review(self.context, json.dumps(value if value is not None else draft()), locale=locale)

    def confirm(self, prepared):
        return commands.confirm_contact_review(self.context, prepared.review_json, prepared.confirmation_token,
                                                customer_confirmed=True)

    def counts(self):
        with self.factory() as db:
            return db.query(Lead).count(), db.query(LeadDeliveryJob).count()

    def queue_identity(self):
        with self.factory() as db:
            return sorted((str(row.id), str(row.lead_id), row.step, row.payload_sha256, row.status)
                          for row in db.query(LeadDeliveryJob).all())

    def test_prepare_uses_current_contact_validation_normalizes_and_never_writes(self):
        prepared = self.prepare(draft(phone="+1 (809) 555-1234"), "es")
        review = json.loads(prepared.review_json)
        self.assertEqual((review["name"], review["phone"], review["locale"]),
                         ("Synthetic customer", "+18095551234", "es"))
        self.assertEqual(review["consent_version"], "contact_request_v1")
        self.assertIn("esta solicitud", review["consent_text"])
        self.assertEqual(self.counts(), (0, 0))
        for value in (draft(email=None, phone=None), draft(phone="8095551234"),
                      draft(contact_consent=False), draft(intent="booking"),
                      draft(intent="property_inquiry"), draft(property_id=str(self.listing_id))):
            with self.assertRaises(commands.LeadCommandError):
                self.prepare(value)
        self.assertEqual(self.counts(), (0, 0))
        self.assertNotIn("visitor@example.com", repr(prepared))
        self.assertNotIn("signing", repr(self.context))

    def test_buy_sell_rent_and_public_property_confirm_real_leads_and_existing_outbox(self):
        for intent, stored in (("buy", "buyer_interest"), ("sell", "seller_interest"),
                               ("rent", "rental_interest"), ("property_inquiry", "property_inquiry")):
            prepared = self.prepare(draft(intent=intent, **({"property_id": str(self.listing_id)} if intent == "property_inquiry" else {})))
            receipt = self.confirm(prepared)
            with self.factory() as db:
                lead = db.get(Lead, UUID(receipt.reference))
                self.assertEqual(lead.type, stored)
                self.assertIsNone(lead.from_user_id)
                self.assertIsNone(lead.assigned_realtor_id)
                self.assertTrue(lead.tracking["contact_consent"])
                self.assertEqual(lead.tracking["measurement_consent"], "denied")
                self.assertEqual(lead.tracking["page_path"], "/other/")
                self.assertNotIn("attribution", lead.tracking)
                self.assertGreater(db.query(LeadDeliveryJob).filter_by(lead_id=lead.id).count(), 0)
            self.assertTrue(receipt.recorded)
            self.assertFalse(receipt.replayed or receipt.delivery_confirmed)
        self.assertEqual(self.counts()[0], 4)

    def test_only_explicit_true_with_trusted_enabled_context_can_enter_persistence(self):
        prepared = self.prepare()
        for value in (False, None, 1, "true", "customer said yes"):
            with self.assertRaisesRegex(commands.LeadCommandError, "^customer_confirmation_required$"):
                commands.confirm_contact_review(self.context, prepared.review_json, prepared.confirmation_token,
                                                customer_confirmed=value)
        for context in (replace(self.context, enabled=False), asdict(self.context)):
            with self.assertRaises(commands.LeadCommandError):
                commands.confirm_contact_review(context, prepared.review_json, prepared.confirmation_token,
                                                customer_confirmed=True)
        self.assertEqual(self.counts(), (0, 0))

    def test_review_contact_consent_locale_and_unknown_authority_tampering_refused(self):
        prepared = self.prepare()
        for key, value in (("name", "Different customer"), ("intent", "sell"), ("contact_consent", False),
                           ("locale", "es"), ("email", "different@example.com"),
                           ("consent_text", "Agree to unrelated marketing"), ("consent_version", "other"),
                           ("actor_id", str(uuid4())), ("assigned_realtor_id", str(uuid4())),
                           ("tracking", {"measurement_consent": "granted"})):
            review = json.loads(prepared.review_json)
            review[key] = value
            with self.assertRaises(commands.LeadCommandError):
                commands.confirm_contact_review(self.context, json.dumps(review), prepared.confirmation_token,
                                                customer_confirmed=True)
        self.assertEqual(self.counts(), (0, 0))

    def test_duplicate_nonfinite_or_control_json_and_model_actor_fields_refused(self):
        raw = json.dumps(draft())
        for raw in (raw.replace('"name": " Synthetic customer "', '"name":"first","name":"second"'),
                    raw.replace('"name": " Synthetic customer "', '"name":NaN'),
                    raw.replace('"name": " Synthetic customer "', '"name":1e999'),
                    json.dumps(draft(name="bad\0name")), json.dumps(draft(actor_id=str(uuid4()))),
                    json.dumps(draft(submission_id=str(uuid4()))), json.dumps(draft(locale="es"))):
            with self.assertRaises(commands.LeadCommandError):
                commands.prepare_contact_review(self.context, raw, locale="en")
        self.assertEqual(self.counts(), (0, 0))

    def test_signature_session_and_expiry_refuse_before_database_use(self):
        prepared = self.prepare()
        self.backend.reset_mock()
        for context in (replace(self.context, session_binding="different_customer_session_" + "b" * 32),
                        replace(self.context, signing_key=b"different-synthetic-key-" + b"z" * 32)):
            with self.assertRaisesRegex(commands.LeadCommandError, "^invalid_review$"):
                commands.confirm_contact_review(context, prepared.review_json, prepared.confirmation_token,
                                                customer_confirmed=True)
        for now in (1800000000 - 1, prepared.expires_at + 1):
            self.clock.return_value = now
            with self.assertRaisesRegex(commands.LeadCommandError, "^review_expired$"):
                self.confirm(prepared)
        self.backend.assert_not_called()
        self.assertEqual(self.counts(), (0, 0))

    def test_persistent_replay_across_fresh_contexts_and_sessions_never_duplicates_jobs(self):
        prepared = self.prepare()
        first = self.confirm(prepared)
        original_queue = self.queue_identity()
        self.assertGreater(len(original_queue), 0)
        other_context = replace(self.context)
        replay = commands.confirm_contact_review(other_context, prepared.review_json, prepared.confirmation_token,
                                                customer_confirmed=True)
        self.assertEqual(first.reference, replay.reference)
        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(self.counts(), (1, len(original_queue)))
        self.assertEqual(self.queue_identity(), original_queue)

    def test_concurrent_same_approved_command_has_one_atomic_winner_and_one_replay(self):
        prepared = self.prepare()
        barrier = threading.Barrier(2)
        factory = self.factory
        def synchronized_factory():
            barrier.wait(timeout=5)
            return factory()
        self.backend.return_value = (leads.LeadCreate, leads._persist_submission, synchronized_factory)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = [future.result(timeout=15) for future in
                             [pool.submit(self.confirm, prepared), pool.submit(self.confirm, prepared)]]
        self.assertEqual(first.reference, second.reference)
        self.assertEqual(sorted([first.replayed, second.replayed]), [False, True])
        queue = self.queue_identity()
        self.assertEqual(self.counts(), (1, len(queue)))
        self.assertEqual(len(queue), len({(row[1], row[2]) for row in queue}))

    def test_existing_unique_submission_rejects_even_a_resigned_changed_body(self):
        prepared = self.prepare()
        first = self.confirm(prepared)
        queue = self.queue_identity()
        review = json.loads(prepared.review_json)
        review["message"] = "Different approved request under the same UUID"
        raw = commands._json(review)
        parts = prepared.confirmation_token.split(".")
        unsigned = ".".join(parts[:3]) + "." + hashlib.sha256(raw.encode()).hexdigest()
        resigned = unsigned + "." + commands._signature(self.context, unsigned)
        with self.assertRaisesRegex(commands.LeadCommandError, "^submission_conflict$"):
            commands.confirm_contact_review(self.context, raw, resigned, customer_confirmed=True)
        self.assertEqual(self.counts(), (1, len(queue)))
        self.assertEqual(self.queue_identity(), queue)
        self.assertTrue(self.confirm(prepared).replayed)
        self.assertEqual(self.confirm(prepared).reference, first.reference)

    def test_withdrawn_property_refuses_new_confirmation_without_lead_or_jobs(self):
        prepared = self.prepare(draft(intent="property_inquiry", property_id=str(self.listing_id)))
        with self.factory() as db:
            db.get(Listing, self.listing_id).status = "archived"
            db.commit()
        with self.assertRaisesRegex(commands.LeadCommandError, "^property_unavailable$"):
            self.confirm(prepared)
        self.assertEqual(self.counts(), (0, 0))

    def test_queue_failure_rolls_back_lead_and_jobs_without_automatic_retry(self):
        prepared = self.prepare()
        enqueue = lead_delivery.enqueue_lead_delivery
        def fail_after_real_enqueue(*args, **kwargs):
            enqueue(*args, **kwargs)
            raise SQLAlchemyError("synthetic queue transaction failure")
        with patch.object(lead_delivery, "enqueue_lead_delivery", side_effect=fail_after_real_enqueue) as failed:
            with self.assertRaisesRegex(commands.LeadCommandError, "^submission_unavailable$"):
                self.confirm(prepared)
            self.assertEqual(failed.call_count, 1)
        self.assertEqual(self.counts(), (0, 0))
        self.assertTrue(self.confirm(prepared).recorded)
        self.assertEqual(self.counts()[0], 1)

    def test_lost_postcommit_receipt_is_uncertain_then_explicit_replay_keeps_one_lead(self):
        prepared = self.prepare()
        def committed_but_unreported(*args, **kwargs):
            leads._persist_submission(*args, **kwargs)
            raise RuntimeError("synthetic lost postcommit receipt")
        persisted = Mock(side_effect=committed_but_unreported)
        self.backend.return_value = (leads.LeadCreate, persisted, self.factory)
        with self.assertRaisesRegex(commands.LeadCommandError, "^submission_unavailable$"):
            self.confirm(prepared)
        self.assertEqual(persisted.call_count, 1)
        queue = self.queue_identity()
        self.assertGreater(len(queue), 0)
        self.assertEqual(self.counts(), (1, len(queue)))
        self.backend.return_value = (leads.LeadCreate, leads._persist_submission, self.factory)
        self.assertTrue(self.confirm(prepared).replayed)
        self.assertEqual(self.queue_identity(), queue)
        self.assertEqual(self.counts(), (1, len(queue)))

    def test_disabled_existing_outbox_still_records_without_inline_provider_fallback(self):
        prepared = self.prepare()
        with patch.object(settings, "lead_delivery_enabled", False):
            receipt = self.confirm(prepared)
        self.assertEqual(self.counts(), (1, 0))
        self.assertEqual(set(asdict(receipt)), {"reference", "recorded", "replayed", "delivery_confirmed"})
        self.assertFalse(receipt.delivery_confirmed)
        self.assertNotIn("visitor@example.com", repr(receipt))
        with self.factory() as db:
            lead = db.get(Lead, UUID(receipt.reference))
            lead.status = "contacted"
            db.commit()
        replay = self.confirm(prepared)
        self.assertTrue(replay.replayed)
        self.assertNotIn("contacted", repr(replay))
        self.assertEqual(self.counts(), (1, 0))
