"""Private registry contracts on one owned disposable loopback schema.

Synthetic identities only; no listening server, production data or provider I/O.
"""
import importlib.util
import io
import os
import re
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-registry-tests-only",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
                  SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")

import httpx
from PIL import Image
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
import models
from database import Base, get_db
from models.user import User
from models.listing import Listing
from models.professional_profile import ProfessionalProfile
from models.property_network import NetworkProperty, NetworkProject, NetworkAudit
from routes import property_network
from schemas.property_network import ProjectCreate, RegistryEdit
from services import property_network as service
from utils.auth import get_current_user
from utils import private_network_media as storage


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class PrivateRegistryRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host != "127.0.0.1" or url.port != 55449 or url.database != "ildr_platform_test_20261025" or url.get_backend_name() != "postgresql":
            raise ValueError("Use the fixed disposable loopback Postgres only")
        cls.schema = "registry_test_" + uuid4().hex
        cls.bootstrap = create_engine(url)
        cls.addClassCleanup(cls.cleanup_owned_schema)
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema + " -clock_timeout=5000 -cstatement_timeout=10000"})
        Base.metadata.create_all(cls.engine)
        for name in ("d1e3f7a9b521_private_property_network.py", "f3a5b9c1d723_private_property_registry.py"):
            path = Path(__file__).resolve().parents[1] / "alembic/versions" / name
            spec = importlib.util.spec_from_file_location("registry_route_" + name[:-3], path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            with cls.engine.begin() as connection:
                migration.op = Operations(MigrationContext.configure(connection))
                migration.upgrade()
        app = FastAPI()
        app.include_router(property_network.router)

        def local_db():
            with Session(cls.engine) as db:
                yield db

        def identity(request: Request, db=Depends(get_db)):
            key = request.headers.get("x-test-role")
            if key not in cls.ids:
                raise HTTPException(401, "Synthetic authentication required")
            user = db.get(User, cls.ids[key])
            if user.status != "active" or not user.email_verified:
                raise HTTPException(403, "Account unavailable")
            return user

        app.dependency_overrides[get_db] = local_db
        app.dependency_overrides[get_current_user] = identity
        cls.client = TestClient(app)
        cls.old_limiter = property_network.limiter.enabled
        property_network.limiter.enabled = False

    @classmethod
    def cleanup_owned_schema(cls):
        if hasattr(cls, "client"):
            cls.client.close()
        if hasattr(cls, "old_limiter"):
            property_network.limiter.enabled = cls.old_limiter
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        assert re.fullmatch(r"registry_test_[0-9a-f]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE'))
        finally:
            cls.bootstrap.dispose()

    def setUp(self):
        with self.engine.begin() as connection:
            names = ",".join(f'"{self.schema}"."{table.name}"' for table in Base.metadata.sorted_tables)
            connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        self.__class__.ids, self.codes = {}, {}
        with Session(self.engine) as db:
            fixtures = [("admin", "admin"), ("owner", "owner"), ("other", "owner"), ("manager", "realtor"),
                        ("pro", "buyer"), ("viewer", "buyer"), ("outsider", "buyer"), ("suspended", "buyer"), ("unverified", "buyer")]
            for code, (key, role) in enumerate(fixtures, 2001):
                self.ids[key], self.codes[key] = uuid4(), code
                db.add(User(id=self.ids[key], user_code=code, email=f"registry-{key}@example.com", display_name=key,
                            role=role, status="suspended" if key == "suspended" else "active", email_verified=key != "unverified"))
            db.flush()
            self.listing = uuid4()
            db.add(Listing(id=self.listing, title="Synthetic public listing", type="villa", transaction="rent", currency="USD",
                           price=1000, location="Synthetic town", images=[], features=[], status="active",
                           owner_id=self.ids["owner"], submitted_by=self.ids["owner"]))
            for _ in range(46):
                db.add(ProfessionalProfile(id=uuid4(), user_id=None, version=1, draft_data={"public_contact_consent": False},
                                           review_status="draft", listing_status="hidden"))
            db.commit()
        self.patchers = []
        self.addCleanup(self.stop_patches)
        self.patched(httpx.HTTPTransport, "handle_request", side_effect=AssertionError("No real HTTP in registry tests"))
        self.patched(storage, "_get_client", side_effect=AssertionError("No real Supabase client in registry tests"))
        self.stored = {}
        self.patched(storage, "store_image", side_effect=lambda key, data: self.stored.__setitem__(key, data))
        self.patched(storage, "read_image", side_effect=lambda row: self.stored[row.object_key])

    def patched(self, target, name, *args, **kwargs):
        item = patch.object(target, name, *args, **kwargs)
        self.patchers.append(item)
        return item.start()

    def stop_patches(self):
        for item in reversed(self.patchers):
            item.stop()

    def request(self, method, path, role="owner", payload=None):
        return self.client.request(method, "/property-network" + path,
            headers={"x-test-role": role, "Authorization": "Bearer synthetic-only"}, json=payload)

    def registry_body(self, **changes):
        return {"submission_id": str(uuid4()), "title": "Synthetic private home", "location": "Private district",
                "address": "Private address marker", "property_class": "house_villa", "relationship": "manager",
                "notes": "Private note marker", "authority_acknowledged": True, **changes}

    def registry(self, role="owner", **changes):
        body = self.registry_body(**changes)
        response = self.request("POST", "/registry", role, body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json(), body

    def edit(self, row, role="owner", **changes):
        body = {name: row[name] for name in ("title", "location", "address", "property_class", "relationship", "notes", "status")}
        body.update(expected_version=row["version"], **changes)
        result = self.request("PUT", "/registry/" + row["id"], role, body)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def project_body(self, row, **changes):
        return {"property_id": row["id"], "submission_id": str(uuid4()), "title": "Synthetic repairs",
                "summary": "Scoped work", "property_class": row["property_class"], "service_category_ids": ["pool_service"], **changes}

    def project(self, row, role="owner", **changes):
        body = self.project_body(row, **changes)
        result = self.request("POST", "/projects", role, body)
        self.assertEqual(result.status_code, 201, result.text)
        return result.json(), body

    def assign(self, project, target, role="professional"):
        current = self.request("GET", "/projects/" + project["id"]).json()
        result = self.request("POST", f"/projects/{project['id']}/members", payload={
            "expected_version": current["version"], "user_code": self.codes[target], "role": role})
        self.assertEqual(result.status_code, 200, result.text)

    def revoke(self, project, target):
        current = self.request("GET", "/projects/" + project["id"]).json()
        result = self.request("POST", f"/projects/{project['id']}/members/{self.codes[target]}/revoke",
                              payload={"expected_version": current["version"]})
        self.assertEqual(result.status_code, 200, result.text)

    def update(self, project, role="owner", media_ids=None):
        result = self.request("POST", f"/projects/{project['id']}/updates", role, {
            "submission_id": str(uuid4()), "body": "Synthetic completed work", "occurred_on": service.today_dr().isoformat(),
            "media_ids": media_ids or []})
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    def counts(self):
        with Session(self.engine) as db:
            return tuple(db.query(model).count() for model in (NetworkProperty, NetworkProject, NetworkAudit, Listing, User))

    def test_every_active_verified_role_can_create_only_own_private_record_without_publication(self):
        for role in ("pro", "owner", "manager", "admin"):
            with self.subTest(role=role):
                row, _ = self.registry(role)
                self.assertTrue(row["can_manage"] and row["can_create_project"])
                self.assertTrue(self.request("GET", "/registry", role).json()["can_create"])
                with Session(self.engine) as db:
                    self.assertEqual(db.get(NetworkProperty, UUID(row["id"])).custodian_id, self.ids[role])
        self.assertEqual(self.counts()[3:], (1, 9))
        with Session(self.engine) as db:
            self.assertEqual(db.get(User, self.ids["pro"]).role, "buyer")
            profiles = db.query(ProfessionalProfile).all()
            self.assertEqual(len(profiles), 46)
            self.assertTrue(all(p.user_id is None and p.published_data is None and p.listing_status == "hidden"
                                and p.review_status == "draft" and p.draft_data["public_contact_consent"] is False for p in profiles))

    def test_auth_authority_extra_fields_and_bounded_private_inputs_refuse_without_echo(self):
        self.assertEqual(self.request("GET", "/registry", "unknown").status_code, 401)
        for role in ("suspended", "unverified"):
            self.assertEqual(self.request("POST", "/registry", role, self.registry_body()).status_code, 403)
        bad = ({"authority_acknowledged": False}, {"authority_acknowledged": 1}, {"authority_acknowledged": "true"},
               {"custodian_id": str(self.ids["other"])}, {"author_id": str(self.ids["other"])}, {"location": " "},
               {"title": "x" * 181}, {"address": "x" * 501}, {"notes": "x" * 4001}, {"property_class": "made_up"})
        for change in bad:
            with self.subTest(fields=tuple(change)):
                result = self.request("POST", "/registry", payload=self.registry_body(**change))
                self.assertEqual(result.status_code, 422)
                self.assertEqual(result.json(), {"detail": "Check the network request fields"})
        self.assertEqual(self.counts()[:3], (0, 0, 0))

    def test_collection_detail_status_pagination_and_guessed_ids_are_custodian_scoped(self):
        row, _ = self.registry()
        other, _ = self.registry("other")
        self.edit(other, "other", status="archived")
        own = self.request("GET", "/registry").json()
        self.assertEqual((own["total"], own["page_size"], [r["id"] for r in own["items"]]), (1, 30, [row["id"]]))
        self.assertEqual(self.request("GET", "/registry?status=archived").json()["total"], 0)
        self.assertEqual(self.request("GET", "/registry?status=archived", "admin").json()["total"], 1)
        self.assertEqual(self.request("GET", "/registry", "admin").json()["total"], 2)
        self.assertEqual(self.request("GET", "/registry?page=2").json()["items"], [])
        self.assertEqual(self.request("GET", "/registry?status=invalid").status_code, 422)
        for identity in (other["id"], str(uuid4())):
            self.assertEqual(self.request("GET", "/registry/" + identity).status_code, 404)
        self.assertEqual(self.request("GET", "/registry/" + row["id"], "admin").status_code, 200)

    def test_original_create_fingerprint_survives_edits_archive_and_actor_scoped_resolution(self):
        row, body = self.registry(title="  Synthetic private home  ")
        first = self.counts()
        same = self.request("POST", "/registry", payload={**body, "title": body["title"].strip()})
        self.assertEqual(same.status_code, 201)
        self.assertEqual(same.json()["id"], row["id"])
        edited = self.edit(row, title="Changed later", property_class="office", status="archived")
        after_edit = self.counts()
        replay = self.request("POST", "/registry", payload=body)
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.json(), edited)
        self.assertEqual(self.counts(), after_edit)
        self.assertEqual(self.request("POST", "/registry", payload={**body, "title": "Different original"}).status_code, 409)
        resolver = "/registry/submissions/" + body["submission_id"]
        self.assertEqual(self.request("GET", resolver).json(), edited)
        self.assertEqual(self.request("GET", resolver, "admin").status_code, 404)
        self.assertEqual(self.request("GET", resolver, "other").status_code, 404)
        own_other = self.request("POST", "/registry", "other", body)
        self.assertEqual(own_other.status_code, 201)
        self.assertNotEqual(own_other.json()["id"], row["id"])
        self.assertEqual(first[:2], (1, 0))

    def test_version_conflict_strict_version_and_immutable_creation_identity(self):
        row, _ = self.registry()
        saved = self.edit(row, title="New title", relationship="seller")
        fields = {k: saved[k] for k in ("title", "location", "address", "property_class", "relationship", "notes", "status")}
        self.assertEqual(self.request("PUT", "/registry/" + row["id"], payload={**fields, "expected_version": row["version"]}).status_code, 409)
        self.assertEqual(self.request("PUT", "/registry/" + row["id"], payload={**fields, "expected_version": True}).status_code, 422)
        for column, value in (("custodian_id", self.ids["other"]), ("submission_id", uuid4()), ("creation_sha256", "f" * 64)):
            with self.subTest(column=column), self.assertRaises(DBAPIError):
                with self.engine.begin() as connection:
                    connection.execute(text(f"UPDATE network_properties SET {column}=:value WHERE id=:id"), {"value": value, "id": row["id"]})
        with self.assertRaises(DBAPIError), self.engine.begin() as connection:
            connection.execute(text("DELETE FROM network_properties WHERE id=:id"), {"id": row["id"]})
        self.assertEqual(self.request("GET", "/registry/" + row["id"]).json(), saved)

    def test_project_xor_required_key_owner_class_and_listing_compatibility(self):
        row, _ = self.registry("pro")
        body = self.project_body(row)
        for change in ({"listing_id": str(self.listing)}, {"property_id": None}, {"submission_id": None}):
            self.assertEqual(self.request("POST", "/projects", "pro", {**body, **change}).status_code, 422)
        self.assertEqual(self.request("POST", "/projects", "pro", {**body, "property_class": "office"}).status_code, 422)
        self.assertEqual(self.request("POST", "/projects", "owner", body).status_code, 404)
        project, _ = self.project(row, "admin")
        self.assertIsNone(project["listing_id"])
        self.assertEqual((project["property_id"], project["property_title"], project["listing_title"]), (row["id"], row["title"], row["title"]))
        with Session(self.engine) as db:
            stored = db.get(NetworkProject, UUID(project["id"]))
            self.assertEqual((stored.owner_id, stored.created_by_id), (self.ids["pro"], self.ids["admin"]))
        legacy = {"listing_id": str(self.listing), "title": "Legacy", "property_class": "house_villa", "service_category_ids": []}
        allowed = self.request("POST", "/projects", payload=legacy)
        self.assertEqual(allowed.status_code, 201, allowed.text)
        self.assertEqual(allowed.json()["listing_id"], str(self.listing))
        self.assertIsNone(allowed.json()["property_id"])
        self.assertEqual(self.request("POST", "/projects", "pro", legacy).status_code, 403)
        self.assertEqual(self.counts()[3], 1)

    def test_project_replay_uses_initiating_actor_and_original_request_after_archive(self):
        row, _ = self.registry()
        project, body = self.project(row, "admin")
        self.edit(row, property_class="office", status="archived")
        current = self.request("GET", "/projects/" + project["id"], "admin").json()
        edited = self.request("PUT", "/projects/" + project["id"], "admin", {
            "expected_version": current["version"], "title": "Later project title", "summary": "Later summary", "status": "completed"})
        self.assertEqual(edited.status_code, 200)
        baseline = self.counts()
        self.patched(service, "MAX_PROPERTY_PROJECTS", 0)
        replay = self.request("POST", "/projects", "admin", body)
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(replay.json()["id"], project["id"])
        self.assertEqual(replay.json()["property_class"], "house_villa")
        self.assertEqual(self.request("POST", "/projects", "admin", {**body, "title": "Changed original"}).status_code, 409)
        resolver = "/projects/submissions/" + body["submission_id"]
        self.assertEqual(self.request("GET", resolver, "admin").json()["id"], project["id"])
        self.assertEqual(self.request("GET", resolver).status_code, 404)
        self.assertEqual(self.counts(), baseline)
        # Removing the original Admin privilege does not resurrect access via its saved key.
        with Session(self.engine) as db:
            db.get(User, self.ids["admin"]).role = "buyer"
            db.commit()
        self.assertEqual(self.request("GET", resolver, "admin").status_code, 404)
        self.assertEqual(self.request("POST", "/projects", "admin", body).status_code, 404)

    def test_archive_retains_existing_work_and_admin_capability_matches_inactive_custodian(self):
        row, _ = self.registry()
        project, _ = self.project(row)
        archived = self.edit(row, status="archived")
        self.assertFalse(archived["can_create_project"])
        self.assertEqual(self.request("POST", "/projects", payload=self.project_body(row)).status_code, 409)
        self.assertEqual(self.request("GET", "/projects/" + project["id"]).status_code, 200)
        self.update(project)
        active = self.edit(archived, status="active")
        for field, value in (("status", "suspended"), ("email_verified", False)):
            with self.subTest(field=field):
                with Session(self.engine) as db:
                    owner = db.get(User, self.ids["owner"])
                    owner.status, owner.email_verified = "active", True
                    setattr(owner, field, value)
                    db.commit()
                view = self.request("GET", "/registry/" + row["id"], "admin").json()
                self.assertTrue(view["can_manage"])
                self.assertFalse(view["can_create_project"])
                self.assertEqual(self.request("POST", "/projects", "admin", self.project_body(active)).status_code, 409)

    def test_participant_history_is_intersection_without_private_dossier_and_revocation_is_immediate(self):
        row, _ = self.registry()
        first, _ = self.project(row)
        second, _ = self.project(row)
        self.assign(first, "pro")
        self.assign(second, "viewer", "viewer")
        self.update(first, "pro")
        self.update(second)
        for who, expected in (("owner", {first["id"], second["id"]}), ("admin", {first["id"], second["id"]}),
                              ("pro", {first["id"]}), ("viewer", {second["id"]})):
            result = self.request("GET", f"/registry/{row['id']}/history", who)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual({item["project_id"] for item in result.json()["items"]}, expected)
            self.assertEqual(set(result.json()["items"][0]), {"id", "project_id", "project_title", "service_category_ids", "project_status", "kind", "body", "occurred_on", "media_ids", "created_at"})
        for who in ("pro", "viewer"):
            self.assertEqual(self.request("GET", "/registry/" + row["id"], who).status_code, 404)
            self.assertEqual(self.request("GET", "/registry", who).json()["items"], [])
            view = self.request("GET", "/projects/" + (first if who == "pro" else second)["id"], who)
            for forbidden in ("Private address marker", "Private note marker", "Private district", "custodian_id", "creation_sha256", "relationship"):
                self.assertNotIn(forbidden, view.text)
        self.revoke(first, "pro")
        self.assertEqual(self.request("GET", f"/registry/{row['id']}/history", "pro").status_code, 404)
        self.assertEqual(self.request("GET", "/projects/" + first["id"], "pro").status_code, 404)
        with Session(self.engine) as db:
            db.get(User, self.ids["viewer"]).status = "suspended"
            db.commit()
        self.assertEqual(self.request("GET", f"/registry/{row['id']}/history", "viewer").status_code, 403)

    def test_empty_history_resolvers_and_gets_are_read_only_with_no_store_on_denials(self):
        row, body = self.registry()
        baseline = self.counts()
        paths = ("/registry", "/registry/" + row["id"], "/registry/submissions/" + body["submission_id"],
                 f"/registry/{row['id']}/history", "/projects?property_id=" + row["id"])
        for path in paths:
            result = self.request("GET", path)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertEqual(self.request("GET", f"/registry/{row['id']}/history").json()["total"], 0)
        for role in ("outsider", "viewer"):
            result = self.request("GET", f"/registry/{row['id']}/history", role)
            self.assertEqual(result.status_code, 404)
            self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertEqual(self.request("GET", "/registry/submissions/" + str(uuid4())).status_code, 404)
        self.assertEqual(self.request("GET", "/projects/submissions/" + str(uuid4())).status_code, 404)
        self.assertEqual(self.request("GET", f"/projects?property_id={row['id']}&listing_id={self.listing}").status_code, 422)
        self.assertEqual(self.counts(), baseline)

    def test_project_photos_comments_and_uploads_keep_project_acl_after_archive_and_revoke(self):
        row, _ = self.registry()
        first, _ = self.project(row)
        second, _ = self.project(row)
        self.assign(first, "pro")
        self.assign(second, "viewer", "viewer")
        output = io.BytesIO()
        Image.new("RGB", (24, 20), "blue").save(output, "PNG")
        photo = self.client.post("/property-network/media", headers={"x-test-role": "pro", "Authorization": "Bearer synthetic-only"},
                                 data={"project_id": first["id"]}, files={"file": ("synthetic.png", output.getvalue(), "image/png")})
        self.assertEqual(photo.status_code, 201, photo.text)
        image = photo.json()
        update = self.update(first, "pro", [image["id"]])
        comment = {"submission_id": str(uuid4()), "body": "Photo checked", "media_id": image["id"]}
        commented = self.request("POST", f"/updates/{update['id']}/comments", "pro", comment)
        self.assertEqual(commented.status_code, 201, commented.text)
        self.edit(row, status="archived")
        self.assertEqual(self.request("GET", "/media/" + image["id"], "pro").status_code, 200)
        self.assertEqual(self.request("GET", "/media/" + image["id"], "viewer").status_code, 404)
        foreign = self.request("POST", f"/projects/{second['id']}/updates", payload={
            "submission_id": str(uuid4()), "body": "Wrong photo", "occurred_on": service.today_dr().isoformat(), "media_ids": [image["id"]]})
        self.assertEqual(foreign.status_code, 422)
        self.revoke(first, "pro")
        self.assertEqual(self.request("GET", "/media/" + image["id"], "pro").status_code, 404)
        self.assertEqual(self.request("POST", f"/updates/{update['id']}/comments", "pro", {**comment, "submission_id": str(uuid4())}).status_code, 404)
        self.assertNotIn("object_key", photo.text)

    def test_json_origin_body_and_error_boundaries_do_not_mutate(self):
        body = self.registry_body()
        for origin in (None, "https://foreign.example.com"):
            headers = {"x-test-role": "owner", "Cookie": "ildr_token=synthetic"}
            if origin:
                headers["Origin"] = origin
            result = self.client.post("/property-network/registry", headers=headers, json=body)
            self.assertEqual(result.status_code, 403)
            self.assertEqual(result.headers["cache-control"], "no-store")
        result = self.client.post("/property-network/registry", headers={"x-test-role": "owner", "Authorization": "Bearer synthetic"}, content="{}")
        self.assertEqual(result.status_code, 415)
        result = self.request("POST", "/registry", payload=self.registry_body(notes="x" * 70000))
        self.assertEqual(result.status_code, 413)
        self.assertNotIn("Private address marker", result.text)
        self.assertEqual(self.counts()[:3], (0, 0, 0))

    def test_concurrent_identical_and_changed_property_requests_are_serialized_per_actor(self):
        def together(bodies):
            barrier = threading.Barrier(2, timeout=5)
            def create(body):
                barrier.wait()
                return self.request("POST", "/registry", payload=body)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(create, body) for body in bodies]
                return [future.result(timeout=10) for future in futures]
        body = self.registry_body()
        results = together([body, body])
        self.assertEqual([r.status_code for r in results], [201, 201])
        self.assertEqual(results[0].json()["id"], results[1].json()["id"])
        self.assertEqual(self.counts()[:3], (1, 0, 1))
        changed = self.registry_body()
        results = together([changed, {**changed, "title": "Competing original"}])
        self.assertEqual(sorted(r.status_code for r in results), [201, 409])
        self.assertEqual(self.counts()[:3], (2, 0, 2))

    def test_concurrent_quota_and_version_writes_do_not_overshoot_or_lose_updates(self):
        self.patched(service, "MAX_REGISTRY_PROPERTIES", 1)
        barrier = threading.Barrier(2, timeout=5)
        def create():
            body = self.registry_body()
            barrier.wait()
            return self.request("POST", "/registry", payload=body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(create) for _ in range(2)]
            results = [f.result(timeout=10) for f in results]
        self.assertEqual(sorted(r.status_code for r in results), [201, 409])
        row = next(r.json() for r in results if r.status_code == 201)
        fields = {k: row[k] for k in ("title", "location", "address", "property_class", "relationship", "notes", "status")}
        barrier = threading.Barrier(2, timeout=5)
        def edit(role):
            barrier.wait()
            return self.request("PUT", "/registry/" + row["id"], role, {**fields, "expected_version": 1, "title": role + " wins"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(edit, role) for role in ("owner", "admin")]
            results = [f.result(timeout=10) for f in futures]
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        final = self.request("GET", "/registry/" + row["id"]).json()
        self.assertEqual(final["version"], 2)
        self.assertEqual(final["title"], next(r.json()["title"] for r in results if r.status_code == 200))

    def test_archive_and_new_project_share_actual_row_lock_and_reload_stale_identity(self):
        row, _ = self.registry()
        body = ProjectCreate(**self.project_body(row))
        started, pid = threading.Event(), []
        def create():
            with Session(self.engine) as db:
                pid.append(db.execute(text("SELECT pg_backend_pid()")).scalar_one())
                admin = db.get(User, self.ids["admin"])
                started.set()
                try:
                    return service.create_project(db, admin, body)
                except HTTPException as exc:
                    db.rollback()
                    return exc.status_code
        with Session(self.engine) as owner_db:
            owner = owner_db.get(User, self.ids["owner"])
            service.registry_for(owner_db, owner, UUID(row["id"]), lock=True)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(create)
                try:
                    self.assertTrue(started.wait(3))
                    deadline, blocked = time.monotonic() + 3, False
                    while time.monotonic() < deadline:
                        with self.bootstrap.connect() as observe:
                            blocked = bool(observe.execute(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid[0]}).scalar_one())
                        if blocked:
                            break
                        time.sleep(0.01)
                    self.assertTrue(blocked, "New project must wait on the already-held registry row")
                    edit = RegistryEdit(**{k: row[k] for k in ("title", "location", "address", "property_class", "relationship", "notes")},
                                        status="archived", expected_version=row["version"])
                    service.edit_registry(owner_db, owner, UUID(row["id"]), edit)
                finally:
                    owner_db.rollback()  # Release on assertion/error as well as after committed archive.
                self.assertEqual(future.result(timeout=10), 409)
        self.assertEqual(self.counts()[1], 0)
        current = self.request("GET", "/registry/" + row["id"]).json()
        active = self.edit(current, status="active")
        with Session(self.engine) as stale:
            old = stale.get(NetworkProperty, UUID(row["id"]))
            self.assertEqual(old.property_class, "house_villa")
            self.edit(active, property_class="office")
            with self.assertRaises(HTTPException) as caught:
                service.create_project(stale, stale.get(User, self.ids["owner"]), ProjectCreate(**self.project_body(active)))
            self.assertEqual(caught.exception.status_code, 422)
            stale.rollback()

    def test_concurrent_private_project_key_and_property_quota_preserve_one_creation(self):
        row, _ = self.registry()
        self.patched(service, "MAX_PROPERTY_PROJECTS", 1)
        body = self.project_body(row)
        barrier = threading.Barrier(2, timeout=5)
        def create(role, request_body):
            barrier.wait()
            return self.request("POST", "/projects", role, request_body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create, "owner", body) for _ in range(2)]
            results = [f.result(timeout=10) for f in futures]
        self.assertEqual([r.status_code for r in results], [201, 201])
        self.assertEqual(results[0].json()["id"], results[1].json()["id"])
        self.assertEqual(self.counts()[1], 1)
        self.assertEqual(self.request("POST", "/projects", "admin", self.project_body(row)).status_code, 409)
        # Different initiating actors do not share the actor lock: the property
        # lock must independently serialize the per-property quota.
        other, _ = self.registry()
        barrier = threading.Barrier(2, timeout=5)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create, role, self.project_body(other)) for role in ("owner", "admin")]
            results = [f.result(timeout=10) for f in futures]
        self.assertEqual(sorted(r.status_code for r in results), [201, 409])
        self.assertEqual(self.counts()[1], 2)
