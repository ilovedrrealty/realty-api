"""Owned fallback definitions; no production DB or genuine-migration claim.

Run only in a fresh marked loopback test process. This model-created schema
installs the retained f7 guards; a separate hosted startup/preservation proof
must establish fallback safety on a genuinely upgraded, populated108 database.
"""
import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import smtplib
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib import request as urllib_request
from uuid import UUID, uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-fallback-tests-only",
    ALLOWED_ORIGINS="http://testserver", GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="",
    RESEND_API_KEY="", SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")

import httpx
import jwt
import requests
import resend
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from config import settings
from database import Base, get_db
import models
import models.trade_contact
from models.assignment_notification import AssignmentEvent, AssignmentEmailJob
from models.lead import Lead
from models.lead_delivery import LeadDeliveryJob
from models.user import User
from routes import admin, leads, assignment_notification, lead_delivery as delivery_routes
from services import assignment_notification as assignment_service, lead_delivery as delivery_service


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires fixed owned loopback PostgreSQL")
class AssignmentFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
        if not re.fullmatch(r"[a-f0-9]{32}", nonce):
            raise ValueError("Require the owned fallback test nonce")
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if (url.get_backend_name(), url.host, url.port, url.database) != (
                "postgresql", "127.0.0.1", 55449, "ildr_platform_test_20261025"):
            raise ValueError("Use only the fixed disposable loopback database")
        cls.schema = "assignment_fallback_test_" + uuid4().hex
        cls.bootstrap = create_engine(url, connect_args={"connect_timeout": 5})
        try:
            with cls.bootstrap.connect() as db:
                claim = db.execute(text("SELECT current_database(),host(inet_server_addr()),inet_server_port(),"
                    "current_setting('server_version_num')::int,shobj_description(oid,'pg_database'),"
                    "NOT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),"
                    "NOT EXISTS(SELECT FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg_%%') "
                    "FROM pg_database WHERE datname=current_database()")).one()
                if tuple(claim) != (url.database, "127.0.0.1", 55449, 170006,
                                    "ildr-assignment-fallback-unit-ci:" + nonce, True, True):
                    raise ValueError("Require the fresh marked fallback test database")
        except BaseException:
            cls.bootstrap.dispose()
            raise
        with cls.bootstrap.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.addClassCleanup(cls.cleanup_schema)
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema, "connect_timeout": 5},
                                   pool_size=4, pool_timeout=5)
        cls.Session = sessionmaker(bind=cls.engine)
        if len(Base.metadata.tables) != 107:
            raise AssertionError("Expected retained107 models")
        Base.metadata.create_all(cls.engine)
        path = Path(__file__).resolve().parents[1] / "alembic/versions/f7a9c3e2d114_private_assignment_notifications.py"
        spec = importlib.util.spec_from_file_location("owned_fallback_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with cls.engine.begin() as db:
            migration.op = Operations(MigrationContext.configure(db))
            migration.upgrade()
        app = FastAPI()
        for router in (admin.router, leads.router, assignment_notification.router, delivery_routes.router):
            app.include_router(router)
        def owned_db():
            with cls.Session() as db:
                yield db
        app.dependency_overrides[get_db] = owned_db
        cls.client = TestClient(app, raise_server_exceptions=False)

    @classmethod
    def cleanup_schema(cls):
        if hasattr(cls, "client"):
            cls.client.close()
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        assert re.fullmatch(r"assignment_fallback_test_[a-f0-9]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as db:
                db.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        finally:
            cls.bootstrap.dispose()

    def setUp(self):
        with self.engine.begin() as db:
            names = ",".join(f'"{self.schema}"."{table.name}"' for table in Base.metadata.sorted_tables)
            db.execute(text("TRUNCATE " + names + " RESTART IDENTITY CASCADE"))
        self.external_attempts = []
        def refuse(*_args, **_kwargs):
            self.external_attempts.append(1)
            raise AssertionError("External transport is forbidden")
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        for owner, name in ((httpx.HTTPTransport, "handle_request"), (httpx.AsyncHTTPTransport, "handle_async_request"),
                (requests.Session, "send"), (urllib_request, "urlopen"), (resend.Emails, "send"),
                (smtplib.SMTP, "connect"), (smtplib.SMTP_SSL, "connect")):
            self.patches.enter_context(patch.object(owner, name, refuse))
        self.addCleanup(lambda: self.assertEqual(self.external_attempts, []))
        self.ids = {name: UUID(int=920000 + number) for number, name in enumerate(("admin", "agent", "buyer"), 1)}
        self.headers = {name: {"Authorization": "Bearer " + jwt.encode(
            {"sub": str(identity), "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            settings.jwt_secret, algorithm="HS256")} for name, identity in self.ids.items()}
        self.lead_id, self.lead_request, self.account_request = uuid4(), uuid4(), uuid4()
        self.event_ids = {"lead": uuid4(), "account": uuid4()}
        now = datetime.now(timezone.utc)
        payload = {"recipient_email": "agent@example.com", "body": {"from": "rehearsal@example.com",
            "to": ["agent@example.com"], "subject": "PRIVATE_FALLBACK_SUBJECT", "html": "PRIVATE_FALLBACK_BODY"}}
        with self.Session.begin() as db:
            for name, role in (("admin", "admin"), ("agent", "realtor"), ("buyer", "buyer")):
                db.add(User(id=self.ids[name], user_code=10000 + self.ids[name].int - 920000,
                    email=name + "@example.com", display_name="Synthetic " + name,
                    role=role, email_verified=True, status="active"))
            db.flush()
            db.get(User, self.ids["buyer"]).assigned_realtor_id = self.ids["agent"]
            db.add(Lead(id=self.lead_id, type="seller_interest", name="PRIVATE_FALLBACK_LEAD", email="buyer@example.com",
                message="PRIVATE_FALLBACK_BODY", from_user_id=self.ids["buyer"], status="assigned",
                assigned_realtor_id=self.ids["agent"], assigned_at=now))
            db.flush()
            for kind, identity, request_id in (("lead", self.lead_id, self.lead_request),
                                               ("account", self.ids["buyer"], self.account_request)):
                digest = sha256(canonical({"source_kind": kind, "source_id": str(identity),
                                          "agent_id": str(self.ids["agent"])}).encode()).hexdigest()
                db.add(AssignmentEvent(id=self.event_ids[kind], source_kind=kind,
                    lead_id=identity if kind == "lead" else None, user_id=identity if kind == "account" else None,
                    actor_id=self.ids["admin"], request_id=request_id, request_sha256=digest,
                    previous_agent_id=None, agent_id=self.ids["agent"], cycle=1, action="assigned", created_at=now))
            db.flush()
            db.add(AssignmentEmailJob(event_id=self.event_ids["lead"], kind="lead_assigned_agent",
                recipient_user_id=self.ids["agent"], payload=payload, payload_sha256=sha256(canonical(payload).encode()).hexdigest(),
                status="pending", attempt_count=0, outcome_uncertain=False, created_at=now, updated_at=now))
            db.add(LeadDeliveryJob(user_id=self.ids["buyer"], step="customer_email", provider="resend",
                payload={"private": "PRIVATE_SIGNUP_BODY"}, payload_sha256="c" * 64,
                status="pending", attempt_count=0, safe_to_retry=False, outcome_uncertain=False,
                next_attempt_at=now, created_at=now, updated_at=now))

    def snapshot(self):
        result = {}
        with self.engine.connect() as db:
            for name in sorted(Base.metadata.tables):
                result[name] = db.execute(text('SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'
                    "'[]'::jsonb)::text FROM " + f'"{self.schema}"."{name}" t')).scalar_one()
            result["sequences"] = db.execute(text("SELECT sequencename,last_value FROM pg_sequences "
                "WHERE schemaname=:schema ORDER BY sequencename"), {"schema": self.schema}).all()
        return result

    def paused(self, path, body):
        before = self.snapshot()
        with ExitStack() as stack:
            untouched = [stack.enter_context(patch.object(assignment_service, name))
                         for name in ("prepare_request", "lock_users", "record_event", "run_batch")]
            untouched.append(stack.enter_context(patch.object(leads.ghl, "update_contact_status")))
            response = self.client.put(path, json=body, headers=self.headers["admin"])
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"detail": "Assignment changes are temporarily paused"})
            self.assertEqual(response.headers["cache-control"], "no-store")
            for operation in untouched:
                operation.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_lead_legacy_fresh_and_exact_replay_pause_before_any_assignment_work(self):
        path = f"/admin/leads/{self.lead_id}/assign"
        for body in ({"realtor_id": str(self.ids["agent"])},
                     {"realtor_id": str(self.ids["admin"]), "request_id": str(uuid4())},
                     {"realtor_id": str(self.ids["agent"]), "request_id": str(self.lead_request)}):
            with self.subTest(body_kind="legacy_fresh_or_replay"):
                self.paused(path, body)

    def test_account_assign_clear_and_exact_replay_preserve_all_existing_data(self):
        path = f"/admin/users/{self.ids['buyer']}/assign-realtor"
        for body in ({"realtor_id": str(self.ids["admin"]), "request_id": str(uuid4())},
                     {"realtor_id": None, "request_id": str(uuid4())},
                     {"realtor_id": str(self.ids["agent"]), "request_id": str(self.account_request)},
                     {"realtor_id": None}):
            self.paused(path, body)

    def test_populated_assignment_and_signup_histories_remain_private_metadata(self):
        before = self.snapshot()
        for kind, target in (("leads", self.lead_id), ("users", self.ids["buyer"])):
            path = f"/admin/{kind}/{target}/assignment-history"
            response = self.client.get(path, headers=self.headers["admin"])
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            page = response.json()
            self.assertEqual(set(page), {"total", "limit", "offset", "tracked", "items"})
            self.assertEqual((page["total"], page["tracked"]), (1, True))
            item = page["items"][0]
            self.assertEqual(set(item), {"id", "action", "created_at", "notices"})
            self.assertEqual(item["action"], "assigned")
            self.assertEqual(len(item["notices"]), int(kind == "leads"))
            if kind == "leads":
                self.assertEqual(set(item["notices"][0]), {"kind", "status", "attempts", "created_at",
                    "attempted_at", "accepted_at", "outcome_uncertain"})
                self.assertEqual((item["notices"][0]["status"], item["notices"][0]["attempts"]), ("pending", 0))
            for marker in ("PRIVATE_FALLBACK", "@example.com", "payload", "recipient", "provider_id"):
                self.assertNotIn(marker, response.text)
            self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(self.client.get(path, headers=self.headers["buyer"]).status_code, 403)
        signup = self.client.get(f"/admin/users/{self.ids['buyer']}/communications", headers=self.headers["admin"])
        self.assertEqual(signup.status_code, 200)
        self.assertEqual(signup.json()["total"], 1)
        self.assertEqual(signup.json()["items"][0]["step"], "customer_email")
        self.assertNotIn("PRIVATE_SIGNUP_BODY", signup.text)
        self.assertEqual(self.snapshot(), before)

    def test_pause_keeps_admin_authentication_and_json_origin_boundaries(self):
        before = self.snapshot()
        for path in (f"/admin/leads/{self.lead_id}/assign", f"/admin/users/{self.ids['buyer']}/assign-realtor"):
            body = {"realtor_id": str(self.ids["agent"]), "request_id": str(uuid4())}
            self.assertEqual(self.client.put(path, json=body).status_code, 401)
            self.assertEqual(self.client.put(path, json=body, headers=self.headers["buyer"]).status_code, 403)
            hostile = {**self.headers["admin"], "Origin": "https://untrusted.example"}
            self.assertEqual(self.client.put(path, json=body, headers=hostile).status_code, 403)
            plain = {**self.headers["admin"], "Content-Type": "text/plain"}
            # FastAPI rejects the non-JSON body before either handler runs.
            self.assertEqual(self.client.put(path, content=canonical(body), headers=plain).status_code, 422)
        self.assertEqual(self.snapshot(), before)

    def test_lifespan_pauses_only_assignment_worker_and_drains_prior_tasks(self):
        import main
        before = self.snapshot()
        async def exercise(enabled):
            entered, retired, factories = [], [], []
            ready = asyncio.Event()
            async def hold(label):
                entered.append(label)
                if len(entered) == 3:
                    ready.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    retired.append(label)
            async def old_delivery(factory):
                factories.append(factory)
                await hold("delivery")
            with patch.object(main, "_cleanup_pending_users", lambda: hold("cleanup")), \
                 patch.object(main, "_payout_cron", lambda: hold("payout")), \
                 patch.object(delivery_service, "worker_loop", old_delivery), \
                 patch.object(assignment_service, "worker_loop", new_callable=AsyncMock) as assignment_worker, \
                 patch.object(settings, "lead_delivery_enabled", enabled), \
                 patch.object(settings, "customer_messages_enabled", True):
                async with main.lifespan(main.app):
                    await asyncio.wait_for(ready.wait(), timeout=5)
                    self.assertCountEqual(entered, ["cleanup", "payout", "delivery"])
                    self.assertEqual(factories, [main.SessionLocal])
                    self.assertEqual(delivery_service.enabled(), enabled)
                    self.assertEqual(delivery_service.signup_enabled(), enabled)
                    assignment_worker.assert_not_called()
                self.assertCountEqual(retired, ["cleanup", "payout", "delivery"])
                assignment_worker.assert_not_called()
        for enabled in (False, True):
            asyncio.run(exercise(enabled))
        self.assertEqual(self.snapshot(), before)

    def test_existing_delivery_pause_policy_does_not_touch_pending_signup_or_assignment_jobs(self):
        before = self.snapshot()
        forbidden_factory = Mock(side_effect=AssertionError("Disabled delivery must not query"))
        with patch.object(settings, "lead_delivery_enabled", False):
            self.assertEqual(delivery_service.run_batch(forbidden_factory), {"enabled": False, "processed": 0})
            forbidden_factory.assert_not_called()
        self.assertEqual(self.snapshot(), before)
