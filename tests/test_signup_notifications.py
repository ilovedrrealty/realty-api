"""Signup alert contracts with mocked database, mail and Google responses.

No database connections, listening servers or real provider calls are needed.
"""
import json
import operator
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

os.environ.update(
    DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-signup-alert-tests-only",
    GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
    SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="",
)

from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import settings
from database import get_db
from models.pending_user import PendingUser
from models.site_settings import SiteSettings
from models.user import User
from routes import auth
from utils import email
from utils.auth import get_current_user


NOW = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)
RECIPIENTS = ["one@example.com", "two@example.com", "three@example.com", "four@example.com"]


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *conditions):
        self.rows = [row for row in self.rows if all(
            condition.operator(getattr(row, condition.left.key), condition.right.value)
            for condition in conditions
            if condition.operator in (operator.eq, operator.ne)
        )]
        assert all(condition.operator in (operator.eq, operator.ne) for condition in conditions)
        return self

    def first(self):
        return self.rows[0] if self.rows else None


class _Database:
    """Only the equality-query and commit behavior used by these auth routes."""
    def __init__(self):
        self.users = []
        self.pending = []
        self.settings = SiteSettings(id=1, data={"notify_email": ", ".join(RECIPIENTS)})
        self.added, self.deleted = [], []
        self.commit_error = False
        self.settings_error = False

    def query(self, model):
        if model is SiteSettings:
            if self.settings_error:
                raise RuntimeError("synthetic private database detail")
            return _Query([self.settings] if self.settings else [])
        return _Query({User: self.users, PendingUser: self.pending}[model])

    def add(self, row):
        self.added.append(row)

    def delete(self, row):
        self.deleted.append(row)

    def commit(self):
        if self.commit_error:
            raise RuntimeError("synthetic failed commit")
        for row in self.added:
            target = self.users if isinstance(row, User) else self.pending
            if isinstance(row, User):
                row.id, row.role, row.status, row.created_at = uuid4(), "buyer", "active", NOW
            target.append(row)
        for row in self.deleted:
            self.pending.remove(row)
        self.added.clear()
        self.deleted.clear()

    def refresh(self, user):
        assert user in self.users, "The account must be committed before refresh/notification"


