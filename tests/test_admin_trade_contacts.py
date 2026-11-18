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

    def test_spreadsheet_preview_mapping_errors_and_confirmed_replay(self):
        upload = "/admin/trade-contacts/preview-file"
        data = f'Nombre,Correo\n{self.name},office@example.com\n'.encode()
        self.role("owner")
        self.assertEqual(self.client.post(upload, headers=self.origin, files={"file": ("contacts.csv", data)}).status_code, 403)
        self.role("admin")
        self.assertEqual(self.client.post(upload, files={"file": ("contacts.csv", data)}).status_code, 403)
        response = self.client.post(upload, headers=self.origin, files={"file": ("contacts.csv", data, "text/csv")})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        table = response.json()
        self.assertEqual(table["suggested_mapping"], {"name": 0, "email": 1})
        body = {"table": table, "mapping": table["suggested_mapping"], "defaults": {
            key: self.entry[key] for key in ("category", "source_authority", "source_url", "verified_on", "verification_status")}}
        preview = self.client.post("/admin/trade-contacts/preview-mapping", headers=self.origin, json=body)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.headers["cache-control"], "no-store")
        report = preview.json()
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["result"], {"inserted": 1, "updated": 0, "unchanged": 0})
        self.assertEqual(self.client.get("/admin/trade-contacts", params={"q": self.name}).json()["total"], 0)
        self.assertEqual(report["entries"][0]["source_details"]["import_file_sha256"], table["file_sha256"])
        for expected in ({"inserted": 1, "updated": 0, "unchanged": 0}, {"inserted": 0, "updated": 0, "unchanged": 1}):
            saved = self.client.post("/admin/trade-contacts/import", headers=self.origin,
                                     json={"dry_run": False, "entries": report["entries"]})
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.json(), expected)
        self.assertEqual(self.client.get("/admin/trade-contacts", params={"q": self.name}).json()["items"][0]["source_count"], 1)
        invalid = {**body, "table": {**table, "rows": [{"row_number": 3, "values": [self.name + " invalid", "not-an-email"]}]},
                   "defaults": {key: value for key, value in body["defaults"].items() if key != "source_url"}}
        errors = self.client.post("/admin/trade-contacts/preview-mapping", headers=self.origin, json=invalid).json()
        self.assertEqual(errors["entries"], [])
        self.assertIsNone(errors["result"])
        self.assertEqual({(issue["row"], issue["field"]) for issue in errors["issues"]}, {(3, "email"), (3, "source_url")})
        self.assertNotIn("not-an-email", str(errors))
        self.assertEqual(self.client.get("/admin/trade-contacts", params={"q": self.name}).json()["total"], 1)

    def test_structured_filters_compose_and_private_choices_do_not_mutate(self):
        self.role("admin")
        town = self.name + " locality"
        entries = [{**self.entry, "locality": town},
                   {**self.entry, "name": self.name + " website", "locality": town, "phone": None, "email": None,
                    "website": "https://example.com", "verification_status": "owner_source"},
                   {**self.entry, "name": self.name + " no contact", "phone": None, "email": None}]
        self.assertEqual(self.client.post("/admin/trade-contacts/import", headers=self.origin,
                                         json={"dry_run": False, "entries": entries}).status_code, 200)
        params = {"q": self.name, "category": self.entry["category"], "province": "PUERTO PLATA", "locality": town,
                  "verification_status": "public_source", "contact_channel": "phone"}
        self.assertEqual(self.client.get("/admin/trade-contacts", params=params).json()["total"], 1)
        self.assertEqual(self.client.get("/admin/trade-contacts", params={**params, "verification_status": "owner_source"}).json()["total"], 0)
        for channel in ("website", "no_contact"):
            self.assertEqual(self.client.get("/admin/trade-contacts", params={"q": self.name, "contact_channel": channel}).json()["total"], 1)
        choices = self.client.get("/admin/trade-contacts/options")
        self.assertEqual(choices.headers["cache-control"], "no-store")
        self.assertIn(town, choices.json()["localities"])
        self.role("buyer")
        self.assertEqual(self.client.get("/admin/trade-contacts/options").status_code, 403)
