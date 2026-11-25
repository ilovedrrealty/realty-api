"""Owned loopback PG recovery; no providers, production sessions or automatic replay."""
import asyncio
import os
import re
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-pool-recovery-only",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
                  LEAD_DELIVERY_WORKER_ENABLED="false", CUSTOMER_MESSAGES_ENABLED="false",
                  SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

import models
from database import Base
from models.lead_delivery import LeadDeliveryJob, LeadDeliveryWorkerState
from services import lead_delivery as service
from utils.database_connection import engine_options


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback PostgreSQL")
class DatabasePoolRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw_url = os.environ["SEARCH_FILTER_TEST_DATABASE_URL"]
        url = make_url(raw_url)
        if url.host not in ("127.0.0.1", "localhost", "::1") or url.drivername not in ("postgresql", "postgresql+psycopg2"):
            raise ValueError("Only the reviewed disposable loopback psycopg2 database is allowed")
        cls.schema = "pool_recovery_test_"+uuid4().hex
        cls.label = cls.schema
        cls.bootstrap = create_engine(url, **engine_options(raw_url))
        cls.addClassCleanup(cls.cleanup_owned_schema)
        with cls.bootstrap.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        options = engine_options(raw_url)
        options["connect_args"].update({"application_name": cls.label,
            "options": "-csearch_path="+cls.schema+" -clock_timeout=5000 -cstatement_timeout=5000"})
        cls.engine = create_engine(url, pool_size=1, max_overflow=0, **options)
        Base.metadata.create_all(cls.engine)
        with cls.engine.begin() as db:
            db.execute(text("CREATE TABLE resilience_probe (value TEXT NOT NULL)"))

    @classmethod
    def cleanup_owned_schema(cls):
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        assert re.fullmatch(r"pool_recovery_test_[0-9a-f]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as db:
                db.execute(text(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE'))
        finally:
            cls.bootstrap.dispose()

    def setUp(self):
        with self.engine.begin() as db:
            db.execute(text("TRUNCATE lead_delivery_worker_state,resilience_probe"))
        self.providers = []
        for target, attribute in ((httpx.HTTPTransport, "handle_request"),
                (httpx.AsyncHTTPTransport, "handle_async_request"), (service, "_provider_result")):
            item = patch.object(target, attribute, side_effect=AssertionError("No external provider activity"))
            self.providers.append((item, item.start()))
        self.addCleanup(self.stop_providers)

    def stop_providers(self):
        for item, mock in reversed(self.providers):
            item.stop()
            self.assertEqual(mock.call_count, 0)

    def owned_pid(self, connection):
        pid, label, schema = connection.execute(text("SELECT pg_backend_pid(),current_setting('application_name'),current_schema()")).one()
        self.assertEqual((label, schema), (self.label, self.schema))
        return pid

    def terminate_owned(self, pid):
        # A current PID alone is insufficient: bind the target to this unique
        # test-owned application and the same reviewed disposable database.
        with self.bootstrap.begin() as db:
            result = db.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid=:pid "
                "AND application_name=:label AND datname=current_database() AND pid<>pg_backend_pid()"),
                {"pid": pid, "label": self.label}).all()
        self.assertEqual(result, [(True,)])

    def stale_pool_pid(self):
        with self.engine.connect() as db:
            pid = self.owned_pid(db)
        self.terminate_owned(pid)
        return pid

    def test_server_closed_idle_connection_is_replaced_before_first_application_query(self):
        old = self.stale_pool_pid()
        with self.engine.connect() as db:
            self.assertEqual(db.execute(text("SELECT 1")).scalar(), 1)
            self.assertNotEqual(self.owned_pid(db), old)

    def test_inflight_disconnect_rolls_back_and_does_not_replay_an_application_write(self):
        with self.engine.connect() as db:
            transaction = db.begin()
            pid = self.owned_pid(db)
            db.execute(text("INSERT INTO resilience_probe(value) VALUES('synthetic-uncommitted')"))
            self.terminate_owned(pid)
            with self.assertRaises(DBAPIError) as caught:
                db.execute(text("SELECT 1"))
            self.assertTrue(caught.exception.connection_invalidated)
            transaction.rollback()
        with self.engine.connect() as db:
            self.assertEqual(db.execute(text("SELECT count(*) FROM resilience_probe")).scalar(), 0)
            self.assertEqual(db.execute(text("SELECT 1")).scalar(), 1)

    def test_worker_recovers_stale_pool_and_never_overlaps_or_abandons_active_batch(self):
        self.stale_pool_pid()
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        counts = {"active": 0, "maximum": 0, "batches": 0}
        original = service.run_batch

        def factory():
            return Session(self.engine)

        def tracked(*args, **kwargs):
            counts["active"] += 1
            counts["maximum"] = max(counts["maximum"], counts["active"])
            counts["batches"] += 1
            try:
                result = original(*args, **kwargs)
                if result != {"enabled": True, "processed": 0}:
                    raise AssertionError("Synthetic empty worker did not recover its DB")
                started.set()
                if not release.wait(5):
                    raise AssertionError("Synthetic active worker was not released")
                return result
            finally:
                counts["active"] -= 1
                finished.set()

        async def scenario():
            with patch.object(service, "enabled", return_value=True), patch.object(service, "POLL_SECONDS", 0.001), patch.object(service, "run_batch", side_effect=tracked):
                task = asyncio.create_task(service.worker_loop(factory))
                try:
                    self.assertTrue(await asyncio.wait_for(asyncio.to_thread(started.wait, 5), timeout=6))
                    task.cancel()
                    for _ in range(5):
                        await asyncio.sleep(0)
                    self.assertFalse(task.done(), "Worker abandoned its live thread")
                    self.assertEqual((counts["batches"], counts["maximum"]), (1, 1))
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 6)
                    self.assertTrue(finished.is_set())
                finally:
                    release.set()
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass

        asyncio.run(scenario())
        with factory() as db:
            state = db.get(LeadDeliveryWorkerState, 1)
            self.assertIsNotNone(state.last_poll_at)
            self.assertIsNotNone(state.last_completed_at)
            self.assertIsNone(state.error_code)
            self.assertEqual(db.query(LeadDeliveryJob).count(), 0)
        self.assertEqual((counts["batches"], counts["active"], counts["maximum"]), (1, 0, 1))


if __name__ == "__main__":
    unittest.main()