class SignupNotificationTests(unittest.TestCase):
    def setUp(self):
        self.db = _Database()
        app = FastAPI()
        app.include_router(auth.router)
        app.dependency_overrides[get_db] = lambda: self.db
        self.app = app
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.old_limiter = auth.limiter.enabled
        auth.limiter.enabled = False
        self.addCleanup(setattr, auth.limiter, "enabled", self.old_limiter)
        self.mail = self.patched(email, "_send")
        self.patched(auth, "send_verification_email")
        self.patched(auth, "hash_password", return_value="synthetic-private-password-hash")
        self.patched(settings, "notify_email", "fallback@example.com")

    def patched(self, target, attribute, *args, **kwargs):
        patcher = patch.object(target, attribute, *args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def pending(self, **changes):
        row = PendingUser(
            email="new@example.com", display_name="New <Buyer> & Team",
            password_hash="synthetic-private-password-hash", verification_code="654321",
            verification_attempts=0,
            verification_code_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=48), **changes,
        )
        self.db.pending.append(row)
        return row

    def existing(self, **changes):
        values = dict(id=uuid4(), email="new@example.com", display_name="Existing Buyer",
                      password_hash="synthetic-private-password-hash", email_verified=True,
                      role="buyer", status="active", created_at=NOW)
        values.update(changes)
        row = User(**values)
        self.db.users.append(row)
        return row

    def google(self, **changes):
        info = {"sub": "synthetic-google-id", "email": "new@example.com", "name": "Google Buyer",
                "email_verified": True, **changes}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(info).encode()
        with patch.object(auth.urllib.request, "urlopen", return_value=response):
            return self.client.post("/auth/google", json={"access_token": "synthetic-private-google-token"})

    def test_verified_email_alerts_after_commit_once_and_repeat_does_not_resend(self):
        self.pending()
        def assert_committed(*_args, **_kwargs):
            self.assertEqual(len(self.db.users), 1)
            self.assertEqual(self.db.pending, [])
            self.assertTrue(self.db.users[0].email_verified)
        self.mail.side_effect = assert_committed
        result = self.client.post("/auth/verify", json={"email": "new@example.com", "code": "654321"})
        self.assertEqual(result.status_code, 200)
        self.assertIn("ildr_token", result.cookies)
        self.mail.assert_called_once()
        self.assertEqual(self.mail.call_args.args[0], RECIPIENTS)
        repeat = self.client.post("/auth/verify", json={"email": "new@example.com", "code": "654321"})
        self.assertEqual(repeat.status_code, 400)
        self.mail.assert_called_once()

    def test_register_and_resend_send_no_signup_alert(self):
        registered = self.client.post("/auth/register", json={
            "display_name": "Pending Buyer", "email": "new@example.com", "password": "synthetic-password",
        })
        self.assertEqual(registered.status_code, 201)
        self.assertEqual(self.db.users, [])
        self.db.pending[0].last_code_sent_at = None
        resent = self.client.post("/auth/resend-code", json={"email": "new@example.com"})
        self.assertEqual(resent.status_code, 200)
        self.mail.assert_not_called()

    def test_wrong_expired_or_exhausted_verification_sends_no_alert(self):
        row = self.pending()
        for code, expires, attempts, expected in (
            ("wrong", datetime.now(timezone.utc) + timedelta(minutes=10), 0, 400),
            ("654321", datetime.now(timezone.utc) - timedelta(minutes=1), 0, 400),
            ("654321", datetime.now(timezone.utc) + timedelta(minutes=10), 5, 429),
        ):
            with self.subTest(expected=expected, attempts=attempts):
                row.verification_code_expires_at, row.verification_attempts = expires, attempts
                result = self.client.post("/auth/verify", json={"email": row.email, "code": code})
                self.assertEqual(result.status_code, expected)
        self.assertEqual(self.db.users, [])
        self.mail.assert_not_called()

    def test_google_first_signup_alerts_once_and_subsequent_login_does_not(self):
        def assert_committed(*_args, **_kwargs):
            self.assertEqual(len(self.db.users), 1)
            self.assertTrue(self.db.users[0].email_verified)
            self.assertEqual(self.db.users[0].created_at, NOW)
        self.mail.side_effect = assert_committed
        self.assertEqual(self.google().status_code, 200)
        self.assertEqual(len(self.db.users), 1)
        self.mail.assert_called_once()
        self.assertEqual(self.mail.call_args.args[0], RECIPIENTS)
        self.assertEqual(self.google().status_code, 200)
        self.mail.assert_called_once()
        self.assertEqual(len(self.db.users), 1)

    def test_google_existing_email_link_and_existing_avatar_update_do_not_alert(self):
        user = self.existing()
        self.assertEqual(self.google(picture="https://example.com/photo.jpg").status_code, 200)
        self.assertEqual(user.google_id, "synthetic-google-id")
        user.avatar_url = None
        self.assertEqual(self.google(picture="https://example.com/new-photo.jpg").status_code, 200)
        self.mail.assert_not_called()

    def test_unverified_or_missing_google_identity_does_not_create_or_alert(self):
        for changes in ({"email_verified": False}, {"sub": None}, {"email": None}):
            with self.subTest(changes=changes):
                self.assertEqual(self.google(**changes).status_code, 401)
        self.assertEqual(self.db.users, [])
        self.mail.assert_not_called()

    def test_password_login_refresh_and_explicit_google_link_do_not_alert(self):
        user = self.existing()
        self.app.dependency_overrides[get_current_user] = lambda: user
        with patch.object(auth, "verify_password", return_value=True):
            self.assertEqual(self.client.post("/auth/login", json={
                "email": user.email, "password": "synthetic-password",
            }).status_code, 200)
        self.assertEqual(self.client.post("/auth/refresh").status_code, 200)
        info = MagicMock()
        info.__enter__.return_value.read.return_value = json.dumps({
            "sub": "linked-google", "email_verified": True,
        }).encode()
        # The link route checks for another account with the same Google ID.
        with patch.object(auth.urllib.request, "urlopen", return_value=info):
            self.assertEqual(self.client.post("/auth/link-google", json={
                "access_token": "synthetic-private-google-token",
            }).status_code, 200)
        self.mail.assert_not_called()

    def test_failed_account_commit_never_alerts(self):
        self.pending()
        self.db.commit_error = True
        result = self.client.post("/auth/verify", json={"email": "new@example.com", "code": "654321"})
        self.assertEqual(result.status_code, 500)
        self.assertEqual(self.db.users, [])
        self.mail.assert_not_called()

    def test_send_failure_preserves_signup_cookie_and_logs_no_private_detail_or_retry(self):
        self.pending()
        self.mail.side_effect = RuntimeError("private name new@example.com provider-secret")
        with self.assertLogs(auth.logger, level="ERROR") as logged:
            result = self.client.post("/auth/verify", json={"email": "new@example.com", "code": "654321"})
        self.assertEqual(result.status_code, 200)
        self.assertIn("ildr_token", result.cookies)
        self.assertEqual(len(self.db.users), 1)
        self.assertEqual(logged.output, ["ERROR:routes.auth:verified_signup_notification_failed"])
        self.assertEqual(self.client.post("/auth/verify", json={
            "email": "new@example.com", "code": "654321",
        }).status_code, 400)
        self.mail.assert_called_once()

    def test_notification_setting_lookup_failure_does_not_break_committed_signup(self):
        self.pending()
        self.db.settings_error = True
        with self.assertLogs(auth.logger, level="ERROR") as logged:
            result = self.client.post("/auth/verify", json={"email": "new@example.com", "code": "654321"})
        self.assertEqual(result.status_code, 200)
        self.assertIn("ildr_token", result.cookies)
        self.assertEqual(logged.output, ["ERROR:routes.auth:verified_signup_notification_failed"])
        self.mail.assert_not_called()

    def test_google_send_failure_keeps_signup_successful_and_login_does_not_retry(self):
        self.mail.side_effect = RuntimeError("private provider detail")
        with self.assertLogs(auth.logger, level="ERROR") as logged:
            result = self.google()
        self.assertEqual(result.status_code, 200)
        self.assertIn("ildr_token", result.cookies)
        self.assertEqual(logged.output, ["ERROR:routes.auth:verified_signup_notification_failed"])
        self.assertEqual(self.google().status_code, 200)
        self.mail.assert_called_once()

    def test_db_settings_override_and_case_insensitive_recipient_deduplication(self):
        self.pending()
        self.db.settings.data["notify_email"] = ", ".join(RECIPIENTS + ["ONE@example.com"])
        self.assertEqual(self.client.post("/auth/verify", json={
            "email": "new@example.com", "code": "654321",
        }).status_code, 200)
        self.assertEqual(self.mail.call_args.args[0], RECIPIENTS)

    def test_missing_or_blank_db_setting_falls_back_and_empty_configuration_sends_nothing(self):
        user = self.existing()
        for row in (None, SiteSettings(id=1, data={}), SiteSettings(id=1, data={"notify_email": ""})):
            self.db.settings = row
            self.mail.reset_mock()
            auth._notify_verified_signup(self.db, user)
            self.assertEqual(self.mail.call_args.args[0], ["fallback@example.com"])
        with patch.object(settings, "notify_email", ""):
            self.mail.reset_mock()
            auth._notify_verified_signup(self.db, user)
            self.mail.assert_not_called()

    def test_invalid_recipient_list_sends_no_partial_alert_but_signup_succeeds(self):
        self.pending()
        self.db.settings.data["notify_email"] = "one@example.com,not-an-email,two@example.com"
        with self.assertLogs(auth.logger, level="ERROR"):
            result = self.client.post("/auth/verify", json={"email": "new@example.com", "code": "654321"})
        self.assertEqual(result.status_code, 200)
        self.mail.assert_not_called()

    def test_email_contains_only_minimal_escaped_metadata_and_canonical_admin_link(self):
        user = SimpleNamespace(display_name='<b>A & "B"</b>', email='test&tag@example.com',
                               role='<buyer>', created_at=NOW,
                               password_hash="synthetic-private-password-hash", verification_code="654321",
                               google_id="synthetic-private-google-token", phone="private-phone")
        email.send_new_user_notification(user, notify_email=", ".join(RECIPIENTS))
        recipients, subject, html = self.mail.call_args.args
        self.assertEqual(recipients, RECIPIENTS)
        self.assertEqual(subject, "New verified signup — I Love DR Realty")
        self.assertIn('&lt;b&gt;A &amp; &quot;B&quot;&lt;/b&gt;', html)
        self.assertIn("test&amp;tag@example.com", html)
        self.assertIn("&lt;buyer&gt;", html)
        self.assertIn(NOW.isoformat(), html)
        self.assertIn('href="https://app.ilovedrrealty.com/users?workspace=admin"', html)
        for private in (user.password_hash, user.verification_code, user.google_id, user.phone):
            self.assertNotIn(private, html + subject)


if __name__ == "__main__":
    unittest.main()
