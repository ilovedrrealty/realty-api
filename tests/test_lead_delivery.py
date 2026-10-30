"""Durable new-event delivery against disposable loopback Postgres only.

All provider requests use httpx.MockTransport. These tests cannot send emails,
write CRM contacts or touch production records.
"""
import asyncio
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import re
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from dataclasses import replace
from unittest.mock import patch
from uuid import UUID, uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-outbox-tests-only",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
                  SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")
import httpx
import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from alembic.migration import MigrationContext
from alembic.operations import Operations

from config import settings
from database import Base, get_db
import models
from models.booking import Booking
from models.lead import Lead
from models.lead_delivery import DELIVERY_STATUSES, LeadDeliveryJob, LeadDeliveryWorkerState
from models.listing import Listing
from models.site_settings import SiteSettings
from models.user import User
from routes import bookings, leads, lead_delivery as delivery_routes
from services import lead_delivery as service
from utils.limiter import limiter


def body(**changes):
    return {"type": "buyer_interest", "name": "Synthetic private inquiry", "phone": "+18095551234",
            "message": "Synthetic message only", "submission_id": str(uuid4()), **changes}


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires disposable loopback Postgres")
class LeadDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host not in ("127.0.0.1", "localhost", "::1") or url.get_backend_name() != "postgresql":
            raise ValueError("Tests require disposable loopback Postgres")
        cls.schema = "lead_delivery_test_" + uuid4().hex
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        engine.dispose()
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema}, pool_size=10)
        Base.metadata.create_all(cls.engine)
        app = FastAPI()
        app.state.limiter = limiter
        app.include_router(leads.router)
        app.include_router(delivery_routes.router)
        app.include_router(bookings.router)

        def local_db():
            with Session(cls.engine) as db:
                yield db
        app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(app)
        cls.old_limiter = limiter.enabled
        limiter.enabled = False
        cls.headers = {}
        cls.ids = {}
        with Session(cls.engine) as db:
            db.add(SiteSettings(id=1, data={"notify_email": "owner@example.com,other@example.com"}))
            for role in ("admin", "buyer", "owner", "realtor"):
                user = User(email=f"synthetic-outbox-{role}@example.com", display_name="Synthetic actor",
                            role=role, email_verified=True, status="active")
                db.add(user); db.flush()
                cls.ids[role] = user.id
                cls.headers[role] = {"Authorization": "Bearer " + jwt.encode(
                    {"sub": str(user.id), "exp": datetime.now(timezone.utc)+timedelta(hours=1)},
                    settings.jwt_secret, algorithm="HS256")}
            listing = Listing(title="Synthetic rental", type="condo", transaction="rent", price=100000,
                              price_per_day=150, location="Synthetic location", status="active",
                              submitted_by=cls.ids["admin"], owner_id=cls.ids["owner"])
            db.add(listing); db.flush(); cls.listing_id = listing.id
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        limiter.enabled = cls.old_limiter
        assert re.fullmatch(r"lead_delivery_test_[a-f0-9]{32}", cls.schema)
        with cls.engine.begin() as connection:
            connection.execute(text("SET search_path TO public"))
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.engine.dispose()

    def setUp(self):
        with Session(self.engine) as db:
            db.query(LeadDeliveryJob).delete()
            db.query(Booking).delete()
            db.query(Lead).delete()
            db.query(LeadDeliveryWorkerState).delete()
            db.get(SiteSettings, 1).data = {"notify_email": "owner@example.com,other@example.com"}
            db.commit()
        for name, value in (("ghl_enabled", True), ("ghl_api_key", "synthetic-not-a-real-key"),
                            ("ghl_location_id", "synthetic-location"), ("resend_api_key", "synthetic-not-a-real-key"),
                            ("email_from", "no-reply@example.com"), ("notify_email", "owner@example.com")):
            patcher = patch.object(settings, name, value)
            patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(service, "enabled", return_value=True)
        patcher.start(); self.addCleanup(patcher.stop)

    def factory(self):
        return Session(self.engine)

    def submit(self, payload=None):
        with patch.object(leads.ghl, "create_contact") as crm, patch.object(leads, "send_lead_notification") as email:
            response = self.client.post("/leads", json=payload or body())
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            crm.assert_not_called(); email.assert_not_called()
        return UUID(response.json()["id"])

    def jobs(self, lead_id):
        with self.factory() as db:
            return {row.step: row for row in db.query(LeadDeliveryJob).filter(LeadDeliveryJob.lead_id == lead_id).all()}

    def due_now(self, job_id):
        with self.factory() as db:
            db.get(LeadDeliveryJob, job_id).next_attempt_at = service.utcnow()-timedelta(seconds=1)
            db.commit()

    def transport(self, handler=None):
        self.calls = []
        def send(request):
            self.assertIn(request.url.host, ("api.resend.com", "services.leadconnectorhq.com"))
            self.calls.append(request)
            if handler:
                result = handler(request)
                if result is not None:
                    return result
            if request.url.path == "/contacts/":
                return httpx.Response(201, json={"contact": {"id": "synthetic-contact"}})
            if request.url.host == "api.resend.com":
                target = json.loads(request.content)["to"][0]
                return httpx.Response(200, json={"id": "synthetic-email-"+target.split("@")[0]})
            return httpx.Response(200, json={"id": "synthetic-operation"})
        return httpx.Client(transport=httpx.MockTransport(send))

    def test_new_lead_and_all_steps_are_atomic_and_duplicate_submission_is_one_event(self):
        payload = body()
        lead_id = self.submit(payload)
        self.assertEqual(self.submit(payload), lead_id)
        rows = self.jobs(lead_id)
        self.assertEqual(set(rows), {"crm_contact", "crm_tags", "crm_note", "crm_task", "email_1", "email_2"})
        self.assertTrue(all(row.status == "pending" and row.attempt_count == 0 for row in rows.values()))
        self.assertEqual({rows[key].payload["body"]["to"][0] for key in ("email_1", "email_2")}, {"owner@example.com", "other@example.com"})
        self.assertTrue(all(row.payload_sha256 == service.payload_digest(row.payload) for row in rows.values()))
        with self.factory() as db:
            self.assertEqual(db.query(Lead).count(), 1)

    def test_concurrent_intake_has_one_lead_and_one_set_of_jobs(self):
        payload = body()
        with patch.object(leads.ghl, "create_contact") as crm, patch.object(leads, "send_lead_notification") as email:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                responses = list(executor.map(lambda _: self.client.post("/leads", json=payload), range(4)))
            self.assertTrue(all(r.status_code == 201 for r in responses), [r.text for r in responses])
            self.assertEqual(len({r.json()["id"] for r in responses}), 1)
            crm.assert_not_called(); email.assert_not_called()
        with self.factory() as db:
            self.assertEqual(db.query(Lead).count(), 1)
            self.assertEqual(db.query(LeadDeliveryJob).count(), 6)

    def test_enqueue_failure_rolls_back_lead_and_partial_jobs(self):
        original = service.enqueue_lead_delivery
        def broken(db, lead, *args, **kwargs):
            original(db, lead, *args, **kwargs)
            db.flush()
            raise RuntimeError("Synthetic rollback")
        with patch.object(service, "enqueue_lead_delivery", side_effect=broken):
            with self.assertRaises(RuntimeError):
                self.client.post("/leads", json=body())
        with self.factory() as db:
            self.assertEqual(db.query(Lead).count(), 0)
            self.assertEqual(db.query(LeadDeliveryJob).count(), 0)

    def test_historical_leads_cannot_be_enqueued_and_polling_never_backfills(self):
        with self.factory() as db:
            lead = Lead(type="buyer_interest", name="Synthetic old lead", phone="+18095551234", status="new")
            db.add(lead); db.commit(); db.refresh(lead)
            with self.assertRaises(ValueError):
                service.enqueue_lead_delivery(db, lead)
        with self.transport() as client:
            self.assertEqual(service.run_batch(self.factory, client=client)["processed"], 0)
        self.assertEqual(self.calls, [])
        with self.factory() as db:
            self.assertEqual(db.query(LeadDeliveryJob).count(), 0)
            self.assertIsNotNone(db.get(LeadDeliveryWorkerState, 1).last_completed_at)

    def test_paused_cutover_preserves_legacy_new_submission_without_jobs(self):
        with patch.object(service, "enabled", return_value=False), patch.object(leads.ghl, "create_contact") as crm, patch.object(leads, "send_lead_notification") as send:
            response = self.client.post("/leads", json=body())
            self.assertEqual(response.status_code, 201)
            crm.assert_called_once(); send.assert_called_once()
        with self.factory() as db:
            self.assertEqual(db.query(LeadDeliveryJob).count(), 0)
        with patch.object(service, "enabled", return_value=False):
            self.assertEqual(service.run_batch(lambda: self.fail("Paused worker accessed DB"))["processed"], 0)

    def test_worker_accepts_each_step_once_and_keeps_crm_projection(self):
        lead_id = self.submit()
        with self.transport() as client:
            result = service.run_batch(self.factory, client=client)
            self.assertEqual(result["processed"], 6)
            self.assertEqual(service.run_batch(self.factory, client=client)["processed"], 0)
        rows = self.jobs(lead_id)
        self.assertTrue(all(row.status == "accepted" and row.attempt_count == 1 for row in rows.values()))
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(len([r for r in self.calls if r.url.path.endswith("/notes")]), 1)
        self.assertEqual(len([r for r in self.calls if r.url.path.endswith("/tasks")]), 1)
        with self.factory() as db:
            lead = db.get(Lead, lead_id)
            self.assertEqual(lead.ghl_contact_id, "synthetic-contact")
            self.assertIsNotNone(lead.ghl_synced_at)
            self.assertIsNone(lead.ghl_sync_error)

    def test_concurrent_claims_cannot_take_same_job_twice(self):
        lead_id = self.submit()
        def claim(_):
            with self.factory() as db:
                return service.claim_next_job(db)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            claims = list(executor.map(claim, range(8)))
        claimed = [x for x in claims if x]
        # Only contact and the two independent email steps are initially ready.
        self.assertEqual(len(claimed), 3)
        self.assertEqual(len({x.job_id for x in claimed}), 3)
        self.assertTrue(all(x.status in ("pending", "processing") for x in self.jobs(lead_id).values()))

    def test_concurrent_workers_accept_each_step_once_and_finish_one_crm_projection(self):
        lead_id = self.submit()
        with self.transport() as client:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                results = list(executor.map(lambda _: service.run_batch(self.factory, client=client), range(3)))
        self.assertEqual(sum(x["processed"] for x in results), 6)
        self.assertEqual(len(self.calls), 6)
        self.assertTrue(all(row.status == "accepted" and row.attempt_count == 1 for row in self.jobs(lead_id).values()))
        with self.factory() as db:
            lead = db.get(Lead, lead_id)
            self.assertIsNotNone(lead.ghl_synced_at)
            self.assertIsNone(lead.ghl_sync_error)

    def test_expired_claim_goes_to_review_and_is_never_automatically_replayed(self):
        lead_id = self.submit()
        with self.factory() as db:
            claim = service.claim_next_job(db)
            db.get(LeadDeliveryJob, claim.job_id).lease_until = service.utcnow()-timedelta(seconds=1)
            db.commit()
            self.assertEqual(service.expire_stale_claims(db, service.utcnow()), 1)
            self.assertFalse(service.process_claimed_job(db, claim))
        row = next(x for x in self.jobs(lead_id).values() if x.id == claim.job_id)
        self.assertEqual(row.status, "needs_review")
        self.assertTrue(row.outcome_uncertain)
        self.assertFalse(row.safe_to_retry)

    def test_contact_ambiguity_blocks_followups_but_email_still_runs(self):
        lead_id = self.submit()
        def handler(request):
            if request.url.path == "/contacts/":
                raise httpx.ReadTimeout("private provider/customer data", request=request)
        with self.transport(handler) as client:
            service.run_batch(self.factory, client=client)
        rows = self.jobs(lead_id)
        self.assertEqual(rows["crm_contact"].status, "needs_review")
        self.assertTrue(all(rows[key].status == "pending" for key in ("crm_note", "crm_task", "crm_tags")))
        self.assertEqual(rows["email_1"].status, "accepted")
        self.assertFalse(any(r.url.path.endswith(("/notes", "/tasks", "/tags")) for r in self.calls))

    def test_expired_crm_claim_updates_existing_analytics_error_projection(self):
        lead_id = self.submit()
        with self.factory() as db:
            db.query(LeadDeliveryJob).filter(LeadDeliveryJob.provider == "resend").update(
                {"next_attempt_at": service.utcnow()+timedelta(hours=1)})
            db.commit()
            claim = service.claim_next_job(db)
            row = db.get(LeadDeliveryJob, claim.job_id)
            self.assertEqual(row.step, "crm_contact")
            row.lease_until = service.utcnow()-timedelta(seconds=1); db.commit()
            service.expire_stale_claims(db, service.utcnow())
            self.assertIn("worker_interrupted_outcome_unknown", db.get(Lead, lead_id).ghl_sync_error)

    def test_ambiguous_note_is_not_retried_or_replayed_as_part_of_whole_contact(self):
        lead_id = self.submit()
        def handler(request):
            if request.url.path.endswith("/notes"):
                return httpx.Response(503, json={"private": "contact token customer body"})
        with self.transport(handler) as client:
            service.run_batch(self.factory, client=client)
            service.run_batch(self.factory, client=client)
        rows = self.jobs(lead_id)
        self.assertEqual(rows["crm_note"].status, "needs_review")
        self.assertEqual(rows["crm_note"].attempt_count, 1)
        self.assertEqual(rows["crm_task"].status, "accepted")
        self.assertEqual(rows["crm_contact"].attempt_count, 1)
        with self.factory() as db:
            lead = db.get(Lead, lead_id)
            self.assertEqual(lead.ghl_contact_id, "synthetic-contact")
            self.assertIsNone(lead.ghl_synced_at)
            self.assertIn("crm_note:ghl_http_503", lead.ghl_sync_error)
            self.assertNotIn("private", lead.ghl_sync_error)

    def test_duplicate_contact_reuses_confirmed_id_and_adds_tags_separately(self):
        lead_id = self.submit()
        def handler(request):
            if request.url.path == "/contacts/":
                self.assertNotIn("tags", json.loads(request.content))
                return httpx.Response(400, json={"meta": {"contactId": "synthetic-existing"}})
        with self.transport(handler) as client:
            service.run_batch(self.factory, client=client)
        rows = self.jobs(lead_id)
        self.assertEqual(rows["crm_contact"].provider_id, "synthetic-existing")
        self.assertTrue(any(r.url.path == "/contacts/synthetic-existing/tags" for r in self.calls))

    def test_known_connection_failure_needs_one_explicit_manual_action_after_delay(self):
        lead_id = self.submit()
        first = True
        def handler(request):
            nonlocal first
            if request.url.path == "/contacts/" and first:
                first = False
                raise httpx.ConnectTimeout("Synthetic pre-send failure", request=request)
        with self.transport(handler) as client:
            service.run_batch(self.factory, client=client)
            row = self.jobs(lead_id)["crm_contact"]
            self.assertEqual(row.status, "failed")
            self.assertFalse(row.outcome_uncertain)
            self.assertGreater(row.next_attempt_at, row.last_attempt_at)
            self.assertEqual(service.run_batch(self.factory, client=client)["processed"], 0)
            self.due_now(row.id)
            before = len(self.calls)
            for _ in range(4):
                self.assertEqual(service.run_batch(self.factory, client=client)["processed"], 0)
            self.assertEqual(len(self.calls), before)
            first_at, body_before = row.first_attempt_at, row.payload
            response = self.client.post(f"/admin/lead-deliveries/{row.id}/retry", json={}, headers=self.headers["admin"])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "pending")
            service.run_batch(self.factory, client=client)
        updated = self.jobs(lead_id)["crm_contact"]
        self.assertEqual(updated.attempt_count, 2)
        self.assertEqual(updated.first_attempt_at, first_at)
        self.assertEqual(updated.payload, body_before)
        self.assertTrue(all(row.status == "accepted" for row in self.jobs(lead_id).values()))

    def test_rate_limit_respects_provider_delay_and_manual_action_cannot_bypass_it(self):
        lead_id = self.submit()
        def handler(request):
            if request.url.path.endswith("/tasks"):
                return httpx.Response(429, headers={"Retry-After": "600"}, json={"private": "not retained"})
        with self.transport(handler) as client:
            service.run_batch(self.factory, client=client)
        row = self.jobs(lead_id)["crm_task"]
        self.assertEqual(row.status, "failed")
        self.assertGreaterEqual((row.next_attempt_at-row.last_attempt_at).total_seconds(), 600)
        response = self.client.post(f"/admin/lead-deliveries/{row.id}/retry", json={}, headers=self.headers["admin"])
        self.assertEqual(response.status_code, 409)
        status = self.client.get(f"/admin/leads/{lead_id}/delivery", headers=self.headers["admin"]).json()
        self.assertFalse(next(item for item in status["jobs"] if item["id"] == str(row.id))["can_retry"])

    def test_safe_retries_stop_at_the_durable_attempt_limit(self):
        lead_id = self.submit()
        def handler(request):
            if request.url.path == "/contacts/":
                raise httpx.ConnectError("Synthetic pre-send failure", request=request)
        with self.transport(handler) as client:
            for attempt in range(4):
                row = self.jobs(lead_id)["crm_contact"]
                self.due_now(row.id)
                if attempt:
                    response = self.client.post(f"/admin/lead-deliveries/{row.id}/retry", json={}, headers=self.headers["admin"])
                    self.assertEqual(response.status_code, 200, response.text)
                service.run_batch(self.factory, client=client)
                self.due_now(row.id)
                for _ in range(2):
                    self.assertEqual(service.run_batch(self.factory, client=client)["processed"], 0)
            self.assertEqual(service.run_batch(self.factory, client=client)["processed"], 0)
        row = self.jobs(lead_id)["crm_contact"]
        self.assertEqual(row.attempt_count, 4)
        self.assertEqual(row.status, "failed")
        self.assertEqual(len([r for r in self.calls if r.url.path == "/contacts/"]), 4)
        self.assertEqual(self.client.post(f"/admin/lead-deliveries/{row.id}/retry", json={}, headers=self.headers["admin"]).status_code, 409)

    def test_resend_uncertainty_is_never_repeated_even_inside_idempotency_window(self):
        lead_id = self.submit()
        first = True
        def handler(request):
            nonlocal first
            if request.url.host == "api.resend.com" and json.loads(request.content)["to"] == ["owner@example.com"] and first:
                first = False
                raise httpx.ReadTimeout("Synthetic unknown response", request=request)
        with self.transport(handler) as client:
            service.run_batch(self.factory, client=client)
            row = self.jobs(lead_id)["email_1"]
            self.assertEqual(row.status, "needs_review")
            self.assertTrue(row.outcome_uncertain)
            self.due_now(row.id)
            for _ in range(4):
                service.run_batch(self.factory, client=client)
            self.assertEqual(self.client.post(f"/admin/lead-deliveries/{row.id}/retry", json={}, headers=self.headers["admin"]).status_code, 409)
        requests = [r for r in self.calls if r.url.host == "api.resend.com" and json.loads(r.content)["to"] == ["owner@example.com"]]
        self.assertEqual(len(requests), 1)
        self.assertEqual(self.jobs(lead_id)["email_1"].status, "needs_review")

    def test_uncertain_email_after_retention_window_never_calls_provider(self):
        lead_id = self.submit()
        with self.factory() as db:
            row = db.query(LeadDeliveryJob).filter_by(lead_id=lead_id, step="email_1").one()
            row.status = "retry_wait"; row.outcome_uncertain = True
            row.first_attempt_at = service.utcnow()-timedelta(hours=24)
            row.next_attempt_at = service.utcnow()-timedelta(seconds=1)
            row.attempt_count = 1; db.commit()
        with self.transport() as client:
            service.run_batch(self.factory, client=client)
        self.assertEqual(self.jobs(lead_id)["email_1"].status, "retry_wait")
        self.assertEqual(self.client.post(f"/admin/lead-deliveries/{self.jobs(lead_id)['email_1'].id}/retry", json={}, headers=self.headers["admin"]).status_code, 409)
        self.assertFalse(any(r.url.host == "api.resend.com" and json.loads(r.content)["to"] == ["owner@example.com"] for r in self.calls))

    def test_later_rejection_does_not_erase_an_earlier_uncertain_email_outcome(self):
        lead_id = self.submit()
        first = True
        def handler(request):
            nonlocal first
            if request.url.host == "api.resend.com" and json.loads(request.content)["to"] == ["owner@example.com"]:
                if first:
                    first = False
                    raise httpx.ReadTimeout("Synthetic unknown outcome", request=request)
                return httpx.Response(401, json={"private": "not retained"})
        with self.transport(handler) as client:
            service.run_batch(self.factory, client=client)
            self.due_now(self.jobs(lead_id)["email_1"].id)
            service.run_batch(self.factory, client=client)
        row = self.jobs(lead_id)["email_1"]
        self.assertEqual(row.status, "needs_review")
        self.assertTrue(row.outcome_uncertain)
        self.assertFalse(row.safe_to_retry)

    def test_attempt_limit_and_unknown_states_cannot_be_admin_retried(self):
        lead_id = self.submit()
        rows = self.jobs(lead_id)
        with self.factory() as db:
            row = db.get(LeadDeliveryJob, rows["email_1"].id)
            row.status="failed"; row.safe_to_retry=True; row.attempt_count=4
            other = db.get(LeadDeliveryJob, rows["crm_contact"].id)
            other.status="needs_review"; other.outcome_uncertain=True
            db.commit()
        for row in (rows["email_1"], rows["crm_contact"]):
            result = self.client.post(f"/admin/lead-deliveries/{row.id}/retry", json={}, headers=self.headers["admin"])
            self.assertEqual(result.status_code, 409)

    def test_admin_retry_requeues_only_known_rejection_and_does_not_send(self):
        lead_id = self.submit()
        def handler(request):
            if request.url.path == "/contacts/":
                return httpx.Response(401, json={"private": "not retained"})
        with self.transport(handler) as client:
            service.run_batch(self.factory, client=client)
        row = self.jobs(lead_id)["crm_contact"]
        self.assertEqual(row.status, "failed")
        self.due_now(row.id)
        with patch.object(service, "_provider_result", side_effect=AssertionError("Admin retry sent a provider request")):
            response = self.client.post(f"/admin/lead-deliveries/{row.id}/retry", json={}, headers=self.headers["admin"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        queued = self.jobs(lead_id)["crm_contact"]
        self.assertEqual(queued.status, "pending")
        self.assertEqual(service.manual_retry_prior_code(queued.error_code), row.error_code)
        status = self.client.get(f"/admin/leads/{lead_id}/delivery", headers=self.headers["admin"]).json()
        item = next(item for item in status["jobs"] if item["id"] == str(row.id))
        self.assertEqual(item["error_code"], row.error_code)
        self.assertFalse(item["can_retry"])

    def test_status_endpoints_are_admin_only_private_and_do_not_expose_payloads(self):
        lead_id = self.submit()
        paths = ["/admin/leads/delivery-summary", f"/admin/leads/{lead_id}/delivery", "/admin/lead-deliveries"]
        for path in paths:
            for role in (None, "buyer", "owner", "realtor"):
                response = self.client.get(path, headers=self.headers.get(role, {}))
                self.assertEqual(response.status_code, 401 if role is None else 403)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(response.headers["pragma"], "no-cache")
                self.assertEqual(response.headers["vary"], "Cookie, Authorization")
            response = self.client.get(path, headers=self.headers["admin"])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            for private in ("owner@example.com", "other@example.com", "Synthetic private inquiry", "+18095551234", "Synthetic message", "<html", "payload_sha256"):
                self.assertNotIn(private, response.text)
        response = self.client.get("/admin/lead-deliveries?status=unknown", headers=self.headers["admin"])
        self.assertEqual(response.status_code, 422)

    def test_all_delivery_errors_are_private_without_input_echo_or_provider_calls(self):
        private_input = "private-synthetic-query@example.com"
        cases = [
            ("get", f"/admin/leads/{private_input}/delivery", 422),
            ("get", f"/admin/lead-deliveries?lead_id={private_input}", 422),
            ("get", f"/admin/lead-deliveries?limit={private_input}", 422),
            ("get", "/admin/lead-deliveries?limit=101", 422),
            ("get", "/admin/lead-deliveries?offset=-1", 422),
            ("get", "/admin/lead-deliveries?offset=10001", 422),
            ("get", f"/admin/lead-deliveries?status={private_input}", 422),
            ("get", f"/admin/leads/{uuid4()}/delivery", 404),
            ("post", f"/admin/lead-deliveries/{private_input}/retry", 422),
            ("post", f"/admin/lead-deliveries/{uuid4()}/retry", 404),
        ]
        with patch.object(service, "_provider_result", side_effect=AssertionError("Status read sent to provider")):
            for method, path, status_code in cases:
                kwargs = {"json": {}} if method == "post" else {}
                result = getattr(self.client, method)(path, headers=self.headers["admin"], **kwargs)
                self.assertEqual(result.status_code, status_code)
                self.assertEqual(result.headers["cache-control"], "no-store")
                self.assertEqual(result.headers["pragma"], "no-cache")
                self.assertEqual(result.headers["vary"], "Cookie, Authorization")
                self.assertNotIn(private_input, result.text)
            for role in (None, "buyer", "owner", "realtor"):
                result = self.client.post(f"/admin/lead-deliveries/{uuid4()}/retry", json={}, headers=self.headers.get(role, {}))
                self.assertEqual(result.status_code, 401 if role is None else 403)
                self.assertEqual(result.headers["cache-control"], "no-store")
        with patch.object(delivery_routes, "_metadata_query", side_effect=RuntimeError("synthetic sensitive SQL body")):
            result = self.client.get("/admin/lead-deliveries", headers=self.headers["admin"])
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertNotIn("sensitive", result.text)

    def test_retry_requires_explicit_json_and_rejects_simple_cross_site_form_bodies(self):
        lead_id = self.submit()
        with self.factory() as db:
            for row in db.query(LeadDeliveryJob).filter(LeadDeliveryJob.lead_id == lead_id, LeadDeliveryJob.step.in_(("email_1", "email_2"))):
                row.status = "failed"; row.safe_to_retry = True; row.error_code = "email_provider_not_configured"
                row.attempt_count = 1; row.next_attempt_at = service.utcnow()-timedelta(seconds=1)
            job_id = db.query(LeadDeliveryJob).filter_by(lead_id=lead_id, step="email_1").one().id
            cookie_job_id = db.query(LeadDeliveryJob).filter_by(lead_id=lead_id, step="email_2").one().id
            db.commit()
        path = f"/admin/lead-deliveries/{job_id}/retry"
        cookie_headers = {"Cookie": "ildr_token=" + self.headers["admin"]["Authorization"][7:]}
        cases = [
            ({}, 415),
            ({"content": "{}"}, 415),
            ({"content": "{}", "headers": {**cookie_headers, "Origin": "https://hostile.example"}}, 415),
            ({"content": "{}", "headers": {**self.headers["admin"], "Content-Type": "text/plain"}}, 415),
            ({"content": "ignored=1", "headers": {**self.headers["admin"], "Content-Type": "application/x-www-form-urlencoded"}}, 415),
            ({"json": {}, "headers": {**cookie_headers, "Origin": "https://hostile.example"}}, 403),
            ({"json": {}, "headers": cookie_headers}, 403),
            ({"json": {}, "headers": {**self.headers["admin"], "Origin": "null"}}, 403),
            ({"json": {"unexpected": "private-synthetic-value"}}, 422),
            ({"content": "{invalid", "headers": {**self.headers["admin"], "Content-Type": "application/json"}}, 422),
        ]
        with patch.object(service, "_provider_result", side_effect=AssertionError("Retry sent a provider request")):
            for kwargs, expected_status in cases:
                kwargs.setdefault("headers", self.headers["admin"])
                result = self.client.post(path, **kwargs)
                self.assertEqual(result.status_code, expected_status, result.text)
                self.assertEqual(result.headers["cache-control"], "no-store")
                self.assertNotIn("private-synthetic-value", result.text)
                self.assertEqual(self.jobs(lead_id)["email_1"].status, "failed")
            result = self.client.post(path, json={}, headers=self.headers["admin"])
            cookie_result = self.client.post(f"/admin/lead-deliveries/{cookie_job_id}/retry", json={},
                                             headers={**cookie_headers, "Origin": "http://testserver"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(cookie_result.status_code, 200, cookie_result.text)
        self.assertEqual(self.jobs(lead_id)["email_1"].status, "pending")
        self.assertEqual(self.jobs(lead_id)["email_2"].status, "pending")

    def test_stopped_worker_does_not_open_database_or_claim_any_job(self):
        stopped = threading.Event(); stopped.set()
        result = service.run_batch(lambda: self.fail("Stopped worker accessed DB"), stop_event=stopped)
        self.assertEqual(result, {"enabled": True, "processed": 0, "stopped": True})

    def test_shutdown_restores_a_claim_that_has_not_contacted_a_provider(self):
        lead_id = self.submit()
        stopped = threading.Event()
        with self.factory() as db:
            claim = service.claim_next_job(db)
            self.assertIsNotNone(claim)
            stopped.set()
            with patch.object(service, "_provider_result", side_effect=AssertionError("Stopped claim sent to provider")):
                self.assertFalse(service.process_claimed_job(db, claim, stop_event=stopped))
        with self.factory() as db:
            row = db.get(LeadDeliveryJob, claim.job_id)
            self.assertEqual(row.lead_id, lead_id)
            self.assertEqual(row.status, "pending")
            self.assertEqual(row.attempt_count, 0)
            self.assertIsNone(row.first_attempt_at)
            self.assertIsNone(row.last_attempt_at)
            self.assertIsNone(row.lease_token)
            self.assertIsNone(row.lease_until)

    def test_worker_cancellation_drains_active_request_without_claiming_followup_jobs(self):
        lead_id = self.submit()
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        def handler(_request):
            started.set()
            if not release.wait(10):
                raise AssertionError("Synthetic provider was not released")
        original_batch = service.run_batch
        async def scenario(client):
            def batch(*args, **kwargs):
                try:
                    return original_batch(*args, client=client, **kwargs)
                finally:
                    finished.set()
            with patch.object(service, "POLL_SECONDS", 0.001), patch.object(service, "run_batch", side_effect=batch):
                task = asyncio.create_task(service.worker_loop(self.factory))
                try:
                    self.assertTrue(await asyncio.wait_for(asyncio.to_thread(started.wait, 10), timeout=11))
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done(), "Shutdown abandoned the active provider thread")
                    self.assertFalse(finished.is_set())
                    task.cancel()  # A repeated shutdown signal still must drain.
                    await asyncio.sleep(0)
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, timeout=11)
                    self.assertTrue(finished.is_set())
                finally:
                    release.set()
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
        with self.transport(handler) as client:
            asyncio.run(scenario(client))
        self.assertEqual(len(self.calls), 1)
        rows = list(self.jobs(lead_id).values())
        self.assertEqual(sum(row.status == "accepted" for row in rows), 1)
        self.assertEqual(sum(row.status == "pending" and row.attempt_count == 0 for row in rows), 5)
        self.assertFalse(any(row.status == "processing" for row in rows))

    def test_stop_at_dispatch_preserves_existing_retry_window_and_budget(self):
        lead_id = self.submit()
        previous = service.utcnow() - timedelta(hours=1)
        with self.factory() as db:
            for row in db.query(LeadDeliveryJob).filter_by(lead_id=lead_id):
                row.status = "skipped"
                if row.step == "email_1":
                    row.status = "retry_wait"; row.attempt_count = 1; row.safe_to_retry = True
                    row.first_attempt_at = previous; row.last_attempt_at = previous
                    row.next_attempt_at = previous; row.error_code = "resend_connection_not_established"
            db.commit()
        job_id = self.jobs(lead_id)["email_1"].id
        response = self.client.post(f"/admin/lead-deliveries/{job_id}/retry", json={}, headers=self.headers["admin"])
        self.assertEqual(response.status_code, 200, response.text)
        stopped = threading.Event()
        original = service._provider_result
        def stop_before_send(*args, **kwargs):
            stopped.set()
            return original(*args, **kwargs)
        with self.factory() as db, self.transport() as client:
            claim = service.claim_next_job(db)
            with patch.object(service, "_provider_result", side_effect=stop_before_send):
                self.assertFalse(service.process_claimed_job(db, claim, client=client, stop_event=stopped))
        self.assertEqual(self.calls, [])
        row = self.jobs(lead_id)["email_1"]
        self.assertEqual((row.status, row.attempt_count, row.safe_to_retry), ("pending", 1, True))
        self.assertEqual(row.first_attempt_at, previous)
        self.assertEqual(row.last_attempt_at, previous)
        self.assertEqual(service.manual_retry_prior_code(row.error_code), "resend_connection_not_established")

    def test_summary_is_atomic_and_queue_filters_are_bounded_metadata_only(self):
        lead_id = self.submit()
        other_id = self.submit()
        statements = []
        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            if "lead_delivery_" in statement:
                statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            with patch.object(service, "_provider_result", side_effect=AssertionError("Status read sent to provider")):
                report = self.client.get("/admin/leads/delivery-summary", headers=self.headers["admin"])
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)
        self.assertEqual(report.status_code, 200, report.text)
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements[0].lstrip().startswith("WITH counts AS"))
        snapshot = report.json()
        self.assertEqual(snapshot["total_jobs"], 12)
        self.assertEqual(snapshot["tracked_leads"], 2)
        self.assertEqual(sum(snapshot["status_counts"].values()), snapshot["total_jobs"])
        self.assertEqual(sum(row["count"] for row in snapshot["by_step"]), snapshot["total_jobs"])
        for status in DELIVERY_STATUSES:
            self.assertEqual(sum(row["count"] for row in snapshot["by_step"] if row["status"] == status), snapshot["status_counts"][status])
        self.assertIsNotNone(datetime.fromisoformat(snapshot["generated_at"]))
        result = self.client.get(f"/admin/lead-deliveries?lead_id={lead_id}&status=pending&limit=2&offset=1", headers=self.headers["admin"])
        self.assertEqual(result.status_code, 200, result.text)
        queue = result.json()
        self.assertEqual((queue["total"], queue["limit"], queue["offset"], len(queue["items"])), (6, 2, 1, 2))
        self.assertTrue(all(row["lead_id"] == str(lead_id) and row["status"] == "pending" for row in queue["items"]))
        self.assertNotIn(str(other_id), result.text)
        self.assertTrue(all("payload" not in row and "provider_id" not in row for row in queue["items"]))

    def isolate_step(self, lead_id, step):
        with self.factory() as db:
            db.query(LeadDeliveryJob).filter(LeadDeliveryJob.lead_id == lead_id,
                LeadDeliveryJob.step != step).update({"status": "skipped"})
            row = db.query(LeadDeliveryJob).filter_by(lead_id=lead_id, step=step).one()
            job_id = row.id
            db.commit()
        return job_id

    def test_both_providers_hold_each_known_connection_failure_across_due_polls(self):
        for step in ("crm_contact", "email_1"):
            for error_class in (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
                with self.subTest(step=step, error=error_class.__name__):
                    lead_id = self.submit()
                    job_id = self.isolate_step(lead_id, step)
                    def handler(request):
                        raise error_class("Synthetic known pre-send failure", request=request)
                    with self.transport(handler) as client:
                        self.assertEqual(service.run_batch(self.factory, client=client)["processed"], 1)
                        self.due_now(job_id)
                        for _ in range(4):
                            self.assertEqual(service.run_batch(self.factory, client=client)["processed"], 0)
                        self.assertEqual(len(self.calls), 1)
                    row = self.jobs(lead_id)[step]
                    self.assertEqual((row.status, row.attempt_count, row.safe_to_retry, row.outcome_uncertain),
                                     ("failed", 1, True, False))

    def test_uncertain_provider_failures_cannot_be_worker_or_admin_repeated(self):
        cases = (("crm_contact", "read_timeout"), ("crm_contact", "http_503"),
                 ("email_1", "read_timeout"), ("email_1", "http_503"),
                 ("email_1", "concurrent_key"))
        for step, failure in cases:
            with self.subTest(step=step, failure=failure):
                lead_id = self.submit()
                job_id = self.isolate_step(lead_id, step)
                def handler(request):
                    if failure == "read_timeout":
                        raise httpx.ReadTimeout("Synthetic uncertain write", request=request)
                    if failure == "concurrent_key":
                        return httpx.Response(409, json={"name": "concurrent_idempotent_requests"})
                    return httpx.Response(503, json={})
                with self.transport(handler) as client:
                    service.run_batch(self.factory, client=client)
                    self.due_now(job_id)
                    for _ in range(4):
                        service.run_batch(self.factory, client=client)
                    self.assertEqual(len(self.calls), 1)
                row = self.jobs(lead_id)[step]
                self.assertEqual((row.status, row.outcome_uncertain, row.safe_to_retry), ("needs_review", True, False))
                self.assertEqual(self.client.post(f"/admin/lead-deliveries/{job_id}/retry", json={}, headers=self.headers["admin"]).status_code, 409)

    def test_rate_limit_unsupported_or_oversized_delay_is_held_for_review(self):
        for step in ("crm_contact", "email_1"):
            for header in (None, "Thu, 01 Jan 2099 00:00:00 GMT", "86401", "-1", "1.5", "999999999999"):
                with self.subTest(step=step, header=header):
                    lead_id = self.submit()
                    job_id = self.isolate_step(lead_id, step)
                    def handler(_request):
                        return httpx.Response(429, headers={} if header is None else {"Retry-After": header}, json={})
                    with self.transport(handler) as client:
                        service.run_batch(self.factory, client=client)
                        self.due_now(job_id)
                        for _ in range(2):
                            service.run_batch(self.factory, client=client)
                        self.assertEqual(len(self.calls), 1)
                    row = self.jobs(lead_id)[step]
                    self.assertEqual((row.status, row.safe_to_retry), ("needs_review", False))
                    self.assertEqual(row.error_code, f"{row.provider}_retry_after_unverified")
                    self.assertEqual(self.client.post(f"/admin/lead-deliveries/{job_id}/retry", json={}, headers=self.headers["admin"]).status_code, 409)

    def test_rate_limit_manual_approval_never_shortens_recorded_cooldown(self):
        lead_id = self.submit()
        job_id = self.isolate_step(lead_id, "email_1")
        with self.transport(lambda _: httpx.Response(429, headers={"Retry-After": "86400"}, json={})) as client:
            service.run_batch(self.factory, client=client)
        row = self.jobs(lead_id)["email_1"]
        self.assertEqual((row.next_attempt_at-row.last_attempt_at).total_seconds(), 86400)
        self.assertEqual(self.client.post(f"/admin/lead-deliveries/{job_id}/retry", json={}, headers=self.headers["admin"]).status_code, 409)
        self.due_now(job_id)
        before = self.jobs(lead_id)["email_1"]
        self.assertEqual(self.client.post(f"/admin/lead-deliveries/{job_id}/retry", json={}, headers=self.headers["admin"]).status_code, 200)
        after = self.jobs(lead_id)["email_1"]
        self.assertEqual((after.next_attempt_at, after.first_attempt_at, after.last_attempt_at, after.payload, after.payload_sha256),
                         (before.next_attempt_at, before.first_attempt_at, before.last_attempt_at, before.payload, before.payload_sha256))

    def test_one_concurrent_admin_approval_and_worker_claim_means_one_exact_request(self):
        lead_id = self.submit()
        job_id = self.isolate_step(lead_id, "email_1")
        with self.transport(lambda _: httpx.Response(401, json={})) as client:
            service.run_batch(self.factory, client=client)
            original = self.calls[0]
        self.due_now(job_id)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: self.client.post(f"/admin/lead-deliveries/{job_id}/retry",
                json={}, headers=self.headers["admin"]), range(2)))
        self.assertCountEqual([result.status_code for result in responses], [200, 409])
        with self.transport() as client:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                results = list(executor.map(lambda _: service.run_batch(self.factory, client=client), range(3)))
            self.assertEqual(sum(result["processed"] for result in results), 1)
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(self.calls[0].headers["Idempotency-Key"], original.headers["Idempotency-Key"])
            self.assertEqual(self.calls[0].content, original.content)
        row = self.jobs(lead_id)["email_1"]
        self.assertEqual((row.status, row.attempt_count), ("accepted", 2))

    def test_every_further_failure_requires_a_separate_manual_approval(self):
        lead_id = self.submit()
        job_id = self.isolate_step(lead_id, "email_1")
        with self.transport(lambda _: httpx.Response(401, json={})) as client:
            service.run_batch(self.factory, client=client)
            for permitted_attempt in (2, 3):
                self.due_now(job_id)
                for _ in range(4):
                    self.assertEqual(service.run_batch(self.factory, client=client)["processed"], 0)
                self.assertEqual(len(self.calls), permitted_attempt-1)
                response = self.client.post(f"/admin/lead-deliveries/{job_id}/retry", json={}, headers=self.headers["admin"])
                self.assertEqual(response.status_code, 200, response.text)
                service.run_batch(self.factory, client=client)
                self.assertEqual(len(self.calls), permitted_attempt)
                row = self.jobs(lead_id)["email_1"]
                self.assertEqual((row.status, row.attempt_count), ("failed", permitted_attempt))

    def test_legacy_safe_retry_wait_is_held_until_explicit_due_admin_action(self):
        lead_id = self.submit()
        job_id = self.isolate_step(lead_id, "email_1")
        previous = service.utcnow()-timedelta(hours=1)
        with self.factory() as db:
            row = db.get(LeadDeliveryJob, job_id)
            row.status = "retry_wait"; row.attempt_count = 1; row.safe_to_retry = True
            row.first_attempt_at = previous; row.last_attempt_at = previous
            row.next_attempt_at = previous; row.error_code = "resend_connection_not_established"
            db.commit()
        with self.transport() as client:
            for _ in range(4):
                self.assertEqual(service.run_batch(self.factory, client=client)["processed"], 0)
            self.assertEqual(self.calls, [])
            detail = self.client.get(f"/admin/leads/{lead_id}/delivery", headers=self.headers["admin"]).json()
            self.assertTrue(next(item for item in detail["jobs"] if item["id"] == str(job_id))["can_retry"])
            response = self.client.post(f"/admin/lead-deliveries/{job_id}/retry", json={}, headers=self.headers["admin"])
            self.assertEqual(response.status_code, 200, response.text)
            service.run_batch(self.factory, client=client)
            self.assertEqual(len(self.calls), 1)

    def test_unexplained_or_malformed_pending_retry_refuses_worker_dispatch(self):
        for code in (None, "resend_connection_not_established", service.MANUAL_RETRY_PREFIX + "x"*121):
            with self.subTest(code=code):
                lead_id = self.submit()
                job_id = self.isolate_step(lead_id, "email_1")
                with self.factory() as db:
                    row = db.get(LeadDeliveryJob, job_id)
                    row.attempt_count = 1; row.safe_to_retry = True
                    row.error_code = code; row.next_attempt_at = service.utcnow()-timedelta(seconds=1)
                    db.commit()
                with self.transport() as client:
                    result = service.run_batch(self.factory, client=client)
                    self.assertEqual(result["error_code"], "pending_retry_authorization_unknown")
                    self.assertEqual(result["processed"], 0)
                    self.assertEqual(self.calls, [])
                with self.factory() as db:
                    self.assertEqual(db.get(LeadDeliveryWorkerState, 1).error_code, "pending_retry_authorization_unknown")
                    row = db.get(LeadDeliveryJob, job_id)
                    self.assertEqual((row.status, row.attempt_count), ("pending", 1))
                    row.status = "needs_review"; db.commit()

    def test_processing_claim_rejects_uncertainty_or_fabricated_prior_permit(self):
        for uncertain in (True, False):
            with self.subTest(uncertain=uncertain):
                lead_id = self.submit()
                job_id = self.isolate_step(lead_id, "email_1")
                with self.factory() as db:
                    claim = service.claim_next_job(db)
                    self.assertEqual(claim.job_id, job_id)
                    row = db.get(LeadDeliveryJob, job_id)
                    row.outcome_uncertain = uncertain
                    if not uncertain:
                        row.attempt_count = 2
                        claim = replace(claim, prior_attempt_count=1, prior_safe_to_retry=True,
                            prior_error_code=service.MANUAL_RETRY_PREFIX + "resend_http_401")
                    db.commit()
                    with self.transport() as client:
                        service.process_claimed_job(db, claim, client=client)
                        self.assertEqual(self.calls, [])
                row = self.jobs(lead_id)["email_1"]
                self.assertEqual(row.status, "needs_review")
                self.assertFalse(row.safe_to_retry)

    def test_changed_payload_or_recipient_configuration_does_not_send(self):
        lead_id = self.submit()
        with self.factory() as db:
            row = db.query(LeadDeliveryJob).filter_by(lead_id=lead_id, step="email_1").one()
            row.payload = {**row.payload, "body": {**row.payload["body"], "subject": "Synthetic alteration"}}
            db.get(SiteSettings, 1).data = {"notify_email": "new@example.com"}
            db.commit()
        with self.transport() as client:
            service.run_batch(self.factory, client=client)
        rows = self.jobs(lead_id)
        self.assertEqual(rows["email_1"].error_code, "delivery_payload_changed")
        self.assertEqual(rows["email_2"].error_code, "email_destination_configuration_changed")
        self.assertFalse(any(r.url.host == "api.resend.com" for r in self.calls))

    def test_changed_crm_destination_requires_review_instead_of_sending_to_new_account(self):
        lead_id = self.submit()
        with patch.object(settings, "ghl_location_id", "different-location"), self.transport() as client:
            service.run_batch(self.factory, client=client)
        self.assertEqual(self.jobs(lead_id)["crm_contact"].error_code, "crm_destination_configuration_changed")
        self.assertFalse(any(r.url.host == "services.leadconnectorhq.com" for r in self.calls))

    def test_failure_after_provider_acceptance_does_not_make_job_replayable(self):
        lead_id = self.submit()
        original = service.finish_claim
        failed = False
        def failure(db, claim, outcome, now):
            nonlocal failed
            if outcome.status == "accepted" and not failed:
                failed = True
                raise RuntimeError("Synthetic persistence interruption after provider acceptance")
            return original(db, claim, outcome, now)
        with self.transport() as client, patch.object(service, "finish_claim", side_effect=failure):
            service.run_batch(self.factory, client=client)
            calls_before = len(self.calls)
            service.run_batch(self.factory, client=client)
        self.assertEqual(len(self.calls), calls_before)
        unknown = [row for row in self.jobs(lead_id).values() if row.status == "needs_review"]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0].attempt_count, 1)
        self.assertTrue(unknown[0].outcome_uncertain)

    def test_empty_note_is_skipped_without_blocking_complete_crm_acknowledgement(self):
        lead_id = self.submit(body(message=None))
        with self.transport() as client:
            service.run_batch(self.factory, client=client)
        self.assertEqual(self.jobs(lead_id)["crm_note"].status, "skipped")
        self.assertFalse(any(r.url.path.endswith("/notes") for r in self.calls))
        with self.factory() as db:
            self.assertIsNotNone(db.get(Lead, lead_id).ghl_synced_at)

    def test_email_without_confirmed_provider_identifier_needs_review_and_is_not_replayed(self):
        lead_id = self.submit()
        def handler(request):
            if request.url.host == "api.resend.com":
                return httpx.Response(200, json={"synthetic": "unconfirmed acknowledgement"})
        with self.transport(handler) as client:
            service.run_batch(self.factory, client=client)
            calls = len(self.calls)
            service.run_batch(self.factory, client=client)
        self.assertEqual(len(self.calls), calls)
        for key in ("email_1", "email_2"):
            row = self.jobs(lead_id)[key]
            self.assertEqual(row.status, "needs_review")
            self.assertEqual(row.error_code, "email_acceptance_unknown")
            self.assertTrue(row.outcome_uncertain)
            self.assertFalse(row.safe_to_retry)
            self.assertIsNone(row.accepted_at)

    def test_missing_provider_configuration_is_recorded_without_dev_email_printing(self):
        lead_id = self.submit()
        with patch.object(settings, "resend_api_key", ""), patch.object(settings, "ghl_api_key", ""), self.transport() as client, patch("builtins.print") as output:
            service.run_batch(self.factory, client=client)
        self.assertEqual(self.calls, [])
        output.assert_not_called()
        rows = self.jobs(lead_id)
        self.assertEqual(rows["email_1"].error_code, "email_provider_not_configured")
        self.assertEqual(rows["crm_contact"].error_code, "crm_provider_not_configured")

    def test_capture_preserves_email_only_legacy_routing(self):
        response = self.client.post("/leads/capture", json=body(type="rental_interest"))
        self.assertEqual(response.status_code, 201, response.text)
        rows = self.jobs(UUID(response.json()["id"]))
        self.assertEqual(set(rows), {"email_1", "email_2"})

    def test_booking_lead_is_queued_without_duplicate_legacy_alerts(self):
        payload = {"listing_id": str(self.listing_id), "name": "Synthetic guest", "email": "guest@example.com",
                   "check_in": str(date.today()+timedelta(days=4)), "check_out": str(date.today()+timedelta(days=6)), "guests": 1}
        with patch.object(bookings, "send_booking_submitted_email") as confirmation, patch.object(bookings, "send_owner_new_booking_email"), patch.object(bookings.ghl, "create_contact") as crm, patch.object(bookings, "send_lead_notification") as alert:
            response = self.client.post("/bookings", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        confirmation.assert_called_once()
        crm.assert_not_called(); alert.assert_not_called()
        with self.factory() as db:
            lead = db.query(Lead).filter_by(type="booking").one()
            self.assertEqual(db.query(LeadDeliveryJob).filter_by(lead_id=lead.id).count(), 6)

    def test_authenticated_booking_link_lead_and_outbox_commit_once(self):
        payload = {"listing_id": str(self.listing_id), "check_in": str(date.today()+timedelta(days=4)),
                   "check_out": str(date.today()+timedelta(days=6)), "guests": 1}
        commits = []
        def committed(_connection):
            commits.append(True)
        event.listen(self.engine, "commit", committed)
        try:
            with patch.object(bookings, "send_booking_submitted_email") as guest, patch.object(bookings, "send_owner_new_booking_email") as owner, patch.object(bookings.ghl, "create_contact") as crm, patch.object(bookings, "send_lead_notification") as alert, patch.object(bookings.paypal, "authorize_order") as paypal:
                result = self.client.post("/bookings", json=payload, headers=self.headers["buyer"])
        finally:
            event.remove(self.engine, "commit", committed)
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(len(commits), 1)
        guest.assert_called_once()
        self.assertEqual(guest.call_args.kwargs["to_email"], "synthetic-outbox-buyer@example.com")
        # Established routing notifies both the owner and a distinct submitter.
        # This fixture has owner + Admin submitter, each exactly once.
        self.assertCountEqual([call.kwargs["to_email"] for call in owner.call_args_list],
                              ["synthetic-outbox-owner@example.com", "synthetic-outbox-admin@example.com"])
        crm.assert_not_called(); alert.assert_not_called(); paypal.assert_not_called()
        with self.factory() as db:
            booking = db.query(Booking).one(); lead = db.query(Lead).one()
            self.assertEqual(booking.buyer_id, self.ids["buyer"])
            self.assertEqual(booking.lead_id, lead.id)
            self.assertEqual(db.query(LeadDeliveryJob).filter_by(lead_id=lead.id).count(), 6)

    def test_booking_failure_at_final_commit_rolls_back_link_lead_and_outbox(self):
        payload = {"listing_id": str(self.listing_id), "check_in": str(date.today()+timedelta(days=4)),
                   "check_out": str(date.today()+timedelta(days=6)), "guests": 1}
        observed_links = []
        def fail_commit(db):
            if db.bind is not self.engine:
                return
            db.flush()
            booking = db.query(Booking).one()
            self.assertIsNotNone(booking.lead_id)
            self.assertEqual(db.query(LeadDeliveryJob).filter_by(lead_id=booking.lead_id).count(), 6)
            observed_links.append(booking.lead_id)
            raise SQLAlchemyError("synthetic-private-outbox-parameters")
        event.listen(Session, "before_commit", fail_commit)
        try:
            with patch.object(bookings, "send_booking_submitted_email") as guest, patch.object(bookings, "send_owner_new_booking_email") as owner, patch.object(bookings.ghl, "create_contact") as crm, patch.object(bookings, "send_lead_notification") as alert:
                result = self.client.post("/bookings", json=payload, headers=self.headers["buyer"])
        finally:
            event.remove(Session, "before_commit", fail_commit)
        self.assertEqual(result.status_code, 503, result.text)
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertNotIn("synthetic-private-outbox-parameters", result.text)
        self.assertEqual(len(observed_links), 1)
        guest.assert_not_called(); owner.assert_not_called(); crm.assert_not_called(); alert.assert_not_called()
        with self.factory() as db:
            self.assertEqual(db.query(Booking).count(), 0)
            self.assertEqual(db.query(Lead).count(), 0)
            self.assertEqual(db.query(LeadDeliveryJob).count(), 0)

    def test_lead_outbox_database_failure_is_sanitized_and_rolls_back(self):
        original = service.enqueue_lead_delivery
        def fail_enqueue(db, lead, *args, **kwargs):
            original(db, lead, *args, **kwargs); db.flush()
            raise IntegrityError("INSERT synthetic-private-outbox", {"payload": "synthetic-private-contact"}, Exception("synthetic"))
        with patch.object(service, "enqueue_lead_delivery", side_effect=fail_enqueue), patch.object(leads.ghl, "create_contact") as crm, patch.object(leads, "send_lead_notification") as alert:
            result = self.client.post("/leads", json=body())
        self.assertEqual(result.status_code, 503, result.text)
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertNotIn("synthetic-private", result.text)
        crm.assert_not_called(); alert.assert_not_called()
        with self.factory() as db:
            self.assertEqual(db.query(Lead).count(), 0)
            self.assertEqual(db.query(LeadDeliveryJob).count(), 0)

    def test_booking_enqueue_failure_rolls_back_without_guest_or_owner_messages(self):
        payload = {"listing_id": str(self.listing_id), "name": "Synthetic guest", "email": "guest@example.com",
                   "check_in": str(date.today()+timedelta(days=4)), "check_out": str(date.today()+timedelta(days=6)), "guests": 1}
        def broken(db, lead, *args, **kwargs):
            original_enqueue(db, lead, *args, **kwargs); db.flush()
            raise RuntimeError("Synthetic rollback")
        original_enqueue = service.enqueue_lead_delivery
        with patch.object(service, "enqueue_lead_delivery", side_effect=broken), patch.object(bookings, "send_booking_submitted_email") as guest, patch.object(bookings, "send_owner_new_booking_email") as owner:
            with self.assertRaises(RuntimeError):
                self.client.post("/bookings", json=payload)
        guest.assert_not_called(); owner.assert_not_called()
        with self.factory() as db:
            self.assertEqual(db.query(Lead).count(), 0)
            self.assertEqual(db.query(LeadDeliveryJob).count(), 0)

    def test_booking_flag_off_keeps_existing_single_alert_path(self):
        payload = {"listing_id": str(self.listing_id), "name": "Synthetic guest", "email": "guest@example.com",
                   "check_in": str(date.today()+timedelta(days=4)), "check_out": str(date.today()+timedelta(days=6)), "guests": 1}
        with patch.object(service, "enabled", return_value=False), patch.object(bookings, "send_booking_submitted_email"), patch.object(bookings, "send_owner_new_booking_email"), patch.object(bookings.ghl, "create_contact") as crm, patch.object(bookings, "send_lead_notification") as alert:
            response = self.client.post("/bookings", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        crm.assert_called_once(); alert.assert_called_once()
        with self.factory() as db:
            self.assertEqual(db.query(LeadDeliveryJob).count(), 0)

    def test_migration_is_additive_repeatable_private_and_does_not_enqueue_history(self):
        path = Path(__file__).parents[1]/"alembic/versions/82e1a7c4d9b0_private_lead_delivery_outbox.py"
        spec = importlib.util.spec_from_file_location("synthetic_outbox_migration", path)
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        self.assertEqual(migration.down_revision, "52b2edc810ad")
        with self.factory() as db:
            db.add(Lead(type="buyer_interest", name="Synthetic historical", phone="+18095551234", status="new")); db.commit()
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade(); migration.upgrade(); migration.downgrade()
            private = connection.execute(text("SELECT relname,relrowsecurity FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relname IN ('lead_delivery_jobs','lead_delivery_worker_state')")).all()
            self.assertEqual(len(private), 2)
            self.assertTrue(all(row.relrowsecurity for row in private))
            grants = connection.scalar(text("SELECT count(*) FROM pg_class c CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a WHERE c.relnamespace=current_schema()::regnamespace AND c.relname IN ('lead_delivery_jobs','lead_delivery_worker_state') AND a.grantee=0"))
            self.assertEqual(grants, 0)
        with self.factory() as db:
            self.assertEqual(db.query(Lead).count(), 1)
            self.assertEqual(db.query(LeadDeliveryJob).count(), 0)
            self.assertEqual(db.query(LeadDeliveryWorkerState).count(), 0)
            new = Lead(type="buyer_interest", name="Synthetic new", phone="+18095551234", status="new")
            db.add(new); db.flush()
            service.enqueue_lead_delivery(db, new)
            db.commit()
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
        with self.factory() as db:
            self.assertEqual(db.query(Lead).count(), 2)
            self.assertEqual(db.query(LeadDeliveryJob).count(), 6)


if __name__ == "__main__":
    unittest.main()
