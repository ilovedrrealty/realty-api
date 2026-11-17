"""Floorplan publication and byte integrity on an isolated PostgreSQL fixture."""

import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import io
import os
from pathlib import Path
import re
import unittest
from uuid import UUID, uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt
from PIL import Image
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-floor-plan-tests-only",
                  GHL_ENABLED="false", SUPABASE_URL="", SUPABASE_SERVICE_KEY="")

from config import settings
from database import Base, get_db
from models.listing import Listing
from models.listing_floor_plan import ListingFloorPlan
from models.user import User
from routes import listing_floor_plans as routes


MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/86fc1b2e4a79_private_listing_floor_plans.py"


def plan_image(color="white"):
    output = io.BytesIO()
    with Image.new("RGB", (28, 20), color) as image:
        image.save(output, format="PNG")
    return output.getvalue()


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires disposable loopback PostgreSQL")
class ListingFloorPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.get_backend_name() != "postgresql" or url.host != "127.0.0.1" or url.port != 55449:
            raise ValueError("Use the isolated loopback PostgreSQL fixture")
        cls.bootstrap = create_engine(url)
        cls.addClassCleanup(cls.bootstrap.dispose)
        cls.schema = "floor_plan_test_" + uuid4().hex
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.addClassCleanup(cls.remove_schema)
        cls.engine = create_engine(url, connect_args={
            "options": f"-csearch_path={cls.schema} -clock_timeout=5000 -cstatement_timeout=10000",
        })
        cls.addClassCleanup(cls.engine.dispose)
        Base.metadata.create_all(cls.engine, tables=[Base.metadata.tables[name] for name in ("users", "listings")])
        spec = spec_from_file_location(MIGRATION.stem, MIGRATION)
        migration = module_from_spec(spec)
        spec.loader.exec_module(migration)
        with cls.engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()

        def local_db():
            with Session(cls.engine) as session:
                yield session

        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(app)
        cls.addClassCleanup(cls.client.close)

    @classmethod
    def remove_schema(cls):
        assert re.fullmatch(r"floor_plan_test_[0-9a-f]{32}", cls.schema)
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))

    def setUp(self):
        self.tokens, self.ids = {}, {}
        with Session(self.engine) as db:
            for role in ("admin", "owner"):
                account = User(email=f"{uuid4()}@example.invalid", role=role, email_verified=True,
                               status="active", display_name="Synthetic floorplan actor")
                db.add(account)
                db.flush()
                self.ids[role] = account.id
                self.tokens[role] = jwt.encode({"sub": str(account.id)}, settings.jwt_secret, algorithm="HS256")
            for name, status in (("live", "active"), ("unpublished", "draft")):
                listing = Listing(title="Synthetic floorplan listing", type="villa", transaction="sale", price=100,
                                  location="Cabarete", status=status, submitted_by=self.ids["admin"],
                                  owner_id=self.ids["owner"])
                db.add(listing)
                db.flush()
                self.ids[name] = listing.id
            db.commit()
        self.public = f"/listings/{self.ids['live']}/floor-plan"
        self.admin = f"/admin{self.public}"

    def call(self, method, path, status=200, actor=None, **fields):
        headers = fields.pop("headers", {})
        if actor:
            headers = {**headers, "Authorization": f"Bearer {self.tokens[actor]}"}
        response = self.client.request(method, path, headers=headers, **fields)
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        return response

    def stage(self, payload, current=None, *, path=None, actor="admin", status=200, mime="image/png"):
        params = {"replaces": current} if current else None
        return self.call("PUT", (path or self.admin) + "/draft", status, actor,
                         params=params, content=payload, headers={"Content-Type": mime}).json()

    def publish(self, draft_id, prior=None, *, status=200, path=None, permission=True):
        return self.call("POST", (path or self.admin) + "/publish", status, "admin", json={
            "draft_id": draft_id, "expected_published_id": prior, "owner_permission_confirmed": permission,
        }).json()

    def test_only_exact_owner_approved_current_plan_is_public(self):
        self.call("GET", self.public, 404)
        self.call("GET", self.admin, 401)
        self.call("GET", self.admin, 403, "owner")
        data = plan_image()
        self.stage(data, actor="owner", status=403)
        draft = self.stage(data)["draft"]
        content_url = f"/{draft['id']}/content"
        self.assertEqual(set(draft), {"id", "content_type", "byte_count", "sha256", "created_at"})
        self.assertEqual(draft["sha256"], hashlib.sha256(data).hexdigest())
        self.call("GET", self.public + content_url, 404)
        self.call("GET", self.admin + "/draft" + content_url, 401)
        self.assertEqual(self.call("GET", self.admin + "/draft" + content_url, actor="admin").content, data)
        self.publish(draft["id"], status=422, permission=False)
        self.publish(draft["id"], status=422, permission=1)
        self.call("GET", self.public, 404)
        self.assertEqual(self.publish(draft["id"]), {"draft": None, "published": draft})
        self.assertEqual(self.call("GET", self.public).json(), draft)
        published = self.call("GET", self.public + content_url)
        self.assertEqual(published.content, data)
        self.assertEqual(published.headers["Content-Type"], "image/png")
        self.assertEqual(published.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(published.headers["Content-Disposition"], 'inline; filename="floor-plan.png"')
        self.call("GET", self.admin + "/draft" + content_url, 404, "admin")

    def test_staging_preserves_published_bytes_and_stale_actions_cannot_replace_them(self):
        original = self.stage(plan_image())["draft"]
        self.publish(original["id"])
        new_data = plan_image("black")
        current = self.stage(new_data)["draft"]
        self.assertEqual(self.stage(new_data)["draft"], current)
        self.stage(plan_image("green"), status=409)
        self.assertEqual(self.call("GET", self.public).json(), original)
        self.publish(current["id"], status=409)
        self.publish(str(uuid4()), original["id"], status=409)
        replacement = self.stage(plan_image("green"), current["id"])["draft"]
        self.call("GET", f"{self.admin}/draft/{current['id']}/content", 404, "admin")
        self.assertEqual(self.publish(replacement["id"], original["id"])["published"], replacement)
        self.call("GET", f"{self.public}/{original['id']}/content", 404)
        next_draft = self.stage(new_data)["draft"]
        self.call("POST", self.admin + "/withdraw", 409, "admin", json={"published_id": original["id"]})
        withdrawn = self.call("POST", self.admin + "/withdraw", actor="admin", json={"published_id": replacement["id"]})
        self.assertEqual(withdrawn.json(), {"published": None, "draft": next_draft})
        self.call("GET", f"{self.public}/{replacement['id']}/content", 404)

    def test_approval_requires_an_active_listing_and_valid_bytes_and_is_scoped(self):
        other = f"/admin/listings/{self.ids['unpublished']}/floor-plan"
        draft = self.stage(plan_image(), path=other)["draft"]
        self.publish(draft["id"], path=other, status=409)
        self.publish(draft["id"], status=409)
        self.call("GET", self.public, 404)
        self.stage(b"not a floor plan", status=422)
        self.stage(plan_image(), mime="text/html", status=415)
        self.stage(b"x" * (routes.MAX_DOCUMENT_BYTES + 1), status=413)
        self.call("PUT", self.admin + "/draft", 403, "admin", content=plan_image(),
                  headers={"Content-Type": "image/png", "Origin": "https://other.example.invalid"})
        active = self.stage(plan_image())["draft"]
        self.publish(active["id"])
        with Session(self.engine) as db:
            db.get(Listing, self.ids["live"]).status = "archived"
            db.commit()
        self.call("GET", self.public, 404)
        self.call("GET", f"{self.public}/{active['id']}/content", 404)

    def test_binary_rows_stay_private_and_download_refuses_a_digest_mismatch(self):
        asset = self.stage(plan_image())["draft"]
        self.publish(asset["id"])
        with Session(self.engine) as db:
            result = db.execute(text("""SELECT c.relrowsecurity, pg_typeof(f.payload)::text
                FROM listing_floor_plans f CROSS JOIN pg_class c
                WHERE f.id = :asset AND c.oid = to_regclass('listing_floor_plans')"""),
                {"asset": asset["id"]}).one()
            self.assertEqual(result, (True, "bytea"))
            plan = db.get(ListingFloorPlan, UUID(asset["id"]))
            plan.sha256 = "0" * 64
            db.commit()
        response = self.call("GET", f"{self.public}/{asset['id']}/content", 503)
        self.assertNotIn(b"\x89PNG", response.content)
