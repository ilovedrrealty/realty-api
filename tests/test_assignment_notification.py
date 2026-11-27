"""Owned PostgreSQL source tests. No real providers, main or lifespan import.

This class makes an isolated model schema and installs the new private guards;
it does not substitute for the separate genuine106->108 migration census.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import smtplib
import threading
import unittest
from unittest.mock import patch
from urllib import request as urllib_request
from uuid import UUID, uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-assignment-tests-only",
    GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
    SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")

import httpx
import jwt
import requests
import resend
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event as sql_event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from config import settings
from database import Base, get_db
import models
import models.trade_contact  # The two legacy tables are not in models.__init__.
from models.assignment_notification import AssignmentEvent as Event, AssignmentEmailJob as Job
from models.lead import Lead
from models.lead_delivery import LeadDeliveryJob
from models.listing import Listing
from models.seller_valuation import SellerValuationIntake
from models.user import User
from routes import admin, leads, assignment_notification as history_routes
from services import assignment_notification as service
from utils import assignment_messages, email


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires fixed owned loopback PostgreSQL")
class AssignmentNotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host != "127.0.0.1" or url.port != 55449 or url.database != "ildr_platform_test_20261025" or url.get_backend_name() != "postgresql":
            raise ValueError("Use only the fixed disposable loopback database")
        cls.schema = "assignment_notice_test_" + uuid4().hex
        cls.bootstrap = create_engine(url)
        with cls.bootstrap.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.addClassCleanup(cls.cleanup_schema)
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema, "connect_timeout": 5},
                                  pool_size=12, pool_timeout=5)
        cls.Session = sessionmaker(bind=cls.engine)
        if len(Base.metadata.tables) != 107:
            raise AssertionError("Expected current107 modeled tables")
        Base.metadata.create_all(cls.engine)
        path = Path(__file__).resolve().parents[1] / "alembic/versions/f7a9c3e2d114_private_assignment_notifications.py"
        spec = importlib.util.spec_from_file_location("owned_assignment_migration", path)
        cls.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.migration)
        with cls.engine.begin() as db:
            cls.migration.op = Operations(MigrationContext.configure(db))
            cls.migration.upgrade()
        cls.app = FastAPI()
        for router in (admin.router, leads.router, history_routes.router):
            cls.app.include_router(router)
        def local_db():
            with cls.Session() as db:
                yield db
        cls.app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(cls.app, raise_server_exceptions=False)

    @classmethod
    def cleanup_schema(cls):
        if hasattr(cls, "client"):
            cls.client.close()
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        assert re.fullmatch(r"assignment_notice_test_[a-f0-9]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as db:
                db.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        finally:
            cls.bootstrap.dispose()

    def setUp(self):
        with self.engine.begin() as db:
            names = ",".join(f'"{self.schema}"."{table.name}"' for table in Base.metadata.sorted_tables)
            db.execute(text("TRUNCATE " + names + " RESTART IDENTITY CASCADE"))
        self.provider_attempts = []
        def refuse(*_args, **_kwargs):
            self.provider_attempts.append(1)
            raise AssertionError("Real provider transport is forbidden")
        for patcher in (
            patch.object(httpx.HTTPTransport, "handle_request", refuse),
            patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse),
            patch.object(requests.Session, "send", refuse),
            patch.object(urllib_request, "urlopen", refuse),
            patch.object(resend.Emails, "send", refuse),
            patch.object(smtplib.SMTP, "connect", refuse),
            patch.object(smtplib.SMTP_SSL, "connect", refuse),
            patch.object(email, "_send", refuse),
            patch.object(leads.ghl, "update_contact_status"),
            patch.object(settings, "lead_delivery_enabled", True),
            patch.object(settings, "resend_api_key", "synthetic-assignment-not-a-key"),
            patch.object(settings, "email_from", "no-reply@example.com"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(lambda: self.assertEqual(self.provider_attempts, []))
        self.ids, self.headers = {}, {}
        with self.Session.begin() as db:
            for number, (name, role) in enumerate((("admin", "admin"), ("agent", "realtor"),
                ("second", "admin"), ("buyer", "buyer"), ("owner", "owner"), ("outsider", "buyer")), 1):
                identity = UUID(int=900000 + number)
                db.add(User(id=identity, user_code=10000 + number, email=name + "@example.com",
                    display_name="Synthetic " + name, role=role, status="active", email_verified=True))
                self.ids[name] = identity
                self.headers[name] = {"Authorization": "Bearer " + jwt.encode(
                    {"sub": str(identity), "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                    settings.jwt_secret, algorithm="HS256")}
            db.flush()
            listing = Listing(title="Synthetic assignment listing", location="Synthetic area", type="villa",
                transaction="sale", currency="USD", price=1000, images=[], features=[], status="active",
                submitted_by=self.ids["owner"])
            db.add(listing)
            db.flush()
            self.listing_id = listing.id

    def lead(self, *, kind="buyer_interest", submitter="buyer", valuation=False, phone_only=False):
        with self.Session.begin() as db:
            row = Lead(type=kind, name="PRIVATE_LEAD_NAME", email=None if phone_only else "lead-private@example.com",
                phone="+18095551234", message="PRIVATE_BODY_SENTINEL", property_id=self.listing_id,
                from_user_id=self.ids[submitter] if submitter else None, status="new")
            db.add(row)
            db.flush()
            if valuation:
                db.add(SellerValuationIntake(lead_id=row.id, subject={"property_type": "land", "location": "Synthetic area"}))
            return row.id

    def assign(self, target, *, kind="lead", agent="agent", request_id=None, expected=204, actor="admin", client=None):
        path = f"/admin/leads/{target}/assign" if kind == "lead" else f"/admin/users/{target}/assign-realtor"
        body = {"realtor_id": str(self.ids[agent]) if agent else None}
        if request_id is not None:
            body["request_id"] = str(request_id)
        response = (client or self.client).put(path, json=body, headers=self.headers[actor])
        self.assertEqual(response.status_code, expected, response.text)
        return response

    def jobs(self):
        with self.Session() as db:
            return db.query(Job).order_by(Job.created_at, Job.id).all()

    def mock_transport(self, handler=None):
        self.calls = []
        def handle(request):
            self.calls.append(request)
            return handler(request) if handler else httpx.Response(200, json={"id": "owned-provider-id"})
        return httpx.MockTransport(handle)

    def test_account_assign_and_clear_record_events_without_any_email_or_role_change(self):
        target = self.ids["buyer"]
        self.assign(target, kind="account", agent="second")
        self.assign(target, kind="account", agent=None)
        with self.Session() as db:
            user = db.get(User, target)
            self.assertEqual(user.role, "buyer")
            self.assertIsNone(user.assigned_realtor_id)
            self.assertEqual({row.action for row in db.query(Event)}, {"assigned", "cleared"})
            self.assertEqual(db.query(Job).count(), 0)
            self.assertEqual(db.query(LeadDeliveryJob).count(), 0)
        report = self.client.get(f"/admin/users/{target}/assignment-history", headers=self.headers["admin"]).json()
        self.assertTrue(report["tracked"])
        self.assertEqual([item["notices"] for item in report["items"]], [[], []])

    def test_exact_seller_upgrade_and_structured_valuation_notice_policy(self):
        for valuation in (True, False):
            identity = self.lead(kind="seller_interest", valuation=valuation)
            self.assign(identity)
            with self.Session() as db:
                source = db.query(Event).filter(Event.lead_id == identity).one()
                kinds = {row.kind for row in db.query(Job).filter(Job.event_id == source.id)}
                self.assertEqual(kinds, {"lead_assigned_agent"} if valuation else set(service.NOTICE_KINDS))
                user = db.get(User, self.ids["buyer"])
                self.assertEqual(user.role, "buyer" if valuation else "owner")
                self.assertEqual(user.assigned_realtor_id, None if valuation else self.ids["agent"])
        self.assertTrue(all(row.status == "pending" and row.attempt_count == 0 for row in self.jobs()))

    def test_optional_invalid_address_and_render_failure_do_not_lose_assignment(self):
        for bad in ("", "invalid-address"):
            with self.Session.begin() as db:
                db.get(User, self.ids["agent"]).email = bad
            identity = self.lead()
            self.assign(identity)
            with self.Session() as db:
                self.assertEqual(db.get(Lead, identity).assigned_realtor_id, self.ids["agent"])
                event = db.query(Event).filter(Event.lead_id == identity).one()
                job = db.query(Job).filter(Job.event_id == event.id).one()
                self.assertEqual((job.status, job.attempt_count, job.payload), ("failed", 0, {}))
        with self.Session.begin() as db:
            db.get(User, self.ids["agent"]).email = "agent@example.com"
        identity = self.lead()
        with patch.object(service, "build_realtor_lead_assigned_notification", side_effect=ValueError("PRIVATE_RENDER_FAILURE")):
            self.assign(identity)
        with self.Session() as db:
            self.assertEqual(db.get(Lead, identity).assigned_realtor_id, self.ids["agent"])
            self.assertEqual(db.query(Job).join(Event).filter(Event.lead_id == identity).one().status, "failed")

    def test_request_replay_after_later_reassignment_never_reapplies_old_agent(self):
        identity, command = self.lead(), uuid4()
        self.assign(identity, request_id=command)
        self.assign(identity, agent="second", request_id=uuid4())
        self.assign(identity, request_id=command)
        self.assign(identity, agent="second", request_id=command, expected=409)
        self.assign(self.ids["owner"], kind="account", request_id=command, expected=409)
        with self.Session() as db:
            self.assertEqual(db.get(Lead, identity).assigned_realtor_id, self.ids["second"])
            self.assertEqual(db.query(Event).count(), 2)
            self.assertEqual(db.query(Job).count(), 2)
            self.assertIsNone(db.get(User, self.ids["owner"]).assigned_realtor_id)

    def test_fresh_same_agent_legacy_and_explicit_actions_remain_distinct(self):
        identity = self.lead()
        self.assign(identity)
        self.assign(identity)
        self.assign(identity, request_id=uuid4())
        with self.Session() as db:
            self.assertEqual(db.query(Event).count(), 3)
            self.assertEqual(db.query(Job).count(), 3)
            self.assertIsNotNone(db.get(Lead, identity).assigned_at)

    def test_assignment_event_and_jobs_rollback_together_on_database_failure(self):
        identity = self.lead(kind="seller_interest")
        def reject(_connection, _cursor, statement, *_args):
            if statement.lower().startswith("insert into assignment_email_jobs"):
                raise RuntimeError("owned insertion failure")
        sql_event.listen(self.engine, "before_cursor_execute", reject)
        try:
            self.assign(identity, expected=500)
        finally:
            sql_event.remove(self.engine, "before_cursor_execute", reject)
        with self.Session() as db:
            self.assertEqual(db.query(Event).count(), 0)
            self.assertEqual(db.query(Job).count(), 0)
            self.assertIsNone(db.get(Lead, identity).assigned_realtor_id)
            self.assertEqual(db.get(User, self.ids["buyer"]).role, "buyer")

    def test_disabled_worker_keeps_real_queued_jobs_and_does_no_database_or_provider_work(self):
        self.assign(self.lead())
        with patch.object(settings, "lead_delivery_enabled", False), patch.object(self, "Session") as factory:
            self.assertEqual(service.run_batch(factory), {"enabled": False, "processed": 0})
            factory.assert_not_called()
        self.assertEqual(self.jobs()[0].status, "pending")

    def test_valid_provider_acceptance_once_with_event_specific_idempotency_and_private_snapshot(self):
        self.assign(self.lead(phone_only=True))
        with self.mock_transport() as client:
            self.assertEqual(service.run_batch(self.Session, transport=client)["processed"], 1)
            self.assertEqual(service.run_batch(self.Session, transport=client)["processed"], 0)
        self.assertEqual(len(self.calls), 1)
        job = self.jobs()[0]
        self.assertEqual((job.status, job.attempt_count, job.provider_id), ("accepted", 1, "owned-provider-id"))
        self.assertIsNotNone(job.accepted_at)
        self.assertEqual(self.calls[0].headers["Idempotency-Key"], f"assignment-event/{job.event_id}/{job.kind}/v1")
        self.assertEqual(json.loads(self.calls[0].content)["to"], ["agent@example.com"])
        self.assertIn("+18095551234", json.loads(self.calls[0].content)["html"])

    def test_provider_outcomes_are_truthful_and_never_automatically_retried(self):
        cases = [
            ("known400", lambda _r: httpx.Response(400), "failed", False),
            ("unknown500", lambda _r: httpx.Response(500), "needs_review", True),
            ("invalid200", lambda _r: httpx.Response(200, json={}), "needs_review", True),
            ("read_timeout", lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("PRIVATE", request=r)), "needs_review", True),
            ("connect_timeout", lambda r: (_ for _ in ()).throw(httpx.ConnectTimeout("PRIVATE", request=r)), "failed", False),
        ]
        for name, handler, status, uncertain in cases:
            with self.subTest(name=name):
                self.assign(self.lead())
                with self.mock_transport(handler) as client:
                    service.run_batch(self.Session, transport=client)
                    service.run_batch(self.Session, transport=client)
                self.assertEqual(len(self.calls), 1)
                job = self.jobs()[-1]
                self.assertEqual((job.status, job.outcome_uncertain, job.attempt_count), (status, uncertain, 1))

    def test_missing_provider_config_is_not_acceptance(self):
        self.assign(self.lead())
        with patch.object(settings, "resend_api_key", ""), self.mock_transport() as client:
            service.run_batch(self.Session, transport=client)
        self.assertEqual(self.calls, [])
        self.assertEqual((self.jobs()[0].status, self.jobs()[0].provider_id), ("failed", None))

    def test_superseded_and_changed_or_ineligible_destinations_never_receive_contacts(self):
        identity = self.lead()
        self.assign(identity)
        self.assign(identity, agent="second")
        with self.mock_transport() as client:
            service.run_batch(self.Session, max_jobs=1, transport=client)
        self.assertEqual(self.calls, [])
        with self.Session.begin() as db:
            db.get(User, self.ids["second"]).email = "changed@example.com"
        with self.mock_transport() as client:
            service.run_batch(self.Session, transport=client)
        self.assertEqual(self.calls, [])
        self.assertTrue(all(job.status == "skipped" for job in self.jobs()))
        for field, value in (("status", "suspended"), ("role", "buyer"), ("email_verified", False)):
            with self.Session.begin() as db:
                actor = db.get(User, self.ids["agent"])
                actor.status, actor.role, actor.email_verified = "active", "realtor", True
            self.assign(self.lead())
            with self.Session.begin() as db:
                setattr(db.get(User, self.ids["agent"]), field, value)
            with self.mock_transport() as client:
                service.run_batch(self.Session, transport=client)
            self.assertEqual(self.calls, [])
            self.assertEqual(self.jobs()[-1].status, "skipped")

    def test_assignment_cycles_never_revive_old_a_b_a_but_fresh_a_a_notices_remain(self):
        identity = self.lead()
        commands = [uuid4() for _n in range(4)]
        for agent, command in zip(("agent", "second", "agent", "agent"), commands):
            self.assign(identity, agent=agent, request_id=command)
        with self.Session() as db:
            events = db.query(Event).filter(Event.lead_id == identity).all()
            cycles = {row.request_id: row.cycle for row in events}
            self.assertEqual([cycles[command] for command in commands], [1, 2, 3, 3])
        with self.mock_transport() as transport:
            service.run_batch(self.Session, transport=transport)
        self.assertEqual(len(self.calls), 2)
        with self.Session() as db:
            outcomes = dict(db.query(Event.request_id, Job.status).join(Job, Job.event_id == Event.id).all())
            self.assertEqual([outcomes[command] for command in commands], ["skipped", "skipped", "accepted", "accepted"])

    def test_owner_promotion_confirmation_survives_later_agent_change_or_suspension(self):
        identity = self.lead(kind="seller_interest")
        self.assign(identity)
        self.assign(identity, agent="second")
        with self.Session.begin() as db:
            db.get(User, self.ids["agent"]).status = "suspended"
            owner = db.get(User, self.ids["buyer"])
            # These were not restrictions in the existing promotion-email path.
            owner.status, owner.email_verified = "suspended", False
        with self.mock_transport() as transport:
            service.run_batch(self.Session, transport=transport)
        targets = [json.loads(request.content)["to"] for request in self.calls]
        self.assertEqual(sorted(targets), sorted([["buyer@example.com"], ["second@example.com"]]))
        with self.Session() as db:
            customer = db.query(Job).filter(Job.kind == "owner_upgrade_customer").one()
            self.assertEqual(customer.status, "accepted")
            self.assertEqual(customer.recipient_user_id, self.ids["buyer"])

    def test_absolute_header_deadline_cancels_the_actual_async_exchange_without_resend(self):
        self.assign(self.lead())
        cancelled = threading.Event()
        async def slow_headers(_request):
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return httpx.Response(200, json={"id": "late-provider-id"})
        with patch.object(service, "ABSOLUTE_PROVIDER_SECONDS", 0.05), self.mock_transport(slow_headers) as transport:
            service.run_batch(self.Session, transport=transport)
            service.run_batch(self.Session, transport=transport)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual((self.jobs()[0].status, self.jobs()[0].outcome_uncertain), ("needs_review", True))

    def test_absolute_drip_body_deadline_cancels_inactivity_resetting_stream(self):
        self.assign(self.lead())
        cancelled, closed = threading.Event(), threading.Event()
        class Drip(httpx.AsyncByteStream):
            async def __aiter__(self):
                try:
                    for _n in range(100):
                        await asyncio.sleep(0.01)
                        yield b"x"
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            async def aclose(self):
                closed.set()
        def handler(_request):
            return httpx.Response(200, stream=Drip())
        with patch.object(service, "ABSOLUTE_PROVIDER_SECONDS", 0.05), self.mock_transport(handler) as transport:
            service.run_batch(self.Session, transport=transport)
        self.assertTrue(cancelled.is_set())
        self.assertTrue(closed.is_set())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.jobs()[0].status, "needs_review")

    def test_response_size_and_encoding_are_bounded_before_provider_json_projection(self):
        for encoding in (None, "gzip"):
            self.assign(self.lead())
            consumed = threading.Event()
            class Body(httpx.AsyncByteStream):
                async def __aiter__(self):
                    consumed.set()
                    yield b"x" * (service.MAX_RESPONSE_BYTES + 1)
            headers = {"content-encoding": encoding} if encoding else {}
            with self.mock_transport(lambda _r: httpx.Response(200, headers=headers, stream=Body())) as transport:
                service.run_batch(self.Session, transport=transport)
            self.assertEqual(consumed.is_set(), encoding is None)
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(self.jobs()[-1].status, "needs_review")

    def test_worker_database_statement_lock_and_idle_bounds_are_transaction_local(self):
        with self.Session() as db:
            service._limits(db)
            self.assertEqual(db.execute(text("SHOW statement_timeout")).scalar(), "5s")
            self.assertEqual(db.execute(text("SHOW lock_timeout")).scalar(), "3s")
            self.assertEqual(db.execute(text("SHOW idle_in_transaction_session_timeout")).scalar(), "30s")
            db.rollback()

    def test_expired_processing_is_unconfirmed_and_stale_token_cannot_accept_or_resend(self):
        self.assign(self.lead())
        now = service.utcnow()
        with self.Session() as db:
            claim = service.claim_next(db, now=now)
        with self.Session() as db:
            self.assertEqual(service.expire_claims(db, now + timedelta(seconds=121)), 1)
            self.assertFalse(service.finish(db, claim, service.Outcome("accepted", provider_id="stale")))
        with self.mock_transport() as client:
            self.assertEqual(service.run_batch(self.Session, transport=client)["processed"], 0)
        self.assertEqual(self.calls, [])
        self.assertEqual((self.jobs()[0].status, self.jobs()[0].outcome_uncertain), ("needs_review", True))

    def test_only_proven_unsent_shutdown_can_release_an_initial_claim(self):
        self.assign(self.lead())
        with self.Session() as db:
            claim = service.claim_next(db)
        stopped = threading.Event()
        stopped.set()
        with self.Session() as db, self.mock_transport() as client:
            self.assertTrue(service.process_claim(db, claim, transport=client, stop_event=stopped))
        self.assertEqual(self.calls, [])
        self.assertEqual((self.jobs()[0].status, self.jobs()[0].attempt_count), ("pending", 0))
        with self.mock_transport() as client:
            service.run_batch(self.Session, transport=client)
        self.assertEqual(len(self.calls), 1)

    def test_concurrent_exact_command_replay_creates_one_event_and_notice(self):
        identity, command = self.lead(), uuid4()
        barrier = threading.Barrier(2)
        def send():
            with TestClient(self.app, raise_server_exceptions=False) as client:
                barrier.wait(timeout=5)
                return self.assign(identity, request_id=command, client=client).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(lambda _value: send(), range(2))), [204, 204])
        with self.Session() as db:
            self.assertEqual((db.query(Event).count(), db.query(Job).count()), (1, 1))

    def test_concurrent_workers_claim_distinct_jobs_without_duplicate_provider_handoffs(self):
        self.assign(self.lead())
        self.assign(self.lead())
        barrier = threading.Barrier(2)
        calls, lock = [], threading.Lock()
        def deliver(request):
            with lock:
                calls.append(request.headers["Idempotency-Key"])
            barrier.wait(timeout=5)
            return httpx.Response(200, json={"id": "owned-provider-id"})
        with httpx.MockTransport(deliver) as client, ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _n: service.run_batch(self.Session, max_jobs=1, transport=client), range(2)))
        self.assertEqual(sum(row["processed"] for row in results), 2)
        self.assertEqual(len(set(calls)), 2)
        self.assertTrue(all(job.status == "accepted" for job in self.jobs()))

    def test_source_assignment_lock_remains_held_through_bounded_provider_handoff(self):
        identity = self.lead()
        self.assign(identity)
        entered, release, mutation_entered = threading.Event(), threading.Event(), threading.Event()
        original = service.prepare_request
        def prepare(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs["agent_id"] == str(self.ids["second"]):
                mutation_entered.set()
            return result
        def deliver(_request):
            entered.set()
            if not release.wait(5):
                raise AssertionError("Owned handoff barrier deadline")
            return httpx.Response(200, json={"id": "owned-provider-id"})
        with self.mock_transport(deliver) as client, patch.object(service, "prepare_request", prepare), ThreadPoolExecutor(max_workers=2) as pool:
            sending = pool.submit(service.run_batch, self.Session, max_jobs=1, transport=client)
            self.assertTrue(entered.wait(5))
            changing = pool.submit(self.assign, identity, agent="second")
            self.assertTrue(mutation_entered.wait(5))
            self.assertFalse(changing.done())
            with self.Session() as db:
                self.assertEqual(db.get(Lead, identity).assigned_realtor_id, self.ids["agent"])
            release.set()
            self.assertEqual(sending.result(timeout=10)["processed"], 1)
            self.assertEqual(changing.result(timeout=10).status_code, 204)
        self.assertEqual(len(self.calls), 1)
        with self.Session() as db:
            self.assertEqual(db.get(Lead, identity).assigned_realtor_id, self.ids["second"])

    def test_admin_history_paging_is_payload_free_private_and_current_role_bound(self):
        target = self.ids["owner"]
        stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch.object(service, "utcnow", return_value=stamp):
            for _n in range(12):
                self.assign(target, kind="account")
        identity = self.lead()
        self.assign(identity)
        with self.mock_transport() as client:
            service.run_batch(self.Session, transport=client)
        selects = []
        def no_private(_conn, _cursor, statement, *_args):
            if statement.lstrip().lower().startswith("select") and re.search(r"\bassignment_(?:events|email_jobs)\b", statement):
                self.assertIsNone(re.search(r"\b(payload|payload_sha256|recipient_user_id|provider_id|error_code|request_sha256)\b", statement))
                selects.append(statement)
        sql_event.listen(self.engine, "before_cursor_execute", no_private)
        try:
            path = f"/admin/users/{target}/assignment-history"
            first = self.client.get(path, headers=self.headers["admin"])
            second = self.client.get(path + "?offset=10", headers=self.headers["admin"])
            self.assertEqual((len(first.json()["items"]), len(second.json()["items"])), (10, 2))
            ids = [row["id"] for row in first.json()["items"] + second.json()["items"]]
            self.assertEqual(ids, sorted(ids, reverse=True))
            report = self.client.get(f"/admin/leads/{identity}/assignment-history", headers=self.headers["admin"])
            self.assertEqual(report.status_code, 200)
            self.assertEqual(report.headers["cache-control"], "no-store")
            for private in ("PRIVATE_BODY_SENTINEL", "lead-private@example.com", "agent@example.com", "owned-provider-id",
                            "recipient_user_id", "provider_id", "payload", str(self.ids["agent"])):
                self.assertNotIn(private, report.text)
            self.assertEqual(report.json()["items"][0]["notices"][0]["status"], "accepted")
            for actor in ("buyer", "owner", "agent"):
                denied = self.client.get(path, headers=self.headers[actor])
                self.assertEqual(denied.status_code, 403)
                self.assertEqual(denied.headers["cache-control"], "no-store")
            self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(self.client.get(f"/admin/users/{uuid4()}/assignment-history", headers=self.headers["admin"]).status_code, 404)
            invalid = self.client.get("/admin/users/PRIVATE_SENTINEL/assignment-history", headers=self.headers["admin"])
            self.assertEqual(invalid.status_code, 422)
            self.assertNotIn("PRIVATE_SENTINEL", invalid.text)
            self.assertEqual(self.client.get(path + "?limit=51", headers=self.headers["admin"]).status_code, 422)
        finally:
            sql_event.remove(self.engine, "before_cursor_execute", no_private)
        self.assertTrue(selects)

    def test_assignment_mutations_keep_admin_and_exact_json_origin_boundaries(self):
        identity = self.lead()
        self.assign(identity, actor="outsider", expected=403)
        path = f"/admin/leads/{identity}/assign"
        token = self.headers["admin"]["Authorization"].split(" ", 1)[1]
        self.client.cookies.set("ildr_token", token)
        try:
            blocked = self.client.put(path, json={"realtor_id": str(self.ids["agent"])})
            self.assertEqual(blocked.status_code, 403)
        finally:
            self.client.cookies.clear()
        invalid = self.client.put(path, headers=self.headers["admin"],
            json={"realtor_id": str(self.ids["agent"]), "request_id": "PRIVATE_REQUEST_SENTINEL"})
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn("PRIVATE_REQUEST_SENTINEL", invalid.text)
        with self.Session() as db:
            self.assertEqual(db.query(Event).count(), 0)
            self.assertIsNone(db.get(Lead, identity).assigned_realtor_id)

    def test_private_ddl_immutability_and_populated_downgrade_refusal(self):
        self.assign(self.lead())
        with self.engine.connect() as db:
            rows = db.execute(text("SELECT relname,relrowsecurity,relacl::text FROM pg_class WHERE relnamespace=CAST(:schema AS regnamespace) AND relname IN ('assignment_events','assignment_email_jobs')"), {"schema": self.schema}).all()
            self.assertEqual({(row[0], row[1]) for row in rows}, {("assignment_events", True), ("assignment_email_jobs", True)})
            self.assertEqual(db.execute(text("SELECT count(*) FROM pg_policies WHERE schemaname=:schema AND tablename IN ('assignment_events','assignment_email_jobs')"), {"schema": self.schema}).scalar(), 0)
            functions = db.execute(text("SELECT proname,prosecdef,proconfig FROM pg_proc WHERE pronamespace=CAST(:schema AS regnamespace) AND proname IN ('assignment_guard_event','assignment_guard_notice')"), {"schema": self.schema}).all()
            self.assertEqual({row[0] for row in functions}, {"assignment_guard_event", "assignment_guard_notice"})
            self.assertTrue(all(not row[1] and len(row[2]) == 1
                and row[2][0].replace(" ", "") == "search_path=pg_catalog,public" for row in functions))
            for role in ("anon", "authenticated", "service_role"):
                if db.execute(text("SELECT EXISTS(SELECT FROM pg_roles WHERE rolname=:role)"), {"role": role}).scalar():
                    for table in ("assignment_events", "assignment_email_jobs"):
                        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                            self.assertFalse(db.execute(text("SELECT has_table_privilege(:role,:name,:privilege)"),
                                {"role": role, "name": self.schema + "." + table, "privilege": privilege}).scalar())
                    for function in ("assignment_guard_event", "assignment_guard_notice"):
                        self.assertFalse(db.execute(text("SELECT has_function_privilege(:role,:name,'EXECUTE')"),
                            {"role": role, "name": self.schema + "." + function + "()"}).scalar())
        for sql in ("UPDATE assignment_events SET action='cleared'",
                    "UPDATE assignment_email_jobs SET payload='{}'::jsonb",
                    "DELETE FROM assignment_email_jobs"):
            with self.assertRaises(DBAPIError) as rejected:
                with self.engine.begin() as db:
                    db.execute(text(sql))
            self.assertEqual(rejected.exception.orig.pgcode, "23000")
        with self.assertRaises(DBAPIError) as rejected:
            with self.engine.begin() as db:
                self.migration.op = Operations(MigrationContext.configure(db))
                self.migration.downgrade()
        self.assertEqual(rejected.exception.orig.pgcode, "23000")
        with self.Session() as db:
            self.assertEqual((db.query(Event).count(), db.query(Job).count()), (1, 1))

    def test_empty_downgrade_reupgrade_preserves_all_legacy_model_schema_rows(self):
        with self.engine.begin() as db:
            before = db.execute(text("SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema=:schema AND table_name NOT IN ('assignment_events','assignment_email_jobs') ORDER BY table_name,ordinal_position"), {"schema": self.schema}).all()
            users = db.execute(text("SELECT id,email,role,status FROM users ORDER BY id")).all()
            self.migration.op = Operations(MigrationContext.configure(db))
            self.migration.downgrade()
            self.assertEqual(db.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname=:schema"), {"schema": self.schema}).scalar(), 105)
            self.migration.upgrade()
            after = db.execute(text("SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema=:schema AND table_name NOT IN ('assignment_events','assignment_email_jobs') ORDER BY table_name,ordinal_position"), {"schema": self.schema}).all()
            self.assertEqual(before, after)
            self.assertEqual(users, db.execute(text("SELECT id,email,role,status FROM users ORDER BY id")).all())

    def test_pure_new_renderers_preserve_all_existing_assignment_notice_content(self):
        from types import SimpleNamespace
        lead = SimpleNamespace(type="rental_interest", name="Synthetic <Buyer>", email=None,
                               phone="+18095551234", message="<Private> & details")
        cases = [
            (email.send_upgrade_approved_email, ("target@example.com", "Name", "owner"),
             assignment_messages.build_upgrade_approved_notification, ("Name", "owner")),
            (email.send_realtor_assigned_owner_email, ("agent@example.com", "Agent", "Owner", "owner@example.com"),
             assignment_messages.build_realtor_assigned_owner_notification, ("Agent", "Owner", "owner@example.com")),
            (email.send_realtor_lead_assigned_email, ("agent@example.com", "Agent", lead),
             assignment_messages.build_realtor_lead_assigned_notification, ("Agent", lead)),
        ]
        for send, args, build, values in cases:
            with patch.object(email, "_send") as legacy:
                send(*args)
                self.assertEqual(tuple(legacy.call_args.args[1:]), build(*values))

    def test_worker_thread_is_nonblocking_and_cancellation_drains_current_batch(self):
        entered, finished = threading.Event(), threading.Event()
        main_thread = threading.get_ident()
        seen_threads = []
        def batch(_factory, *, stop_event):
            seen_threads.append(threading.get_ident())
            entered.set()
            stop_event.wait(3)
            finished.set()
            return {"processed": 0}
        async def exercise():
            with patch.object(service, "POLL_SECONDS", 0), patch.object(service, "run_batch", batch):
                task = asyncio.create_task(service.worker_loop(None))
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                self.assertFalse(task.done())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(finished.is_set())
        asyncio.run(exercise())
        self.assertTrue(seen_threads)
        self.assertTrue(all(value != main_thread for value in seen_threads))
