"""Durable request-related email contracts; fixed disposable PG + MockTransport.

No main/lifespan import, real provider request, historical replay or SMS channel.
"""
import importlib.util
import json
import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-customer-email-tests-only",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
                  SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")

import httpx
import jwt
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from config import settings
from database import Base, get_db
import models
from models.user import User
from models.pending_user import PendingUser
from models.lead import Lead
from models.listing import Listing
from models.site_settings import SiteSettings
from models.lead_delivery import LeadDeliveryJob as Job
from routes import auth, leads, lead_delivery as delivery_routes
from services import lead_delivery as service
from utils.limiter import limiter


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class CustomerDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host != "127.0.0.1" or url.port != 55449 or url.database != "ildr_platform_test_20261025" or url.get_backend_name() != "postgresql":
            raise ValueError("Use only the fixed disposable loopback Postgres")
        cls.schema = "customer_delivery_test_" + uuid4().hex
        cls.bootstrap = create_engine(url)
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.addClassCleanup(cls.cleanup_owned_schema)
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema})
        Base.metadata.create_all(cls.engine)
        app = FastAPI()
        app.state.limiter = limiter
        for router in (auth.router, leads.router, delivery_routes.router):
            app.include_router(router)

        def local_db():
            with Session(cls.engine) as db:
                yield db

        app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.old_limiter = limiter.enabled
        limiter.enabled = False

    @classmethod
    def cleanup_owned_schema(cls):
        if hasattr(cls, "client"):
            cls.client.close()
        if hasattr(cls, "old_limiter"):
            limiter.enabled = cls.old_limiter
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        assert re.fullmatch(r"customer_delivery_test_[0-9a-f]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        finally:
            cls.bootstrap.dispose()

    def setUp(self):
        with self.engine.begin() as connection:
            names = ",".join(f'"{self.schema}"."{row.name}"' for row in Base.metadata.sorted_tables)
            connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        self.patches = []
        self.addCleanup(self.stop_patches)
        for name, value in (("lead_delivery_enabled", True), ("customer_messages_enabled", True),
                            ("ghl_enabled", False), ("resend_api_key", "synthetic-not-a-key"),
                            ("email_from", "no-reply@example.com"), ("notify_email", "fallback@example.com")):
            self.patched(settings, name, value)
        self.direct = self.patched(auth, "_notify_verified_signup")
        self.patched(auth, "send_verification_email")
        self.patched(httpx.HTTPTransport, "handle_request", side_effect=AssertionError("Real provider transport is forbidden"))
        self.admin_id = uuid4()
        self.owners = [f"owner{number}@example.com" for number in range(1, 5)]
        with Session(self.engine) as db:
            db.add(User(id=self.admin_id, user_code=9001, email="synthetic-admin@example.com", display_name="Synthetic Admin",
                        role="admin", email_verified=True, status="active"))
            db.add(SiteSettings(id=1, data={"notify_email": ", ".join(self.owners)}))
            db.flush()
            self.public_id, self.private_id = uuid4(), uuid4()
            for identity, status in ((self.public_id, "active"), (self.private_id, "draft")):
                db.add(Listing(id=identity, title="Synthetic private details", type="villa", transaction="sale",
                               price=1000, location="Synthetic location", images=[], features=[], status=status,
                               submitted_by=self.admin_id))
            db.commit()
        self.admin_headers = {"Authorization": "Bearer " + jwt.encode(
            {"sub": str(self.admin_id), "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, settings.jwt_secret, algorithm="HS256")}

    def patched(self, target, name, *args, **kwargs):
        patcher = patch.object(target, name, *args, **kwargs)
        self.patches.append(patcher)
        return patcher.start()

    def stop_patches(self):
        for patcher in reversed(self.patches):
            patcher.stop()

    def verify(self, address="new@example.com", locale="en"):
        with Session(self.engine) as db:
            db.add(PendingUser(email=address, display_name="Synthetic New Buyer", password_hash="synthetic-only-hash",
                verification_code="123456", verification_attempts=0,
                verification_code_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24)))
            db.commit()
        return self.client.post("/auth/verify", json={"email": address, "code": "123456", "locale": locale})

    def google(self, address="google@example.com", **changes):
        payload = {"sub": "synthetic-google", "email": address, "name": "Synthetic Google Buyer", "email_verified": True, **changes}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        with patch.object(auth.urllib.request, "urlopen", return_value=response):
            return self.client.post("/auth/google", json={"access_token": "synthetic-only-token", "locale": "es"})

    def user_and_jobs(self, address="new@example.com"):
        with Session(self.engine) as db:
            user = db.query(User).filter(User.email == address).one()
            return user.id, {row.step: row for row in db.query(Job).filter(Job.user_id == user.id)}

    def lead(self, **changes):
        payload = {"type": "buyer_interest", "name": "Synthetic Buyer", "email": "buyer@example.com", "submission_id": str(uuid4()),
            "tracking": {"form": "get_started", "locale": "es", "intent": "buy", "contact_consent": True, "internal_test": False}, **changes}
        with patch.object(leads, "send_lead_notification") as direct, patch.object(leads.ghl, "create_contact") as crm:
            response = self.client.post("/leads", json=payload)
            self.assertEqual(response.status_code, 201, response.text)
            direct.assert_not_called(); crm.assert_not_called()
        return UUID(response.json()["id"]), payload

    def lead_jobs(self, identity):
        with Session(self.engine) as db:
            return {row.step: row for row in db.query(Job).filter(Job.lead_id == identity)}

    def isolate_job(self, identity):
        with Session(self.engine) as db:
            db.query(Job).filter(Job.id != identity).update({Job.status: "accepted"}, synchronize_session=False)
            db.commit()

    def run_job(self, identity, status=200):
        self.isolate_job(identity)
        self.calls = []
        def provider(request):
            self.assertEqual(str(request.url), "https://api.resend.com/emails")
            self.calls.append(request)
            return httpx.Response(status, json={"id": "synthetic-accepted", "private": "synthetic-provider-detail"})
        with httpx.Client(transport=httpx.MockTransport(provider)) as client, Session(self.engine) as db:
            claim = service.claim_next_job(db)
            self.assertIsNotNone(claim)
            self.assertEqual(claim.job_id, identity)
            service.process_claimed_job(db, claim, client=client)
        with Session(self.engine) as db:
            return db.get(Job, identity)

    def test_verified_signup_owner_and_customer_jobs_commit_atomically_without_direct_send(self):
        result = self.verify(locale="es")
        self.assertEqual(result.status_code, 200)
        self.assertIn("ildr_token", result.cookies)
        identity, jobs = self.user_and_jobs()
        self.assertEqual(set(jobs), {"email_1", "email_2", "email_3", "email_4", "customer_email"})
        self.assertTrue(all(row.user_id == identity and row.lead_id is None and row.attempt_count == 0 for row in jobs.values()))
        self.assertIn("bienvenida", jobs["customer_email"].payload["body"]["subject"])
        self.assertEqual(jobs["customer_email"].payload["body"]["to"], ["new@example.com"])
        self.assertEqual({jobs[f"email_{n}"].payload["body"]["to"][0] for n in range(1, 5)}, set(self.owners))
        self.direct.assert_not_called()
        repeat = self.client.post("/auth/verify", json={"email": "new@example.com", "code": "123456"})
        self.assertEqual(repeat.status_code, 400)
        self.assertEqual(len(self.user_and_jobs()[1]), 5)
        with Session(self.engine) as db:
            self.assertEqual(db.query(Lead).count(), 0)

    def test_each_disabled_flag_preserves_direct_owner_fallback_and_creates_no_jobs(self):
        for index, name in enumerate(("lead_delivery_enabled", "customer_messages_enabled")):
            with self.subTest(flag=name), patch.object(settings, name, False):
                result = self.verify(f"fallback{index}@example.com")
                self.assertEqual(result.status_code, 200)
                self.assertEqual(self.user_and_jobs(f"fallback{index}@example.com")[1], {})
        self.assertEqual(self.direct.call_count, 2)

    def test_google_only_first_new_verified_account_queues_and_existing_link_does_not(self):
        self.assertEqual(self.google().status_code, 200)
        self.assertEqual(len(self.user_and_jobs("google@example.com")[1]), 5)
        self.assertEqual(self.google().status_code, 200)
        self.assertEqual(len(self.user_and_jobs("google@example.com")[1]), 5)
        with Session(self.engine) as db:
            db.add(User(email="existing@example.com", display_name="Existing", role="buyer", email_verified=True, status="active"))
            db.commit()
        self.assertEqual(self.google("existing@example.com", sub="another-google-id").status_code, 200)
        self.assertEqual(self.user_and_jobs("existing@example.com")[1], {})
        self.direct.assert_not_called()

    def test_queue_failure_rolls_back_new_user_and_pending_promotion(self):
        with patch.object(service, "enqueue_signup_delivery", side_effect=RuntimeError("synthetic queue failure")):
            result = self.verify()
        self.assertEqual(result.status_code, 500)
        with Session(self.engine) as db:
            self.assertIsNone(db.query(User).filter(User.email == "new@example.com").first())
            self.assertIsNotNone(db.query(PendingUser).filter(PendingUser.email == "new@example.com").first())
            self.assertEqual(db.query(Job).count(), 0)
        self.direct.assert_not_called()

    def test_original_new_user_marker_refuses_committed_accounts_and_unverified_new_users(self):
        with Session(self.engine) as db:
            with self.assertRaises(ValueError):
                service.enqueue_signup_delivery(db, db.get(User, self.admin_id))
            row = User(email="unverified@example.com", display_name="Unverified", email_verified=False, role="buyer", status="active")
            db.add(row); db.flush()
            with self.assertRaises(ValueError):
                service.enqueue_signup_delivery(db, row)
            db.rollback()
        with Session(self.engine) as db:
            self.assertEqual(db.query(Job).count(), 0)

    def test_bounded_locale_rejects_unknown_values_before_account_or_provider_work(self):
        result = self.client.post("/auth/verify", json={"email": "new@example.com", "code": "123456", "locale": "fr"})
        self.assertEqual(result.status_code, 422)
        with patch.object(auth.urllib.request, "urlopen") as google:
            self.assertEqual(self.client.post("/auth/google", json={"access_token": "synthetic", "locale": "pt"}).status_code, 422)
            google.assert_not_called()

    def test_pending_registration_resend_wrong_code_and_unverified_google_do_not_enqueue(self):
        with patch.object(auth, "hash_password", return_value="synthetic-only-hash"):
            registered = self.client.post("/auth/register", json={"email": "pending@example.com", "display_name": "Pending",
                                                                      "password": "synthetic-password"})
        self.assertEqual(registered.status_code, 201)
        with Session(self.engine) as db:
            db.query(PendingUser).filter(PendingUser.email == "pending@example.com").one().last_code_sent_at = None
            db.commit()
        self.assertEqual(self.client.post("/auth/resend-code", json={"email": "pending@example.com"}).status_code, 200)
        self.assertEqual(self.client.post("/auth/verify", json={"email": "pending@example.com", "code": "wrong"}).status_code, 400)
        self.assertEqual(self.google(email_verified=False).status_code, 401)
        with Session(self.engine) as db:
            self.assertEqual(db.query(Job).count(), 0)
            self.assertEqual(db.query(User).count(), 1)
        self.direct.assert_not_called()

    def test_customer_inquiry_requires_consent_email_real_event_and_excludes_booking(self):
        identity, payload = self.lead()
        self.assertIn("customer_email", self.lead_jobs(identity))
        self.assertIn("Gracias", self.lead_jobs(identity)["customer_email"].payload["body"]["subject"])
        repeat = self.client.post("/leads", json=payload)
        self.assertEqual(UUID(repeat.json()["id"]), identity)
        for changes in ({"tracking": None}, {"tracking": {**payload["tracking"], "internal_test": True}},
                        {"email": None, "phone": "+18095551234"}, {"type": "booking"}):
            other, _ = self.lead(**changes)
            self.assertNotIn("customer_email", self.lead_jobs(other))
        no_consent = {**payload, "submission_id": str(uuid4()), "tracking": {**payload["tracking"], "contact_consent": False}}
        self.assertEqual(self.client.post("/leads", json=no_consent).status_code, 422)

    def test_public_listing_link_requires_actual_public_record_not_supplied_snapshot(self):
        public, _ = self.lead(type="property_inquiry", property_id=str(self.public_id))
        self.assertIn(f"/listing/{self.public_id}/", self.lead_jobs(public)["customer_email"].payload["body"]["html"])
        with Session(self.engine) as db:
            row = Lead(type="property_inquiry", name="Synthetic", email="private-context@example.com", property_id=self.private_id,
                       status="new", tracking={"contact_consent": True, "locale": "en"})
            db.add(row); db.flush()
            service.enqueue_lead_delivery(db, row, {"id": str(self.private_id), "title": "Do not trust this snapshot"}, include_crm=False)
            db.commit(); private = row.id
        body = self.lead_jobs(private)["customer_email"].payload["body"]
        self.assertIn("/search/", body["text"])
        self.assertNotIn(str(self.private_id), body["html"])

    def test_customer_dispatch_rechecks_lead_address_consent_internal_status_and_listing(self):
        cases = ("address", "consent", "internal", "listing")
        for case in cases:
            with self.subTest(case=case):
                lead_id, _ = self.lead(type="property_inquiry", property_id=str(self.public_id))
                job = self.lead_jobs(lead_id)["customer_email"]
                with Session(self.engine) as db:
                    lead = db.get(Lead, lead_id)
                    if case == "address":
                        lead.email = "changed@example.com"
                    elif case in {"consent", "internal"}:
                        lead.tracking = {**lead.tracking, "contact_consent": case != "consent", "internal_test": case == "internal"}
                    else:
                        db.get(Listing, self.public_id).status = "archived"
                    db.commit()
                result = self.run_job(job.id)
                self.assertEqual(result.status, "needs_review")
                self.assertEqual(self.calls, [])

    def test_signup_dispatch_rechecks_active_verified_account_current_email_and_feature_flag(self):
        for case in ("address", "verification", "suspension", "feature"):
            with self.subTest(case=case):
                address = f"guard-{case}@example.com"
                self.assertEqual(self.verify(address).status_code, 200)
                user_id, jobs = self.user_and_jobs(address)
                with Session(self.engine) as db:
                    user = db.get(User, user_id)
                    if case == "address":
                        user.email = "changed@example.com"
                    elif case == "verification":
                        user.email_verified = False
                    elif case == "suspension":
                        user.status = "suspended"
                    db.commit()
                with patch.object(settings, "customer_messages_enabled", case != "feature"):
                    result = self.run_job(jobs["customer_email"].id)
                self.assertEqual(result.status, "needs_review")
                self.assertEqual(self.calls, [])

    def test_owner_destination_is_rechecked_and_signup_keys_are_distinct_from_leads(self):
        self.assertEqual(self.verify().status_code, 200)
        user_id, jobs = self.user_and_jobs()
        accepted = self.run_job(jobs["customer_email"].id)
        self.assertEqual(accepted.status, "accepted")
        self.assertEqual(self.calls[0].headers["Idempotency-Key"], f"website-signup/{user_id}/customer_email/v1")
        with Session(self.engine) as db:
            db.get(SiteSettings, 1).data = {"notify_email": "replacement@example.com"}
            owner = db.get(Job, jobs["email_1"].id)
            owner.status = "pending"  # isolate_job marked unrelated synthetic jobs accepted.
            db.commit()
        self.assertEqual(self.run_job(jobs["email_1"].id).status, "needs_review")
        self.assertEqual(self.calls, [])

    def test_dependency_claim_matches_signup_identity_not_null_lead_ids(self):
        self.verify("first@example.com"); self.verify("second@example.com")
        _, first = self.user_and_jobs("first@example.com")
        _, second = self.user_and_jobs("second@example.com")
        with Session(self.engine) as db:
            db.query(Job).update({Job.status: "accepted"}, synchronize_session=False)
            db.get(Job, second["email_1"].id).status = "failed"
            customer = db.get(Job, second["customer_email"].id)
            customer.status, customer.depends_on = "pending", "email_1"
            db.commit()
            self.assertIsNone(service.claim_next_job(db))
            db.get(Job, second["email_1"].id).status = "accepted"
            db.commit()
            self.assertEqual(service.claim_next_job(db).job_id, second["customer_email"].id)

    def test_customer_failure_is_held_and_only_one_explicit_manual_retry_can_dispatch(self):
        self.verify()
        _, jobs = self.user_and_jobs()
        result = self.run_job(jobs["customer_email"].id, status=400)
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.safe_to_retry)
        with Session(self.engine) as db:
            self.assertIsNone(service.claim_next_job(db))
            db.get(Job, result.id).next_attempt_at = service.utcnow() - timedelta(seconds=1)
            db.commit()
        request = self.client.post(f"/admin/communications/{result.id}/retry", json={}, headers=self.admin_headers)
        self.assertEqual(request.status_code, 200)
        accepted = self.run_job(result.id)
        self.assertEqual(accepted.status, "accepted")
        self.assertEqual(accepted.attempt_count, 2)

    def test_legacy_lead_admin_views_exclude_signups_and_generic_metadata_has_no_payload_or_contact(self):
        self.verify()
        user_id, jobs = self.user_and_jobs()
        self.assertEqual(self.client.get("/admin/lead-deliveries", headers=self.admin_headers).json()["total"], 0)
        self.assertEqual(self.client.get("/admin/leads/delivery-summary", headers=self.admin_headers).json()["total_jobs"], 0)
        result = self.client.get("/admin/communications?event_kind=signup", headers=self.admin_headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertEqual(result.json()["total"], 5)
        for private in ("payload", "html", "text", "new@example.com", "Synthetic New Buyer", str(user_id), "owner1@example.com"):
            self.assertNotIn(private, result.text)
        self.assertEqual(self.client.get("/admin/communications").status_code, 401)
        self.assertEqual(self.client.post(f"/admin/lead-deliveries/{jobs['customer_email'].id}/retry", json={}, headers=self.admin_headers).status_code, 404)

    def test_database_xor_event_and_signup_step_uniqueness_constraints(self):
        self.verify()
        user_id, jobs = self.user_and_jobs()
        lead_id, _ = self.lead()
        for lead, user in ((None, None), (lead_id, user_id)):
            with self.subTest(lead=bool(lead), user=bool(user)), self.assertRaises(IntegrityError):
                with self.engine.begin() as connection:
                    connection.execute(text("""INSERT INTO lead_delivery_jobs(id,lead_id,user_id,step,provider,payload,payload_sha256)
                        VALUES(:id,:lead,:user,'synthetic','resend','{}'::jsonb,'synthetic')"""), {"id": uuid4(), "lead": lead, "user": user})
        with self.assertRaises(IntegrityError), Session(self.engine) as db:
            db.add(Job(user_id=user_id, step="customer_email", provider="resend", payload={}, payload_sha256="synthetic"))
            db.commit()

    def test_generic_email_retry_rejects_crm_job_while_legacy_retry_stays_available(self):
        lead_id, _ = self.lead()
        job = self.lead_jobs(lead_id)["crm_contact"]
        with Session(self.engine) as db:
            row = db.get(Job, job.id)
            row.status, row.attempt_count = "failed", 1
            row.safe_to_retry, row.outcome_uncertain = True, False
            row.error_code = "ghl_http_400"
            row.next_attempt_at = service.utcnow() - timedelta(seconds=1)
            db.commit()
        rejected = self.client.post(f"/admin/communications/{job.id}/retry", json={}, headers=self.admin_headers)
        self.assertEqual(rejected.status_code, 404)
        accepted = self.client.post(f"/admin/lead-deliveries/{job.id}/retry", json={}, headers=self.admin_headers)
        self.assertEqual(accepted.status_code, 200)
        with Session(self.engine) as db:
            row = db.get(Job, job.id)
            self.assertEqual(row.status, "pending")
            self.assertTrue(row.error_code.startswith(service.MANUAL_RETRY_PREFIX))

    def test_actual_additive_migration_preserves_legacy_jobs_and_does_not_enqueue_anything(self):
        with patch.object(settings, "customer_messages_enabled", False):
            lead_id, _ = self.lead()
        before = self.lead_jobs(lead_id)
        path = Path(__file__).resolve().parents[1] / "alembic/versions/e2f4a8b0c612_signup_delivery_events.py"
        spec = importlib.util.spec_from_file_location("signup_event_migration_test", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with self.engine.begin() as connection:
            connection.execute(text("ALTER TABLE lead_delivery_jobs DROP COLUMN user_id CASCADE"))
            connection.execute(text("ALTER TABLE lead_delivery_jobs ALTER COLUMN lead_id SET NOT NULL"))
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
        after = self.lead_jobs(lead_id)
        self.assertEqual(set(after), set(before))
        for step in before:
            self.assertEqual((after[step].id, after[step].payload_sha256, after[step].payload),
                             (before[step].id, before[step].payload_sha256, before[step].payload))
        with Session(self.engine) as db:
            self.assertEqual(db.query(Job).filter(Job.user_id.isnot(None)).count(), 0)


if __name__ == "__main__":
    unittest.main()
