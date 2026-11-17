"""Development inventory contracts against an owned synthetic PostgreSQL schema."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from uuid import uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-development-test",
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
from models.listing import Listing
from models.user import User
from routes import development_inventory as routes


TODAY = date(2031, 2, 1)
CUTOFF = "2031-02-15"
APPROVED_FLOORPLAN = "https://example.org/approved-floorplan.pdf"
PRIVATE_FLOORPLAN = "https://example.org/PRIVATE_INVENTORY_FLOORPLAN.pdf"
SOURCE_REFERENCE = "PRIVATE_INVENTORY_SOURCE"
PRIVATE_NOTE = "PRIVATE_INVENTORY_NOTE"


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires owned loopback PostgreSQL")
class DevelopmentInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if (url.get_backend_name() != "postgresql" or url.host != "127.0.0.1" or
                url.port != 55449 or url.database != "ildr_platform_test_20261025"):
            raise ValueError("Use the fixed disposable loopback PostgreSQL database")
        cls.schema = "development_test_" + uuid4().hex
        cls.bootstrap = create_engine(url)
        cls.addClassCleanup(cls.cleanup_schema)
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        options = f"-csearch_path={cls.schema} -clock_timeout=5000 -cstatement_timeout=10000"
        cls.engine = create_engine(url, connect_args={"options": options})
        Base.metadata.create_all(cls.engine, tables=(User.__table__, Listing.__table__))
        path = Path(__file__).resolve().parents[1] / "alembic/versions/fad71e03c6b9_private_development_inventory.py"
        spec = spec_from_file_location("development_revision", path)
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
        assert re.fullmatch(r"development_test_[0-9a-f]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE'))
        finally:
            cls.bootstrap.dispose()

    def setUp(self):
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        clock = patch.object(routes.service, "today_dr", return_value=TODAY)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.ids, self.tokens, self.listings = {}, {}, {}
        with Session(self.engine) as db:
            for name, role in (("editor", "admin"), ("reviewer", "admin"), ("buyer", "buyer"),
                               ("owner", "owner"), ("realtor", "realtor"),
                               ("unverified", "admin"), ("suspended", "admin")):
                identifier = uuid4()
                db.add(User(id=identifier, email=f"{identifier}@example.invalid", role=role,
                            display_name="Synthetic inventory actor",
                            status="suspended" if name == "suspended" else "active", email_verified=name != "unverified"))
                self.ids[name] = identifier
                self.tokens[name] = jwt.encode({"sub": str(identifier)}, settings.jwt_secret, algorithm="HS256")
            db.flush()
            for name, transaction, status in (
                ("sale", "sale", "active"), ("both", "both", "active"), ("rent", "rent", "active"),
                ("draft", "sale", "draft"), ("archived", "sale", "archived"),
            ):
                identifier = uuid4()
                db.add(Listing(id=identifier, title=f"Synthetic {name}", type="condo", transaction=transaction,
                               price=10, location="Synthetic area", status=status,
                               submitted_by=self.ids["owner"], owner_id=self.ids["owner"]))
                self.listings[name] = identifier
            db.commit()

    @staticmethod
    def payload(*, suffix="", cutoff=CUTOFF, verified="2031-01-25"):
        return {
            "project": {
                "name_en": "Synthetic development" + suffix, "name_es": "Proyecto de prueba" + suffix,
                "summary_en": "Synthetic summary", "summary_es": "Resumen de prueba", "target_delivery": "2032-Q3",
                "phases": [{
                    "code": "A", "name_en": "Phase A", "name_es": "Fase A", "target_delivery": "2032-09",
                    "units": [
                        {"code": "A1", "floor": "Ground floor", "area_m2": "110.25", "price": "199000.00",
                         "currency": "USD", "status": "available", "floor_plan_url": APPROVED_FLOORPLAN,
                         "floor_plan_approved": True},
                        {"code": "A2", "floor": "Penthouse", "area_m2": "155", "price": "9500000", "currency": "DOP",
                         "status": "reserved", "floor_plan_url": PRIVATE_FLOORPLAN, "floor_plan_approved": False},
                        {"code": "A3", "status": "sold"}, {"code": "A4", "status": "unconfirmed"},
                    ],
                }],
                "payment_stages": [
                    {"label_en": "First stage", "label_es": "Primera etapa", "due_en": "Upon signing",
                     "due_es": "A la firma", "percent": "20.25"},
                    {"label_en": "Second stage", "label_es": "Segunda etapa", "due_date": "2031-08-15",
                     "amount": "5000.00", "currency": "USD"},
                    {"label_en": "Final stage", "label_es": "Última etapa", "due_en": "Delivery", "due_es": "Entrega"},
                ],
                "updates": [{"date": "2031-01-25", "title_en": "Inventory checked",
                             "title_es": "Inventario revisado", "text_en": "Synthetic update",
                             "text_es": "Actualización de prueba"}],
            },
            "evidence": {"source_reference": SOURCE_REFERENCE, "private_note": PRIVATE_NOTE, "verified_on": verified,
                         "availability_valid_through": cutoff},
        }

    def path(self, listing="sale", *, public=False):
        identifier = self.listings.get(listing, listing)
        return f"/listings/{identifier}/development" if public else f"/admin/developments/{identifier}"

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
        if path.startswith("/listings/") or expected >= 400:
            self.assertNotIn("PRIVATE_INVENTORY", result.text)
        return result.json()

    def save(self, version=0, *, actor="editor", listing="sale", payload=None, expected=200):
        return self.request("PUT", self.path(listing) + "/draft", expected, actor=actor,
                            data={"expected_version": version, **(self.payload() if payload is None else payload)})

    def action(self, state, name, *, listing="sale", actor="reviewer", revision=None, expected=200):
        body = {"expected_version": state["version"]}
        if name != "withdraw":
            body["revision"] = state["draft_revision"] if revision is None else revision
        return self.request("POST", self.path(listing) + f"/{name}", expected, actor=actor, data=body)

    def publish(self, *, listing="sale"):
        saved = self.save(listing=listing)
        reviewed = self.action(saved, "review", listing=listing)
        return self.action(reviewed, "publish", listing=listing)

    def test_private_admin_scope_and_empty_version_zero(self):
        for listing in ("sale", "both"):
            state = self.request("GET", self.path(listing), 200)
            self.assertEqual(state, {"listing_id": str(self.listings[listing]), "version": 0, "draft_revision": None,
                                     "reviewed_revision": None, "published_revision": None, "draft": None,
                                     "published": None})
            self.request("GET", self.path(listing, public=True), 404, actor=None)
        for listing in ("rent", "draft", "archived", uuid4()):
            self.request("GET", self.path(listing), 404)
            self.save(listing=listing, expected=404)
        for actor in (None, "buyer", "owner", "realtor", "unverified", "suspended"):
            status = 401 if actor is None else 403
            self.request("GET", self.path(), status, actor=actor)
            self.save(actor=actor, expected=status)
            self.request("GET", self.path() + "/revisions/1", status, actor=actor)

    def test_review_requires_exact_complete_sourced_draft(self):
        payload = self.payload()
        payload["evidence"] = {}
        draft = self.save(payload=payload)
        self.action(draft, "review", expected=422)
        self.action(draft, "publish", expected=409)
        future = self.save(draft["version"], payload=self.payload(verified="2031-02-03"))
        self.action(future, "review", expected=422)
        incomplete = self.payload()
        incomplete["project"]["phases"] = []
        next_draft = self.save(future["version"], payload=incomplete)
        self.action(next_draft, "review", expected=422)
        self.request("GET", self.path(public=True), 404, actor=None)
        complete = self.save(next_draft["version"])
        self.action(complete, "review", revision=1, expected=409)
        reviewed = self.action(complete, "review")
        self.action(reviewed, "review", expected=409)
        self.action(complete, "publish", expected=409)
        self.assertEqual(self.action(reviewed, "publish")["published_revision"], 4)

    def test_old_public_revision_stays_visible_until_new_review_and_explicit_withdrawal(self):
        initial = self.publish()
        public = self.request("GET", self.path(public=True), 200, actor=None)
        self.assertEqual(public, initial["published"])
        self.assertEqual(set(public), {"listing_id", "revision", "published_at", "verified_on",
                                       "availability_valid_through", "availability_current", "project"})
        units = public["project"]["phases"][0]["units"]
        self.assertEqual(units[0], {"code": "A1", "floor": "Ground floor", "area_m2": "110.25",
                                    "price": "199000.00", "currency": "USD", "status": "available",
                                    "floor_plan_url": APPROVED_FLOORPLAN})
        self.assertIsNone(units[1]["floor_plan_url"])
        self.assertEqual(public["project"]["payment_stages"][1]["amount"], "5000.00")
        self.assertEqual(public["project"]["updates"][0]["date"], "2031-01-25")
        next_draft = self.save(initial["version"], payload=self.payload(suffix=" revised"))
        self.assertEqual(next_draft["published"], public)
        self.assertIsNone(next_draft["reviewed_revision"])
        self.assertEqual(self.request("GET", self.path(public=True), 200, actor=None), public)
        archived_revision = self.request("GET", self.path() + "/revisions/1", 200)
        self.assertEqual(archived_revision["project"], initial["draft"]["project"])
        self.assertEqual(archived_revision["evidence"], initial["draft"]["evidence"])
        revised = self.action(self.action(next_draft, "review"), "publish")
        self.assertTrue(revised["published"]["project"]["name_en"].endswith("revised"))
        self.action(initial, "withdraw", expected=409)
        withdrawn = self.action(revised, "withdraw")
        self.assertIsNone(withdrawn["published"])
        self.request("GET", self.path(public=True), 404, actor=None)
        self.assertEqual(self.request("GET", self.path() + "/revisions/1", 200), archived_revision)
        self.request("GET", self.path("both") + "/revisions/1", 404)

    def test_dominican_business_date_expiry_replaces_all_dated_statuses(self):
        self.publish()
        self.clock.return_value = date.fromisoformat(CUTOFF)
        last_day = self.request("GET", self.path(public=True), 200, actor=None)
        self.assertTrue(last_day["availability_current"])
        self.assertEqual([item["status"] for item in last_day["project"]["phases"][0]["units"]],
                         ["available", "reserved", "sold", "unconfirmed"])
        self.clock.return_value = date(2031, 2, 16)
        stale = self.request("GET", self.path(public=True), 200, actor=None)
        self.assertFalse(stale["availability_current"])
        self.assertEqual((stale["verified_on"], stale["availability_valid_through"]), ("2031-01-25", CUTOFF))
        self.assertEqual([unit["status"] for unit in stale["project"]["phases"][0]["units"]], ["unconfirmed"] * 4)
        state = self.request("GET", self.path(), 200)
        self.assertEqual(state["published"], stale)
        self.assertEqual(state["draft"]["project"]["phases"][0]["units"][0]["status"], "available")

    def test_wire_validation_link_safety_and_browser_mutation_guards(self):
        version = 0
        for target in ("2032", "2032-06", "2032-06-10", "2032-Q4"):
            payload = self.payload()
            payload["project"]["target_delivery"] = target
            saved = self.save(listing="both", version=version, payload=payload)
            self.assertEqual(saved["draft"]["project"]["target_delivery"], target)
            version = saved["version"]
        for target in ("0000", "2032-Q5", "2032-13", "2032-02-30", "2032-2"):
            payload = self.payload()
            payload["project"]["target_delivery"] = target
            self.save(payload=payload, expected=422)
        for url in ("http://example.org/a", "https://person:PRIVATE_INVENTORY@example.org/doc",
                    "https://example.org/?token=PRIVATE_INVENTORY", "https://example.org/?X-Amz-Signature=PRIVATE_INVENTORY",
                    "https://example.org/?resourcekey=PRIVATE_INVENTORY", "https://example.org/?sig=PRIVATE_INVENTORY"):
            payload = self.payload()
            payload["project"]["phases"][0]["units"][0]["floor_plan_url"] = url
            self.save(payload=payload, expected=422)
        payload = self.payload()
        phase = payload["project"]["phases"][0]
        phase["units"][1]["code"] = phase["units"][0]["code"].lower()
        self.save(payload=payload, expected=422)
        payload = self.payload()
        payload["project"]["payment_stages"][0].update(amount="100", currency="USD")
        self.save(payload=payload, expected=422)
        self.save(payload=self.payload(cutoff="2031-01-01"), expected=422)
        self.request("PUT", self.path() + "/draft", 415, content=b"PRIVATE_INVENTORY")
        self.request("PUT", self.path() + "/draft", 413, headers={"content-type": "application/json"},
                     content=b"x" * (routes.MAX_BODY + 1))
        self.client.cookies.set("ildr_token", self.tokens["editor"])
        self.request("PUT", self.path() + "/draft", 403, actor=None, headers={"Origin": "https://unknown.invalid"},
                     data={"expected_version": 0, **self.payload()})
        with patch.object(routes.service, "public_read", side_effect=RuntimeError("PRIVATE_INVENTORY_DB")):
            self.request("GET", self.path(public=True), 503)

    def test_only_one_concurrent_editor_can_create_or_save_the_same_version(self):
        def update(actor, version):
            with TestClient(self.app) as client:
                return client.put(self.path() + "/draft", headers={"Authorization": "Bearer " + self.tokens[actor]},
                                  json={"expected_version": version, **self.payload(suffix=f" {actor}")}).status_code

        for version in (0, 1):
            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = list(pool.map(lambda actor: update(actor, version), ("editor", "reviewer")))
            self.assertEqual(sorted(statuses), [200, 409])
        state = self.request("GET", self.path(), 200)
        self.assertEqual((state["version"], state["draft_revision"]), (2, 2))

    def test_current_listing_status_and_transaction_gate_all_public_and_admin_reads(self):
        self.publish()
        self.publish(listing="both")
        with Session(self.engine) as db:
            db.get(Listing, self.listings["sale"]).status = "archived"
            db.get(Listing, self.listings["both"]).transaction = "rent"
            db.commit()
        for listing in ("sale", "both"):
            self.request("GET", self.path(listing, public=True), 404, actor=None)
            self.request("GET", self.path(listing), 404)
            self.request("GET", self.path(listing) + "/revisions/1", 404)

    def test_rls_append_only_revisions_and_events_survive_attribution_deletion(self):
        self.publish()
        with self.engine.connect() as db:
            for table in ("development_inventories", "development_inventory_revisions", "development_inventory_events"):
                enabled = db.execute(text("SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(:table)"),
                                     {"table": table}).scalar_one()
                grants = db.execute(text("""
                    SELECT has_table_privilege(oid, :table, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role')
                """), {"table": table}).scalars()
                self.assertTrue(enabled)
                self.assertFalse(any(grants))
        for statement in (
            "UPDATE development_inventory_revisions SET snapshot = '{}' WHERE listing_id = :id",
            "DELETE FROM development_inventory_events WHERE listing_id = :id",
        ):
            with self.assertRaises(DBAPIError), self.engine.begin() as db:
                db.execute(text(statement), {"id": self.listings["sale"]})
        with self.engine.begin() as db:
            db.execute(text("DELETE FROM users WHERE id = :id"), {"id": self.ids["reviewer"]})
            events = db.execute(text("""
                SELECT version, revision, action, actor_id IS NULL FROM development_inventory_events
                WHERE listing_id = :id ORDER BY version
            """), {"id": self.listings["sale"]}).all()
        self.assertEqual(events, [(1, 1, "draft", False), (2, 1, "review", True), (3, 1, "publish", True)])
        self.assertEqual(self.request("GET", self.path(public=True), 200, actor=None)["revision"], 1)


if __name__ == "__main__":
    unittest.main()
