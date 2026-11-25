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

    def test_contact_deep_link_is_admin_only_and_preserves_owner_records(self):
        self.role("admin")
        owner_entry = {**self.entry, "verification_status": "owner_source", "source_details": {
            "private_note": "Owner-provided context stays inside the private Admin directory"}}
        imported = self.client.post("/admin/trade-contacts/import", headers=self.origin,
                                    json={"dry_run": False, "entries": [owner_entry]})
        self.assertEqual(imported.status_code, 200)
        secondary = {**owner_entry, "source_authority": "Second synthetic source",
                     "source_url": "https://example.org/office"}
        self.assertEqual(self.client.post("/admin/trade-contacts/import", headers=self.origin,
                                         json={"dry_run": False, "entries": [secondary]}).status_code, 200)
        listed = self.client.get("/admin/trade-contacts", params={"q": self.name}).json()["items"]
        self.assertEqual(len(listed), 1)
        expected = listed[0]
        path = "/admin/trade-contacts/" + expected["id"]

        def snapshot():
            with SessionLocal() as db:
                return db.execute(text("""SELECT to_jsonb(contact)::text,
                    (SELECT jsonb_agg(to_jsonb(source) ORDER BY source.id)::text
                     FROM trade_contact_sources source WHERE source.contact_id=contact.id)
                    FROM trade_contacts contact WHERE id=CAST(:identity AS uuid)"""),
                    {"identity": expected["id"]}).all()

        before = snapshot()
        app.dependency_overrides.pop(get_current_user, None)
        anonymous = self.client.get(path)
        self.assertIn(anonymous.status_code, (401, 403))
        self.assertEqual(anonymous.headers.get("cache-control"), "no-store")
        for role in ("buyer", "owner", "realtor", "renter", "professional", "seller"):
            self.role(role)
            denied = self.client.get(path)
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(denied.headers.get("cache-control"), "no-store")
            self.assertNotIn(self.name, denied.text)
            self.assertNotIn("private_note", denied.text)
        self.role("admin")
        detail = self.client.get(path)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.headers.get("cache-control"), "no-store")
        self.assertEqual(detail.headers.get("pragma"), "no-cache")
        self.assertEqual(detail.json(), expected)
        self.assertEqual(detail.json()["source_count"], 2)
        self.assertEqual(detail.json()["verification_status"], "owner_source")
        self.assertEqual(detail.json()["source_details"], owner_entry["source_details"])
        for suffix, status in ((str(uuid4()), 404), ("not-a-uuid", 422)):
            response = self.client.get("/admin/trade-contacts/" + suffix)
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.headers.get("cache-control"), "no-store")
            self.assertNotIn(self.name, response.text)
        self.assertEqual(snapshot(), before)

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

    def test_spreadsheet_refresh_preserves_notes_and_refuses_overflow_atomically(self):
        self.role("admin")
        path = "/admin/trade-contacts/import"
        original = {**self.entry, "verification_status": "owner_source", "source_details": {
            "private_note": "Keep the owner-provided context", "source_description": "Original source observation"}}
        full = {**original, "name": self.name + " full", "source_details": {
            f"note_{index}": f"Protected source note {index}" for index in range(20)}}

        def submit(entries, preserve=False):
            return self.client.post(path, headers=self.origin,
                                    json={"dry_run": False, "entries": entries, "preserve_source_details": preserve})

        def snapshot():
            with SessionLocal() as db:
                return db.execute(text("""SELECT c.name,c.details::text,s.evidence::text
                    FROM trade_contacts c JOIN trade_contact_sources s ON s.contact_id=c.id
                    WHERE c.name LIKE :name ORDER BY c.name,s.source_key"""), {"name": self.name + "%"}).all()

        def mapped(rows):
            data = ("name,email\n" + "\n".join(f"{name},{email}" for name, email in rows) + "\n").encode()
            table = self.client.post("/admin/trade-contacts/preview-file", headers=self.origin,
                                     files={"file": ("contacts.csv", data, "text/csv")}).json()
            return {"table": table, "mapping": table["suggested_mapping"], "defaults": {
                key: original[key] for key in ("category", "province", "source_authority", "source_url", "verified_on", "verification_status")}}

        self.assertEqual(submit([original, full]).status_code, 200)
        before = snapshot()
        body = mapped([(self.name, "refreshed@example.com")])
        preview = self.client.post("/admin/trade-contacts/preview-mapping", headers=self.origin, json=body).json()
        self.assertTrue(preview["preserve_source_details"])
        self.assertEqual(preview["result"], {"inserted": 0, "updated": 1, "unchanged": 0})
        self.assertEqual(snapshot(), before)
        self.assertEqual(submit(preview["entries"], True).json(), {"inserted": 0, "updated": 1, "unchanged": 0})
        after = snapshot()
        import json
        details, evidence = json.loads(after[0][1]), json.loads(after[0][2])
        self.assertEqual(details["email"], "refreshed@example.com")
        self.assertEqual(details["phone"], original["phone"])
        for notes in (details["source_details"], evidence["source_details"]):
            self.assertEqual(notes["private_note"], original["source_details"]["private_note"])
            self.assertEqual(notes["source_description"], original["source_details"]["source_description"])
            self.assertEqual(notes["import_file_sha256"], body["table"]["file_sha256"])
        self.assertEqual(after[1], before[1])
        replay = self.client.post("/admin/trade-contacts/preview-mapping", headers=self.origin, json=body).json()
        self.assertEqual(replay["result"], {"inserted": 0, "updated": 0, "unchanged": 1})
        self.assertEqual(submit(replay["entries"], True).json(), {"inserted": 0, "updated": 0, "unchanged": 1})
        self.assertEqual(snapshot(), after)

        overflow = mapped([(self.name, "changed@example.com"), (full["name"], "changed@example.com")])
        refused = self.client.post("/admin/trade-contacts/preview-mapping", headers=self.origin, json=overflow).json()
        self.assertEqual(refused["entries"], [])
        self.assertIsNone(refused["result"])
        self.assertEqual(refused["issues"], [{"row": 3, "field": "source_details", "code": "note_limit"}])
        self.assertEqual(snapshot(), after)

        # Simulate an authorized legacy JSON edit after the spreadsheet preview.
        # The final locked import must recheck notes and roll back an earlier
        # new contact in the same batch if the later refresh now overflows.
        concurrent = {**original, "email": "refreshed@example.com", "source_details": full["source_details"]}
        self.assertEqual(submit([concurrent]).status_code, 200)
        current = snapshot()
        new_entry = {**original, "name": self.name + " new", "source_details": None}
        self.assertEqual(submit([new_entry, *preview["entries"]], True).status_code, 422)
        self.assertEqual(snapshot(), current)

    def test_map_is_admin_only_and_shared_location_filters_validate(self):
        path = "/admin/trade-contacts/map"
        response = self.client.get(path)
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        for role in ("buyer", "owner", "realtor"):
            self.role(role)
            self.assertEqual(self.client.get(path).status_code, 403)
        self.role("admin")
        for endpoint in (path, "/admin/trade-contacts"):
            for params in ({"map_area": "DO:near-sosua"}, {"location_status": "unknown"},
                           {"subtype": "x" * 161}, {"service_area": "x" * 321}):
                response = self.client.get(endpoint, params=params)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.headers.get("cache-control"), "no-store")
        empty = self.client.get(path, params={"q": self.name}).json()
        self.assertEqual((empty["total"], empty["mapped_total"], empty["unmapped_total"], empty["areas"]), (0, 0, 0, []))
        self.assertTrue(empty["complete"])

    def test_map_and_list_share_subtype_service_area_and_provenance_filters_without_writes(self):
        self.role("admin")
        entries = [
            {**self.entry, "locality": "Sosúa", "subtype": "Hotel maintenance", "service_area": "Sosúa %_ service zone"},
            {**self.entry, "name": self.name + " other subtype", "locality": "Sosúa", "subtype": "Home maintenance", "service_area": "Sosúa %_ service zone"},
            {**self.entry, "name": self.name + " wildcard decoy", "locality": "Sosúa", "subtype": "Hotel maintenance", "service_area": "Sosúa other service zone"},
        ]
        response = self.client.post("/admin/trade-contacts/import", headers=self.origin,
                                    json={"dry_run": False, "entries": entries})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["inserted"], 3)

        def snapshot():
            with SessionLocal() as db:
                return db.execute(text("""SELECT to_jsonb(contact)::text,
                    (SELECT jsonb_agg(to_jsonb(source) ORDER BY source.id)::text
                     FROM trade_contact_sources source WHERE source.contact_id=contact.id)
                    FROM trade_contacts contact WHERE name LIKE :prefix ORDER BY contact.id"""),
                    {"prefix": self.name + "%"}).all()

        before = snapshot()
        params = {"q": self.name, "category": "Construcción", "province": "PUERTO PLATA", "locality": "Sosúa",
                  "subtype": "HOTEL MAINTENANCE", "service_area": "%_", "verification_status": "public_source",
                  "contact_channel": "phone", "map_area": "DO:sosua"}
        listed = self.client.get("/admin/trade-contacts", params=params).json()
        response = self.client.get("/admin/trade-contacts/map", params=params)
        self.assertEqual(response.status_code, 200)
        mapped = response.json()
        self.assertEqual((listed["total"], mapped["total"], mapped["mapped_total"]), (1, 1, 1))
        self.assertEqual(mapped["areas"][0]["record_count"], 1)
        self.assertEqual(mapped["areas"][0]["purpose"], "record_area")
        for private in (self.name, "office@example.com", "source_details", "source_authority", "contact_name"):
            self.assertNotIn(private, response.text)
        options = self.client.get("/admin/trade-contacts/options").json()
        self.assertIn("Hotel maintenance", options["subtypes"])
        self.assertIn("Sosúa %_ service zone", options["service_areas"])
        self.assertEqual(snapshot(), before)

    def test_map_full_scope_exceeds_page_and_keeps_unknown_conflicting_locations_unmapped(self):
        self.role("admin")
        entries = [{**self.entry, "name": self.name + f" mapped {index}", "locality": "  SOsu\u0301a  "}
                   for index in range(51)]
        entries.extend([
            {**self.entry, "name": self.name + " fallback", "locality": "Uncatalogued quarter"},
            {**self.entry, "name": self.name + " conflict", "province": "Santiago", "locality": "Sosúa"},
            {**self.entry, "name": self.name + " partial", "province": "Puerto Plata resort"},
        ])
        response = self.client.post("/admin/trade-contacts/import", headers=self.origin,
                                    json={"dry_run": False, "entries": entries})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["inserted"], 54)
        listed = self.client.get("/admin/trade-contacts", params={"q": self.name}).json()
        self.assertEqual((listed["total"], len(listed["items"])), (54, 50))
        mapped = self.client.get("/admin/trade-contacts/map", params={"q": self.name}).json()
        self.assertEqual((mapped["total"], mapped["mapped_total"], mapped["unmapped_total"]), (54, 52, 2))
        self.assertEqual({area["area_key"]: area["record_count"] for area in mapped["areas"]},
                         {"DO:sosua": 51, "DO:puerto-plata": 1})
        for params, expected in (({"map_area": "DO:sosua"}, 51), ({"location_status": "unmapped"}, 2),
                                 ({"location_status": "mapped"}, 52), ({}, 54)):
            query = {"q": self.name, **params}
            listed = self.client.get("/admin/trade-contacts", params={**query, "limit": 1}).json()
            mapped = self.client.get("/admin/trade-contacts/map", params=query).json()
            self.assertEqual((listed["total"], mapped["total"]), (expected, expected))
            if params.get("location_status") == "unmapped":
                self.assertEqual(mapped["areas"], [])
            if params.get("map_area"):
                self.assertEqual([area["area_key"] for area in mapped["areas"]], ["DO:sosua"])
