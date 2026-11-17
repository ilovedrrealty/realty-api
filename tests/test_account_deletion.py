"""Isolated regressions for persisted deletion requests and private review."""

from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from config import settings
from database import SessionLocal, get_db
from main import app
from models.account_deletion import AccountDeletionEvent, AccountDeletionRequest
from models.user import User
from utils.auth import get_current_user
from utils.limiter import limiter


@unittest.skipUnless(os.environ.get("ACCOUNT_DELETION_FIXTURE") == "yes", "requires isolated PostgreSQL")
class AccountDeletionTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.actor = None
        self.origin = {"Origin": "http://localhost:5174"}
        self.ids = {}
        self.emails = {}
        with SessionLocal() as db:
            for role in ("buyer", "owner", "admin"):
                row = User(id=uuid4(), email=f"deletion-{uuid4()}@example.com",
                           display_name=f"Synthetic {role}", role=role, status="active", email_verified=True)
                self.ids[role], self.emails[role] = row.id, row.email
                db.add(row)
            db.commit()

        def identity(db=Depends(get_db)):
            if self.actor is None:
                from fastapi import HTTPException
                raise HTTPException(401, "Not authenticated")
            return db.get(User, self.actor)

        app.dependency_overrides[get_current_user] = identity
        self.addCleanup(app.dependency_overrides.pop, get_current_user, None)
        for target, name, value in ((settings, "allowed_origins", "http://localhost:5174"),
                                    (limiter, "enabled", False)):
            override = patch.object(target, name, value)
            override.start()
            self.addCleanup(override.stop)

    def as_role(self, role):
        self.actor = self.ids[role]

    def call(self, path, data):
        response = self.client.post(path, headers=self.origin, json=data)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response

    def request(self, role="buyer"):
        return self.call("/auth/me/delete-request", {
            "confirm_email": self.emails[role], "confirm_request": True,
        })

    def status(self):
        response = self.client.get("/auth/me/delete-request")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()

    def test_request_requires_own_email_explicit_consent_and_trusted_json_origin(self):
        self.assertEqual(self.client.get("/auth/me/delete-request").status_code, 401)
        self.as_role("buyer")
        payload = {"confirm_email": self.emails["buyer"], "confirm_request": True}
        self.assertEqual(self.call("/auth/me/delete-request", dict(payload, confirm_request=1)).status_code, 422)
        self.assertEqual(self.call("/auth/me/delete-request", dict(payload, confirm_email=self.emails["owner"])).status_code, 422)
        self.assertEqual(self.client.post("/auth/me/delete-request", data=payload).status_code, 415)
        self.client.cookies.set("ildr_token", "synthetic-cookie")
        self.assertEqual(self.client.post("/auth/me/delete-request", json=payload).status_code, 403)
        self.assertEqual(self.client.post("/auth/me/delete-request", headers={"Origin": "https://other.example"},
                                         json=payload).status_code, 403)
        self.assertEqual(self.status()["status"], "none")

    def test_request_persists_once_cancels_without_erasing_and_can_restart(self):
        self.as_role("buyer")
        first = self.request()
        self.assertEqual(first.status_code, 200)
        saved = first.json()
        self.assertEqual(saved["status"], "pending_review")
        self.assertIsNone(saved["review_after"])
        self.assertEqual(self.request().json(), saved)
        self.assertEqual(self.status(), saved)
        with SessionLocal() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(AccountDeletionEvent)
                                        .where(AccountDeletionEvent.request_id == UUID(saved["request_id"]))), 1)
            self.assertIsNotNone(db.get(User, self.ids["buyer"]).deletion_requested_at)
        cancellation = {"request_id": saved["request_id"], "expected_version": saved["version"]}
        cancelled = self.call("/auth/me/delete-request/cancel", cancellation).json()
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.call("/auth/me/delete-request/cancel", cancellation).json(), cancelled)
        self.assertEqual(self.status(), cancelled)
        with SessionLocal() as db:
            user = db.get(User, self.ids["buyer"])
            self.assertEqual(user.status, "active")
            self.assertIsNone(user.deletion_requested_at)
        later = self.request().json()
        self.assertNotEqual(later["request_id"], saved["request_id"])
        self.assertEqual(later["status"], "pending_review")

    def test_legacy_timestamp_is_visible_and_adopted_without_reset(self):
        self.as_role("owner")
        old = datetime.now(timezone.utc) - timedelta(days=40)
        with SessionLocal() as db:
            db.get(User, self.ids["owner"]).deletion_requested_at = old
            db.commit()
        before = self.status()
        self.assertEqual(before["status"], "pending_review")
        self.assertEqual(datetime.fromisoformat(before["requested_at"]), old)
        self.assertEqual(self.request("owner").json(), before)
        with SessionLocal() as db:
            self.assertEqual(db.get(User, self.ids["owner"]).deletion_requested_at, old)
            event = db.scalar(select(AccountDeletionEvent).where(
                AccountDeletionEvent.request_id == UUID(before["request_id"])))
            self.assertEqual(event.action, "legacy_requested")
            self.assertEqual(event.occurred_at, old)

    def test_review_is_admin_only_versioned_and_cancelled_requests_stay_cancelled(self):
        self.as_role("buyer")
        saved = self.request().json()
        path = f"/admin/account-deletion-requests/{saved['request_id']}/review"
        body = {"expected_version": 1, "status": "retention_hold", "reason": "Review linked property records"}
        self.assertEqual(self.client.get("/admin/account-deletion-requests").status_code, 403)
        self.assertEqual(self.call(path, body).status_code, 403)
        self.as_role("owner")
        self.assertEqual(self.call("/auth/me/delete-request/cancel", {
            "request_id": saved["request_id"], "expected_version": 1,
        }).status_code, 404)
        self.as_role("admin")
        queue = self.client.get("/admin/account-deletion-requests", params={"status": "pending_review"}).json()
        self.assertTrue(any(row["request_id"] == saved["request_id"] for row in queue["items"]))
        reviewed = self.call(path, body).json()
        self.assertEqual(reviewed["version"], 2)
        self.assertEqual(reviewed["status"], "retention_hold")
        self.assertEqual(self.call(path, body).json(), reviewed)
        self.assertEqual(self.call(path, dict(body, status="reviewed", reason="Ready for retention review")).status_code, 409)
        self.as_role("buyer")
        self.assertEqual(self.status()["review_reason"], body["reason"])
        self.assertEqual(self.call("/auth/me/delete-request/cancel", {
            "request_id": saved["request_id"], "expected_version": 1,
        }).status_code, 409)
        self.assertEqual(self.call("/auth/me/delete-request/cancel", {
            "request_id": saved["request_id"], "expected_version": 2,
        }).json()["status"], "cancelled")
        self.as_role("admin")
        self.assertEqual(self.call(path, dict(body, expected_version=3)).status_code, 409)

    def test_review_events_are_immutable_and_private_to_direct_data_roles(self):
        self.as_role("buyer")
        saved = self.request().json()
        with SessionLocal() as db:
            protections = db.execute(text("""SELECT c.relname,c.relrowsecurity,
                bool_or(has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE'))
                FROM pg_class c CROSS JOIN pg_roles r
                WHERE c.oid IN ('public.account_deletion_requests'::regclass,'public.account_deletion_events'::regclass)
                AND r.rolname IN ('anon','authenticated','service_role')
                GROUP BY c.relname,c.relrowsecurity ORDER BY c.relname""")).all()
            self.assertEqual(protections, [("account_deletion_events", True, False),
                                            ("account_deletion_requests", True, False)])
            with self.assertRaises(DBAPIError):
                db.execute(text("UPDATE account_deletion_events SET reason='changed' WHERE request_id=:request"),
                           {"request": saved["request_id"]})
                db.commit()
            db.rollback()
            self.assertEqual(db.get(User, self.ids["buyer"]).status, "active")
