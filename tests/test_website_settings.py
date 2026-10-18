"""Versioned website editor tests, using only synthetic local Postgres data.

Set SEARCH_FILTER_TEST_DATABASE_URL to a disposable loopback Postgres database
and run `python -m unittest discover -s tests -v`. Each suite owns a random
schema. Application secrets/services are disabled before importing routes.
"""

import concurrent.futures
import importlib.util
import json
import os
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

os.environ.update(
    DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-website-tests-only",
    GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
    SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="",
)

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
from models.activity_log import ActivityLog
from models.site_settings import SiteSettings
from models.user import User
from models.website_settings import WebsiteSettings, WebsiteSettingsRevision
from routes import website_settings


def bilingual(value):
    return {"en": value, "es": value + " ES"}


def sample_settings():
    first, second = str(uuid4()), str(uuid4())
    resource = {
        "id": "public-register", "title": bilingual("Public register"),
        "description": bilingual("Official public information"), "category": bilingual("Research"),
        "url": "https://www.ri.gob.do/", "enabled": True,
    }
    return {
        "translations": {"en": {"get_started": {"hero.title": "Talk to our team"}}, "es": {"get_started": {"hero.title": "Hable con nuestro equipo"}}},
        "navigation": [{"route": "landing", "label": bilingual("Home"), "enabled": True}],
        "gallery": {"mode": "featured", "ids": [first, second], "photos": {first: "https://example.com/property.jpg"}},
        "images": {"home_hero": "https://example.com/hero.jpg"},
        "searchAreas": [{"key": "north-coast", "label": bilingual("North Coast"), "locations": ["Sosúa", "Cabarete"]}],
        "resources": [resource],
        "realtorTools": [
            {**resource, "id": "public-research", "access": "public", "mode": "link"},
            {**resource, "id": "agent-research", "title": bilingual("Agent-only research"), "url": "https://example.com/agent-research", "access": "realtor", "mode": "link"},
        ],
        "teamMembers": [{
            "id": "new-person", "name": "Synthetic Team Member", "role": bilingual("Realtor"),
            "description": bilingual("Public staff biography."), "photo": "https://example.com/team.jpg",
            "section": "realtor", "enabled": True,
        }],
    }


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires disposable local Postgres")
class WebsiteSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host not in ("127.0.0.1", "localhost", "::1") or url.get_backend_name() != "postgresql":
            raise ValueError("Website tests require a separate loopback-only Postgres")
        cls.schema = "website_settings_test_" + uuid4().hex
        cls.engine = create_engine(url)
        cls.connection = cls.engine.connect()
        cls.connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.connection.execute(text(f'SET search_path TO "{cls.schema}"'))
        Base.metadata.create_all(cls.connection)
        cls.connection.commit()
        cls.db = Session(cls.connection)
        cls.actors = {}
        cls.auth = {}
        for name, role, verified, status in (
            ("First admin", "admin", True, "active"), ("Second admin", "admin", True, "active"),
            ("Buyer", "buyer", True, "active"), ("Owner", "owner", True, "active"),
            ("Realtor", "realtor", True, "active"), ("Suspended admin", "admin", True, "suspended"),
            ("Unverified admin", "admin", False, "active"),
        ):
            actor = User(email=name.lower().replace(" ", "-") + "@example.invalid", display_name=name, role=role, email_verified=verified, status=status)
            cls.db.add(actor)
            cls.db.flush()
            cls.actors[name] = actor
            cls.auth[name] = {"Authorization": "Bearer " + jwt.encode({"sub": str(actor.id), "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, settings.jwt_secret, algorithm="HS256")}
        cls.platform_data = {"platform_fee_pct": 0.2, "notify_email": "private-platform@example.invalid", "sentinel": "Preserve platform settings"}
        cls.db.add(SiteSettings(id=1, data=deepcopy(cls.platform_data)))
        cls.db.commit()
        cls.app = FastAPI()
        cls.app.include_router(website_settings.router)
        cls.app.dependency_overrides[get_db] = lambda: cls.db
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.db.close()
        cls.connection.rollback()
        assert cls.schema.startswith("website_settings_test_") and len(cls.schema) == 54
        cls.connection.execute(text("SET search_path TO public"))
        cls.connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.connection.commit()
        cls.connection.close()
        cls.engine.dispose()

    def setUp(self):
        self.db.rollback()
        self.db.query(WebsiteSettingsRevision).delete()
        self.db.query(WebsiteSettings).delete()
        self.db.query(ActivityLog).filter(ActivityLog.event_type == "website_content_published").delete()
        self.db.commit()

    def publish(self, content, *, expected=0, actor="First admin", status=200):
        response = self.client.put("/admin/site-settings", headers=self.auth[actor], json={"expected_version": expected, "settings": content})
        self.assertEqual(response.status_code, status, response.text)
        return response

    def test_empty_default_public_and_admin_config_are_no_store(self):
        public = self.client.get("/site/settings")
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json(), {"version": 0, "settings": {}})
        self.assertEqual(public.headers["cache-control"], "no-store")
        admin = self.client.get("/admin/site-settings", headers=self.auth["Second admin"])
        self.assertEqual(admin.json(), {"version": 0, "settings": {}, "updated_at": None})
        self.assertEqual(admin.headers["cache-control"], "no-store")
        self.assertEqual(self.db.query(WebsiteSettings).count(), 0)

    def test_real_auth_rejects_anonymous_buyers_owners_realtors_and_inactive_admins(self):
        operations = [
            ("GET", "/admin/site-settings", None), ("GET", "/admin/site-settings/history", None),
            ("GET", "/admin/site-settings/revisions/0", None),
            ("PUT", "/admin/site-settings", {"expected_version": 0, "settings": {}}),
            ("POST", "/admin/site-settings/restore/0", {"expected_version": 0}),
        ]
        for actor in (None, "Buyer", "Owner", "Realtor", "Suspended admin", "Unverified admin"):
            for method, path, body in operations:
                with self.subTest(actor=actor, method=method, path=path):
                    response = self.client.request(method, path, headers=self.auth[actor] if actor else {}, json=body)
                    self.assertEqual(response.status_code, 401 if actor is None else 403, response.text)
        self.assertEqual(self.db.query(WebsiteSettings).count(), 0)
        self.assertEqual(self.db.query(WebsiteSettingsRevision).count(), 0)

    def test_two_admins_share_access_conflict_history_and_restore_create_new_versions(self):
        first = sample_settings()
        published = self.publish(first)
        self.assertEqual(published.json()["version"], 1)
        self.assertEqual(published.json()["settings"], first)
        second_read = self.client.get("/admin/site-settings", headers=self.auth["Second admin"])
        self.assertEqual(second_read.json()["settings"], first)
        second = deepcopy(first)
        second["teamMembers"][0]["name"] = "Updated team member"
        second["translations"]["en"]["get_started"]["hero.title"] = "Updated form heading"
        self.assertEqual(self.publish(second, expected=1, actor="Second admin").json()["version"], 2)
        stale = self.publish(first, expected=1, status=409)
        self.assertEqual(stale.json()["detail"]["current_version"], 2)
        restored = self.client.post("/admin/site-settings/restore/1", headers=self.auth["First admin"], json={"expected_version": 2})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["version"], 3)
        self.assertEqual(restored.json()["settings"], first)
        initial = self.client.post("/admin/site-settings/restore/0", headers=self.auth["Second admin"], json={"expected_version": 3})
        self.assertEqual(initial.status_code, 200, initial.text)
        self.assertEqual(initial.json()["version"], 4)
        self.assertEqual(initial.json()["settings"], {})
        history = self.client.get("/admin/site-settings/history", headers=self.auth["First admin"])
        self.assertEqual([item["version"] for item in history.json()["items"]], [4, 3, 2, 1, 0])
        self.assertEqual(history.json()["items"][0]["updated_by_name"], "Second admin")
        self.assertIsNone(history.json()["items"][-1]["updated_by_name"])
        original = self.client.get("/admin/site-settings/revisions/1", headers=self.auth["Second admin"])
        self.assertEqual(original.json()["settings"], first)
        self.assertEqual(self.db.query(ActivityLog).filter(ActivityLog.event_type == "website_content_published").count(), 4)
        self.assertEqual(self.db.get(SiteSettings, 1).data, self.platform_data)

    def test_private_tools_never_reach_public_settings_or_unauthorized_users(self):
        content = sample_settings()
        self.publish(content)
        public = self.client.get("/site/settings")
        self.assertEqual([item["id"] for item in public.json()["settings"]["realtorTools"]], ["public-research"])
        serialized = json.dumps(public.json())
        self.assertNotIn("Agent-only research", serialized)
        self.assertNotIn("agent-research", serialized)
        self.assertNotIn("updated_by", serialized)
        for actor in ("First admin", "Second admin", "Realtor"):
            response = self.client.get("/site/realtor-tools", headers=self.auth[actor])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(response.json()), 2)
            self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get("/site/realtor-tools").status_code, 401)
        for actor in ("Buyer", "Owner"):
            self.assertEqual(self.client.get("/site/realtor-tools", headers=self.auth[actor]).status_code, 403)

    def test_invalid_urls_and_private_hosts_cannot_be_published(self):
        urls = [
            "http://example.com/image.jpg", "javascript:alert(1)", "data:image/png;base64,abc",
            "https://user:secret@example.com/image.jpg", "https://localhost/image.jpg",
            "https://127.0.0.1/image.jpg", "https://10.1.2.3/image.jpg", "https://192.168.1.1/image.jpg",
            "https://[::1]/image.jpg", "https://169.254.169.254/latest", "https://internal.local/image.jpg",
            "https://2130706433/image.jpg", "https://127.1/image.jpg", "https://0x7f000001/image.jpg",
            "https://example.com:8443/image.jpg", "https://example.com/\nimage.jpg",
            "https://example.com/%0aimage.jpg", "https://example.com/image.jpg?api_key=secret",
            "https://example.com/image.jpg?access_token=secret",
        ]
        for url in urls:
            with self.subTest(url=url):
                content = sample_settings()
                content["images"]["home_hero"] = url
                self.publish(content, status=422)
        self.assertEqual(self.db.query(WebsiteSettings).count(), 0)

    def test_unknown_fields_prototype_keys_oversized_strings_and_duplicates_rejected(self):
        invalid = [
            {"platform_fee_pct": 0.99}, {"__proto__": {}},
            {"translations": {"en": {"admin": {"title": "No"}}}},
            {"translations": {"en": {"common": {"constructor.prototype.bad": "No"}}}},
            {"translations": {"en": {"common": {"__proto__": "No"}}}},
            {"translations": {"en": {"common": {"title": "x" * 6001}}}},
            {"images": {"unknown_image": "https://example.com/image.jpg"}},
            {"gallery": {"ids": ["not-a-uuid"]}},
            {"gallery": {"photos": {"__proto__": "https://example.com/image.jpg"}}},
            {"searchAreas": [{"key": "__proto__", "label": bilingual("Bad"), "locations": ["Sosua"]}]},
            {"searchAreas": [
                {"key": "first", "label": bilingual("First"), "locations": ["Sosúa"]},
                {"key": "second", "label": bilingual("Second"), "locations": [" SOSU\u0301A "]},
            ]},
            {"searchAreas": [
                {"key": "first", "label": bilingual("First"), "locations": ["Santo Domingo"]},
                {"key": "second", "label": bilingual("Second"), "locations": [" Santo  Domingo "]},
            ]},
        ]
        content = sample_settings()
        duplicate_nav = {"navigation": content["navigation"] * 2}
        invalid.append(duplicate_nav)
        wrong_nav = deepcopy(content["navigation"])
        wrong_nav[0]["route"] = "users"
        invalid.append({"navigation": wrong_nav})
        embed_tool = deepcopy(content["realtorTools"])
        embed_tool[0]["mode"] = "embed"
        invalid.append({"realtorTools": embed_tool})
        oversized_name = deepcopy(content["teamMembers"])
        oversized_name[0]["name"] = "x" * 121
        invalid.append({"teamMembers": oversized_name})
        invalid.append({"resources": content["resources"] * 101})
        for candidate in invalid:
            with self.subTest(candidate=str(candidate)[:100]):
                self.publish(candidate, status=422)
        self.assertEqual(self.db.query(WebsiteSettings).count(), 0)

    def test_raw_body_size_limit_enforced_before_json_validation(self):
        response = self.client.put(
            "/admin/site-settings", headers={**self.auth["First admin"], "Content-Type": "application/json"},
            content=b"x" * (256 * 1024 + 1),
        )
        self.assertEqual(response.status_code, 413, response.text)
        chunked = self.client.put(
            "/admin/site-settings", headers={**self.auth["First admin"], "Content-Type": "application/json"},
            content=iter([b"x" * (256 * 1024 + 1)]),
        )
        self.assertEqual(chunked.status_code, 413, chunked.text)

    def test_unknown_restore_and_stale_restore_do_not_modify_history(self):
        content = sample_settings()
        self.publish(content)
        missing = self.client.post("/admin/site-settings/restore/99", headers=self.auth["First admin"], json={"expected_version": 1})
        self.assertEqual(missing.status_code, 404)
        stale = self.client.post("/admin/site-settings/restore/0", headers=self.auth["Second admin"], json={"expected_version": 0})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.db.query(WebsiteSettingsRevision).count(), 2)
        current = self.client.get("/admin/site-settings", headers=self.auth["First admin"]).json()
        self.assertEqual(current["version"], 1)
        self.assertEqual(current["settings"], content)

    def test_concurrent_first_publish_allows_one_writer_and_conflicts_the_other(self):
        def isolated_db():
            with self.engine.connect() as connection:
                connection.execute(text(f'SET search_path TO "{self.schema}"'))
                connection.commit()
                with Session(connection) as db:
                    yield db

        self.app.dependency_overrides[get_db] = isolated_db
        try:
            def submit(actor):
                return self.client.put("/admin/site-settings", headers=self.auth[actor], json={"expected_version": 0, "settings": sample_settings()})
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(submit, ["First admin", "Second admin"]))
            self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        finally:
            self.app.dependency_overrides[get_db] = lambda: self.db
        self.db.expire_all()
        self.assertEqual(self.db.query(WebsiteSettingsRevision).count(), 2)
        self.assertEqual(self.db.get(WebsiteSettings, 1).version, 1)

    def test_additive_migration_is_idempotent_and_downgrade_keeps_history(self):
        module_path = Path(__file__).parents[1] / "alembic/versions/bcf3e5feca73_add_versioned_website_content_settings.py"
        spec = importlib.util.spec_from_file_location("website_settings_migration", module_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        operations = Operations(MigrationContext.configure(self.connection))
        original_op = migration.op
        migration.op = operations
        try:
            WebsiteSettingsRevision.__table__.drop(self.connection)
            WebsiteSettings.__table__.drop(self.connection)
            migration.upgrade()
            migration.upgrade()
            self.connection.commit()
            rls = self.connection.execute(text("SELECT relrowsecurity FROM pg_class WHERE oid IN ('website_settings'::regclass, 'website_settings_revisions'::regclass)")).scalars().all()
            self.assertEqual(rls, [True, True])
            self.connection.commit()
            self.publish(sample_settings())
            self.db.commit()
            migration.upgrade()
            migration.downgrade()
            self.connection.commit()
            self.assertEqual(self.db.query(WebsiteSettingsRevision).count(), 2)
            self.assertEqual(self.db.get(SiteSettings, 1).data, self.platform_data)
        finally:
            migration.op = original_op
