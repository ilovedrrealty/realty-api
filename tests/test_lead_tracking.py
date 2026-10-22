"""Actual disposable Postgres/JWT tests. No production or external services."""
import concurrent.futures
import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-tracking-tests-only",
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
import models
from models.lead import Lead
from models.user import User
from models.site_settings import SiteSettings
from routes import leads
from utils.limiter import limiter


def payload(**changes):
    return {"type": "buyer_interest", "name": "Synthetic inquiry", "phone": "+18095551234",
            "message": "Synthetic property request", "submission_id": str(uuid4()),
            "tracking": {"form": "get_started", "page_path": "/get-started/", "locale": "en", "intent": "buy",
                         "contact_consent": True, "measurement_consent": "granted",
                         "attribution": {"gclid": "synthetic-unit-test-only", "utm_source": "google", "utm_campaign": "synthetic-buyers"}},
            **changes}


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires disposable loopback Postgres")
class LeadTrackingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host not in ("127.0.0.1", "localhost", "::1") or url.get_backend_name() != "postgresql":
            raise ValueError("Tests require a separate loopback-only Postgres")
        cls.schema = "lead_tracking_test_" + uuid4().hex
        bootstrap = create_engine(url)
        with bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        bootstrap.dispose()
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema}, pool_size=8)
        Base.metadata.create_all(cls.engine)
        app = FastAPI()
        app.include_router(leads.router)
        def local_db():
            with Session(cls.engine) as db:
                yield db
        app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(app)
        cls.old_limiter = limiter.enabled
        limiter.enabled = False
        cls.ids, cls.headers = {}, {}
        with Session(cls.engine) as db:
            db.add(SiteSettings(id=1, data={"notify_email": "owner@example.com,other@example.com"}))
            for role in ("admin", "buyer", "owner", "realtor"):
                user = User(email=f"synthetic-{role}@example.com", display_name=f"Synthetic {role}", role=role, email_verified=True, status="active")
                db.add(user)
                db.flush()
                cls.ids[role] = user.id
                cls.headers[role] = {"Authorization": "Bearer " + jwt.encode({"sub": str(user.id), "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, settings.jwt_secret, algorithm="HS256")}
            db.commit()

    @classmethod
    def tearDownClass(cls):
        limiter.enabled = cls.old_limiter
        cls.client.close()
        with cls.engine.begin() as connection:
            connection.execute(text('SET search_path TO public'))
            assert cls.schema.startswith("lead_tracking_test_") and len(cls.schema) == 51
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.engine.dispose()

    def setUp(self):
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM leads"))
        self.crm_patch = patch.object(leads.ghl, "create_contact")
        self.email_patch = patch.object(leads, "send_lead_notification")
        self.crm = self.crm_patch.start()
        self.send = self.email_patch.start()
        self.addCleanup(self.crm_patch.stop)
        self.addCleanup(self.email_patch.stop)

    def submit(self, body, **kwargs):
        response = self.client.post("/leads", json=body, **kwargs)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()

    def test_phone_only_email_only_and_both_keep_structured_attribution_out_of_message(self):
        for contact in ({"phone": "+18095551234"}, {"email": "visitor@example.com", "phone": None}, {"email": "visitor@example.com", "phone": "+18095551234"}):
            body = payload(**contact)
            receipt = self.submit(body)
            self.assertEqual(receipt["measurement"], {"eligible": True, "reason": "eligible_inquiry", "lead_type": "buyer_interest", "intake_source": "get_started"})
            self.assertEqual(set(receipt), {"id", "status", "measurement"})
            with Session(self.engine) as db:
                lead = db.get(Lead, UUID(receipt["id"]))
                self.assertEqual(lead.tracking["attribution"]["utm_source"], "google")
                self.assertIn("received_at", lead.tracking)
                self.assertEqual(lead.message, "Synthetic property request")
                self.assertEqual(lead.email, contact.get("email"))
            self.assertNotIn("synthetic-unit-test-only", str(receipt))

    def test_identical_retry_persists_one_lead_and_notifies_once(self):
        body = payload()
        first = self.submit(body)
        self.assertEqual(self.submit(body), first)
        with Session(self.engine) as db:
            self.assertEqual(db.query(Lead).count(), 1)
        self.crm.assert_called_once()
        self.send.assert_called_once()
        self.assertEqual(self.send.call_args.kwargs["notify_email"], "owner@example.com,other@example.com")

    def test_concurrent_retry_uses_database_unique_key_and_side_effects_once(self):
        body = payload()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            responses = list(executor.map(lambda _: self.client.post("/leads", json=body), range(4)))
        self.assertTrue(all(response.status_code == 201 for response in responses), [r.text for r in responses])
        self.assertEqual(len({r.json()["id"] for r in responses}), 1)
        with Session(self.engine) as db:
            self.assertEqual(db.query(Lead).count(), 1)
        self.crm.assert_called_once()
        self.send.assert_called_once()

    def test_reused_identifier_with_changed_payload_or_actor_returns_no_receipt(self):
        body = payload()
        self.submit(body)
        for changed in ({**body, "name": "Different"}, {**body, "message": "Different"}):
            response = self.client.post("/leads", json=changed)
            self.assertEqual(response.status_code, 409)
            self.assertNotIn("id", response.json())
        response = self.client.post("/leads", json=body, headers=self.headers["buyer"])
        self.assertEqual(response.status_code, 409)

    def test_all_contact_intents_preserve_generic_action_and_explicit_internal_tests_are_excluded(self):
        for kind in ("seller_interest", "rental_interest"):
            receipt = self.submit(payload(type=kind))
            self.assertTrue(receipt["measurement"]["eligible"])
            self.assertEqual(receipt["measurement"]["lead_type"], kind)
        body = payload()
        body["tracking"]["internal_test"] = True
        receipt = self.submit(body)
        self.assertFalse(receipt["measurement"]["eligible"])
        self.assertEqual(receipt["measurement"]["reason"], "internal_test")
        body = payload()
        body["tracking"]["intent"] = "general"
        self.assertTrue(self.submit(body)["measurement"]["eligible"])

    def test_measurement_decline_never_blocks_contact_and_discards_click_identifiers(self):
        for choice in ("denied", "unset"):
            body = payload()
            body["tracking"]["measurement_consent"] = choice
            receipt = self.submit(body)
            self.assertFalse(receipt["measurement"]["eligible"])
            with Session(self.engine) as db:
                self.assertNotIn("attribution", db.get(Lead, UUID(receipt["id"])).tracking)

    def test_contact_consent_and_contact_validation_precede_database_and_notifications(self):
        bad = payload()
        bad["tracking"]["contact_consent"] = False
        for body in (bad, payload(phone=None), payload(email="broken", phone="+18095551234")):
            response = self.client.post("/leads", json=body)
            self.assertEqual(response.status_code, 422, response.text)
        with Session(self.engine) as db:
            self.assertEqual(db.query(Lead).count(), 0)
        self.crm.assert_not_called()
        self.send.assert_not_called()

    def test_whitelist_ignores_unknown_fields_sanitizes_paths_and_drops_obvious_pii(self):
        body = payload()
        body["tracking"].update(page_path="/reset-password/?token=private", name="Should not store")
        body["tracking"]["attribution"].update(utm_source="visitor%40example.com", utm_content="+1 (809) 555-1234", gclid="bad!token", email="private@example.com")
        receipt = self.submit(body)
        with Session(self.engine) as db:
            saved = db.get(Lead, UUID(receipt["id"])).tracking
            self.assertEqual(saved["page_path"], "/other/")
            self.assertNotIn("name", saved)
            self.assertNotIn("email", saved["attribution"])
            for key in ("utm_source", "utm_content", "gclid"):
                self.assertNotIn(key, saved["attribution"])

    def test_legacy_callers_remain_compatible_and_do_not_guess_measurement_consent(self):
        receipt = self.submit({"type": "buyer_interest", "name": "Legacy", "email": "legacy@example.com"})
        self.assertEqual(receipt["measurement"]["reason"], "legacy_submission")
        with Session(self.engine) as db:
            self.assertIsNone(db.get(Lead, UUID(receipt["id"])).tracking)

    def test_admin_only_detail_and_summary_never_return_raw_click_ids(self):
        receipt = self.submit(payload(), headers=self.headers["buyer"])
        with Session(self.engine) as db:
            db.get(Lead, UUID(receipt["id"])).assigned_realtor_id = self.ids["realtor"]
            db.commit()
        admin_rows = self.client.get("/admin/leads", headers=self.headers["admin"])
        self.assertEqual(admin_rows.status_code, 200)
        self.assertEqual(admin_rows.headers["cache-control"], "no-store")
        self.assertTrue(admin_rows.json()[0]["tracking_summary"]["has_ad_click_id"])
        self.assertEqual(admin_rows.json()[0]["tracking_summary"]["intent"], "buy")
        self.assertNotIn("synthetic-unit-test-only", admin_rows.text)
        for path, role in (("/leads/mine", "buyer"), ("/realtor/leads", "realtor")):
            response = self.client.get(path, headers=self.headers[role])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNone(response.json()[0]["tracking_summary"])
        self.assertEqual(self.client.get("/admin/leads/tracking-summary").status_code, 401)
        for role in ("buyer", "owner", "realtor"):
            self.assertEqual(self.client.get("/admin/leads/tracking-summary", headers=self.headers[role]).status_code, 403)

    def test_aggregate_excludes_explicit_tests_from_intent_and_ad_reference_counts(self):
        self.submit(payload())
        body = payload()
        body["tracking"]["internal_test"] = True
        self.submit(body)
        general = payload()
        general["tracking"]["intent"] = "general"
        self.submit(general)
        result = self.client.get("/admin/leads/tracking-summary", headers=self.headers["admin"])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.headers["cache-control"], "no-store")
        data = result.json()
        self.assertEqual((data["total"], data["internal_tests"], data["with_ad_click_id"]), (3, 1, 2))
        self.assertEqual(data["by_intent"], {"buy": 1, "general": 1})
        self.assertNotIn("Synthetic", result.text)

    def test_additive_migration_preserves_existing_rows_rls_and_nullable_columns(self):
        receipt = self.submit({"type": "buyer_interest", "name": "Legacy", "email": "legacy@example.com"})
        path = Path(__file__).resolve().parents[1] / "alembic/versions/49d50da0e05b_add_private_lead_tracking_and_.py"
        spec = importlib.util.spec_from_file_location("tracking_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with self.engine.begin() as connection:
            connection.execute(text("ALTER TABLE leads ENABLE ROW LEVEL SECURITY"))
            context = MigrationContext.configure(connection)
            with patch.object(migration, "op", Operations(context)):
                migration.upgrade()
                migration.upgrade()
                migration.downgrade()
            preserved = connection.execute(text("SELECT name, email, submission_id, tracking FROM leads WHERE id=:id"), {"id": receipt["id"]}).one()
            self.assertEqual(tuple(preserved), ("Legacy", "legacy@example.com", None, None))
            self.assertTrue(connection.execute(text("SELECT relrowsecurity FROM pg_class WHERE oid='leads'::regclass")).scalar_one())
            connection.execute(text("ALTER TABLE leads DISABLE ROW LEVEL SECURITY"))


if __name__ == "__main__":
    unittest.main()
