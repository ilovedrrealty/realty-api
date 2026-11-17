"""Native PostgreSQL checks for the private directory and replay-safe imports."""

from datetime import date
import os
from types import SimpleNamespace
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from database import SessionLocal
from main import app
from routes.trade_contacts import router
from utils.auth import get_current_user


@unittest.skipUnless(os.environ.get("TRADE_CONTACT_FIXTURE") == "yes", "requires isolated PostgreSQL")
class TradeContactsTest(unittest.TestCase):
    def setUp(self):
        self.name = f"Synthetic contractor {uuid4()}"
        self.origin = {"Origin": "http://localhost:5174"}
        self.entry = {
            "name": self.name, "category": "Construcción", "province": "Puerto Plata",
            "phone": "+1 809 555 0100", "email": "office@example.com",
            "source_authority": "Synthetic source", "source_url": "https://example.com/office",
            "verified_on": date.today().isoformat(), "verification_status": "public_source",
        }
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.pop(get_current_user, None)
        self.client.close()

    def role(self, role):
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role=role)

    def test_private_routes_and_direct_data_api(self):
        self.assertTrue(router.routes)
        response = self.client.get("/admin/trade-contacts")
        self.assertIn(response.status_code, [401, 403])
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.role("owner")
        self.assertEqual(self.client.get("/admin/trade-contacts").status_code, 403)
        response = self.client.post("/admin/trade-contacts/import", headers=self.origin,
                                    json={"dry_run": False, "entries": [self.entry]})
        self.assertEqual(response.status_code, 403)
        self.role("admin")
        self.assertEqual(self.client.post("/admin/trade-contacts/import", json={"dry_run": False, "entries": [self.entry]}).status_code, 403)
        with SessionLocal() as db:
            rows = db.execute(text("""SELECT relname, relrowsecurity,
                (SELECT bool_or(has_table_privilege(role.oid, rel.oid, 'SELECT,INSERT,UPDATE,DELETE'))
                 FROM pg_roles role WHERE role.rolname IN ('anon', 'authenticated', 'service_role'))
                FROM pg_class rel JOIN pg_namespace ns ON ns.oid=rel.relnamespace
                WHERE ns.nspname='public' AND relname IN ('trade_contacts', 'trade_contact_sources')
                ORDER BY relname""")).all()
        self.assertEqual(rows, [("trade_contact_sources", True, False), ("trade_contacts", True, False)])

    def test_import_preview_replay_update_and_search(self):
        self.role("admin")
        path = "/admin/trade-contacts/import"

        def submit(dry_run, *entries):
            response = self.client.post(path, headers=self.origin, json={"dry_run": dry_run, "entries": entries})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers.get("cache-control"), "no-store")
            return response.json()

        def search():
            response = self.client.get("/admin/trade-contacts", params={"q": self.name, "province": "Puerto Plata"})
            self.assertEqual(response.status_code, 200)
            return response.json()

        self.assertEqual(submit(True, self.entry), {"inserted": 1, "updated": 0, "unchanged": 0})
        self.assertEqual(search()["total"], 0)
        self.assertEqual(submit(False, self.entry)["inserted"], 1)
        self.assertEqual(submit(False, self.entry)["unchanged"], 1)
        secondary = {**self.entry, "phone": None, "website": "https://example.org", "source_authority": "Second directory",
                     "source_url": "https://example.org/record"}
        self.assertEqual(submit(False, secondary)["updated"], 1)
        self.assertEqual(submit(False, secondary)["unchanged"], 1)
        result = search()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["source_count"], 2)
        self.assertEqual(result["items"][0]["phone"], self.entry["phone"])
        self.assertEqual(result["items"][0]["website"], secondary["website"])
        same_phone_other_legal_entity = {**self.entry, "name": self.name + " distinct"}
        self.assertEqual(submit(False, same_phone_other_legal_entity)["inserted"], 1)
        self.assertEqual(search()["total"], 2)

    def test_validates_entire_batch_before_writing(self):
        self.role("admin")
        malformed = {**self.entry, "source_url": "javascript:alert(1)"}
        response = self.client.post("/admin/trade-contacts/import", headers=self.origin,
                                    json={"dry_run": False, "entries": [self.entry, malformed]})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("javascript:", response.text)
        self.assertEqual(self.client.get("/admin/trade-contacts", params={"q": self.name}).json()["total"], 0)
