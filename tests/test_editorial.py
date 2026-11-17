"""Private editorial revision and publication contracts on owned loopback PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from uuid import uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-editorial-test",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
                  SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")

import jwt
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from config import settings
from database import Base, get_db
from models.user import User
from routes import editorial as routes
from schemas.editorial import RESERVED_SLUGS


ADMIN = "/admin/editorial"
PUBLIC = "/site/editorial"
SOURCE = {"label": "Synthetic reference", "url": "https://example.org/resource"}
PRIVATE_NOTE = "PRIVATE_EDITORIAL_EVIDENCE"


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires owned loopback PostgreSQL")
class EditorialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if (url.get_backend_name() != "postgresql" or url.host != "127.0.0.1" or
                url.port != 55449 or url.database != "ildr_platform_test_20261025"):
            raise ValueError("Use the fixed disposable loopback PostgreSQL database")
        cls.schema = "editorial_test_" + uuid4().hex
        cls.bootstrap = create_engine(url)
        cls.addClassCleanup(cls.cleanup_schema)
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        options = f"-csearch_path={cls.schema} -clock_timeout=5000 -cstatement_timeout=10000"
        cls.engine = create_engine(url, connect_args={"options": options})
        Base.metadata.create_all(cls.engine, tables=(User.__table__,))
        path = Path(__file__).resolve().parents[1] / "alembic/versions/b35e60f12da4_private_bilingual_editorial.py"
        spec = spec_from_file_location("editorial_revision", path)
        revision = module_from_spec(spec)
        spec.loader.exec_module(revision)
        with cls.engine.begin() as connection:
            revision.op = Operations(MigrationContext.configure(connection))
            revision.upgrade()

        def local_db():
            with Session(cls.engine) as session:
                yield session

        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[get_db] = local_db
        cls.app = app

    @classmethod
    def cleanup_schema(cls):
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        assert re.fullmatch(r"editorial_test_[0-9a-f]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE'))
        finally:
            cls.bootstrap.dispose()

    def setUp(self):
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.ids, self.tokens = {}, {}
        with Session(self.engine) as db:
            db.execute(text("TRUNCATE TABLE editorial_events, editorial_revisions, editorial_pages"))
            for name, role in (("editor", "admin"), ("reviewer", "admin"), ("buyer", "buyer"),
                               ("owner", "owner"), ("realtor", "realtor"),
                               ("unverified", "admin"), ("suspended", "admin")):
                identifier = uuid4()
                db.add(User(id=identifier, email=f"{identifier}@example.invalid", role=role,
                            display_name="Synthetic editor", status="suspended" if name == "suspended" else "active",
                            email_verified=name != "unverified"))
                self.ids[name] = identifier
                self.tokens[name] = jwt.encode({"sub": str(identifier)}, settings.jwt_secret, algorithm="HS256")
            db.commit()

    def request(self, method, path, expected, *, actor="editor", data=None, headers=None, **kwargs):
        outgoing = dict(headers or {})
        if actor is not None:
            outgoing["Authorization"] = "Bearer " + self.tokens[actor]
        if data is not None:
            kwargs["json"] = data
        result = self.client.request(method, path, headers=outgoing, **kwargs)
        self.assertEqual(result.status_code, expected)
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertEqual(result.headers["pragma"], "no-cache")
        self.assertEqual(result.headers["vary"], "Cookie, Authorization")
        if path.startswith(PUBLIC) or expected >= 400:
            self.assertNotIn("PRIVATE_", result.text)
            self.assertNotIn("example.invalid", result.text)
        return result.json()

    @staticmethod
    def payload(*, suffix="", sources=None, spanish_body=None):
        return {
            "content": {
                "en": {"title": "English title" + suffix, "summary": "English summary", "body": "English body"},
                "es": {"title": "Título en español" + suffix, "summary": "Resumen",
                       "body": "Texto en español" if spanish_body is None else spanish_body},
            },
            "sources": [SOURCE] if sources is None else sources,
            "review_note": PRIVATE_NOTE,
        }

    def create(self, slug="synthetic-guide", *, kind="guide", payload=None, expected=201):
        return self.request("POST", ADMIN, expected, data={
            "slug": slug, "kind": kind, **(self.payload() if payload is None else payload),
        })

    def change(self, page, action, *, expected=200, revision=None, actor="editor"):
        body = {"expected_version": page["version"]}
        if action != "unpublish":
            body["revision"] = page["draft_revision"] if revision is None else revision
        return self.request("POST", f"{ADMIN}/{page['id']}/{action}", expected, data=body, actor=actor)

    def save(self, page, payload, *, actor="editor", expected=200):
        return self.request("PUT", f"{ADMIN}/{page['id']}/draft", expected, actor=actor,
                            data={"expected_version": page["version"], **payload})

    def test_verified_admin_only_and_revision_preview_is_private(self):
        page = self.create()
        path = f"{ADMIN}/{page['id']}"
        for actor in (None, "buyer", "owner", "realtor", "suspended", "unverified"):
            status = 401 if actor is None else 403
            for admin_path in (ADMIN, path, path + "/revisions", path + "/revisions/1/preview"):
                self.request("GET", admin_path, status, actor=actor)
            self.request("POST", path + "/publish", status, actor=actor, data={"expected_version": 1, "revision": 1})
        preview = self.request("GET", path + "/revisions/1/preview", 200, actor="reviewer")
        self.assertEqual(preview, {"revision": 1, **self.payload()})
        self.request("GET", path + "/revisions/2/preview", 404)
        self.request("GET", path + "/revisions/0/preview", 422)
        self.request("GET", f"{ADMIN}/{uuid4()}", 404)
        self.request("GET", f"{PUBLIC}/{page['slug']}?preview=1", 404)

    def test_review_requires_both_languages_sources_and_exact_revision(self):
        page = self.create(payload=self.payload(spanish_body="  "))
        self.assertEqual((page["version"], page["draft_revision"], page["reviewed_revision"]), (1, 1, None))
        self.change(page, "publish", expected=409)
        self.change(page, "review", expected=422)
        source_missing = self.save(page, self.payload(sources=[]))
        self.change(source_missing, "review", expected=422)
        complete = self.save(source_missing, self.payload())
        self.change(complete, "review", revision=1, expected=409)
        reviewed = self.change(complete, "review", actor="reviewer")
        self.assertEqual((reviewed["version"], reviewed["reviewed_revision"]), (4, 3))
        self.assertEqual(self.request("GET", PUBLIC, 200, actor=None), {"items": [], "total": 0})
        self.change(complete, "publish", expected=409)
        self.change(reviewed, "publish", revision=1, expected=409)
        self.change(reviewed, "publish")
        for language in ("en", "es"):
            public = self.request("GET", f"{PUBLIC}/{page['slug']}?locale={language}", 200, actor=None)
            self.assertEqual(set(public), {"slug", "kind", "title", "summary", "body", "sources", "published_at"})
            self.assertEqual(public["sources"], [SOURCE])
            for field in ("title", "summary", "body"):
                self.assertEqual(public[field], self.payload()["content"][language][field])

    def test_new_draft_restore_and_withdrawal_preserve_history(self):
        page = self.create()
        published = self.change(self.change(page, "review"), "publish")
        draft = self.save(published, self.payload(suffix=" updated"))
        self.assertEqual((draft["version"], draft["draft_revision"], draft["reviewed_revision"],
                          draft["published_revision"]), (4, 2, None, 1))
        path = f"{PUBLIC}/{page['slug']}"
        self.assertEqual(self.request("GET", path, 200)["title"], "English title")
        self.change(draft, "publish", expected=409)
        restored = self.change(draft, "restore", revision=1)
        self.assertEqual((restored["draft_revision"], restored["reviewed_revision"], restored["published_revision"]),
                         (3, None, 1))
        self.assertEqual(restored["content"], page["content"])
        self.assertEqual(restored["sources"], page["sources"])
        self.assertEqual(restored["review_note"], PRIVATE_NOTE)
        self.change(draft, "unpublish", expected=409)
        withdrawn = self.change(restored, "unpublish")
        self.assertIsNone(withdrawn["published_revision"])
        self.assertEqual(self.request("GET", PUBLIC, 200)["total"], 0)
        self.request("GET", path, 404)
        history = self.request("GET", f"{ADMIN}/{page['id']}/revisions", 200)["items"]
        self.assertEqual([row["revision"] for row in history], [3, 2, 1])
        self.assertTrue(all(set(row) == {"revision", "saved_at"} for row in history))
        with self.engine.connect() as db:
            events = db.execute(text("SELECT version, revision, action FROM editorial_events ORDER BY version")).all()
        self.assertEqual(events, [(1, 1, "create"), (2, 1, "review"), (3, 1, "publish"),
                                  (4, 2, "draft"), (5, 3, "restore"), (6, 1, "unpublish")])

    def test_reserved_slugs_duplicates_and_published_pagination(self):
        self.assertEqual(len(RESERVED_SLUGS), 12)
        for slug in RESERVED_SLUGS:
            self.create(slug, expected=409)
            self.request("GET", f"{PUBLIC}/{slug}", 404)
        first = self.create("synthetic-first")
        second = self.create("synthetic-second", kind="neighborhood")
        self.create("synthetic-unpublished", kind="article")
        self.create(first["slug"], expected=409)
        self.change(self.change(first, "review"), "publish")
        self.change(self.change(second, "review"), "publish")
        for page, slug in ((1, second["slug"]), (2, first["slug"])):
            listing = self.request("GET", f"{PUBLIC}?locale=es&page={page}&page_size=1", 200)
            self.assertEqual(listing["total"], 2)
            self.assertEqual(len(listing["items"]), 1)
            card = listing["items"][0]
            self.assertEqual(set(card), {"slug", "kind", "title", "summary", "published_at"})
            self.assertEqual((card["slug"], card["title"]), (slug, "Título en español"))
        self.assertEqual(self.request("GET", f"{PUBLIC}?page=3&page_size=1", 200), {"items": [], "total": 2})
        self.assertEqual(len(self.request("GET", f"{ADMIN}?page=2&page_size=2", 200)["items"]), 1)

    def test_invalid_inputs_and_browser_mutation_fail_without_echoing_private_data(self):
        for bad in ("UPPERCASE", "double--hyphen", "x" * 81):
            self.create(bad, expected=422)
        for url in ("http://example.org", "https://user:PRIVATE_TOKEN@example.org", "https://example.org/\r\n",
                    "https://example.org\\@fake.invalid"):
            self.create(payload=self.payload(sources=[{"label": "Link", "url": url}]), expected=422)
        self.create(payload=self.payload(sources=[SOURCE] * 9), expected=422)
        self.create(payload=self.payload(spanish_body="PRIVATE_" + "x" * 12000), expected=422)
        self.create(payload=self.payload(spanish_body="Embedded\x00null"), expected=422)
        self.request("GET", PUBLIC + "?locale=fr", 422, actor=None)
        self.request("GET", PUBLIC + "?page=0", 422, actor=None)
        self.request("GET", PUBLIC + "?page_size=51", 422, actor=None)
        self.request("POST", ADMIN, 415, content=b"PRIVATE_PLAINTEXT")
        self.request("POST", ADMIN, 413, headers={"content-type": "application/json"},
                     content=b"x" * (routes.MAX_BODY + 1))
        self.create("cookie-protected")
        self.client.cookies.set("ildr_token", self.tokens["editor"])
        body = {"slug": "cookie-second", "kind": "article", **self.payload()}
        self.request("POST", ADMIN, 403, actor=None, data=body)
        self.request("POST", ADMIN, 403, actor=None, headers={"Origin": "https://unknown.invalid"}, data=body)
        self.request("POST", ADMIN, 201, actor=None, headers={"Origin": "http://testserver"}, data=body)
        with patch.object(routes.service, "public_list", side_effect=RuntimeError("PRIVATE_DB_ERROR")):
            self.request("GET", PUBLIC, 503, actor=None)

    def test_only_one_concurrent_editor_can_save_same_version(self):
        page = self.create()

        def update(actor):
            with TestClient(self.app) as client:
                return client.put(f"{ADMIN}/{page['id']}/draft",
                                  headers={"Authorization": "Bearer " + self.tokens[actor]},
                                  json={"expected_version": 1, **self.payload(suffix=f" {actor}")}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(update, ["editor", "reviewer"]))
        self.assertEqual(sorted(statuses), [200, 409])
        result = self.request("GET", f"{ADMIN}/{page['id']}", 200)
        self.assertEqual((result["version"], result["draft_revision"]), (2, 2))
        self.assertIn(result["content"]["en"]["title"], ("English title editor", "English title reviewer"))

    def test_migration_rls_and_append_only_data_are_enforced(self):
        page = self.create()
        with self.engine.connect() as db:
            for table in ("editorial_pages", "editorial_revisions", "editorial_events"):
                info = db.execute(text("SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(:table)"),
                                  {"table": table}).scalar_one()
                self.assertTrue(info)
                roles = db.execute(text("""
                    SELECT has_table_privilege(oid, :table, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role')
                """), {"table": table}).scalars()
                self.assertFalse(any(roles))
                public = db.execute(text("""
                    SELECT EXISTS (SELECT 1 FROM pg_class
                        CROSS JOIN LATERAL aclexplode(coalesce(relacl, acldefault('r', relowner))) AS acl
                        WHERE oid = to_regclass(:table) AND acl.grantee = 0)
                """), {"table": table}).scalar_one()
                self.assertFalse(public)
        for statement in ("UPDATE editorial_revisions SET review_note = 'forbidden' WHERE page_id = :id",
                          "DELETE FROM editorial_events WHERE page_id = :id"):
            with self.assertRaises(DBAPIError), self.engine.begin() as db:
                db.execute(text(statement), {"id": page["id"]})
        with self.engine.begin() as db:
            db.execute(text("DELETE FROM users WHERE id = :id"), {"id": self.ids["editor"]})
            revision = db.execute(text("SELECT saved_by, review_note FROM editorial_revisions")).one()
            event_actor = db.execute(text("SELECT actor_id FROM editorial_events")).scalar_one()
        self.assertEqual(revision, (None, PRIVATE_NOTE))
        self.assertIsNone(event_actor)


if __name__ == "__main__":
    unittest.main()
