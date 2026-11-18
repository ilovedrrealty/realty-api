"""Synthetic route/role/history/media contracts on a disposable loopback schema.

No production connections, listening server, account invitations or provider I/O.
"""
import hashlib
import importlib.util
import io
import os
import re
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from uuid import UUID, uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-network-tests-only",
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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import models
from database import Base, get_db
from models.user import User
from models.listing import Listing
from models.professional_profile import ProfessionalProfile
from models.property_network import NetworkProjectUpdate, NetworkMedia, NetworkPost, NetworkProjectComment
from routes import property_network, property_management
from services import property_network as service
from utils.auth import get_current_user
from utils import private_network_media as storage


def photo():
    output = io.BytesIO()
    Image.new("RGB", (32, 24), "blue").save(output, "PNG")
    return output.getvalue()


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class PropertyNetworkRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host != "127.0.0.1" or url.port != 55449 or url.database != "ildr_platform_test_20261025" or url.get_backend_name() != "postgresql":
            raise ValueError("Use the fixed disposable loopback Postgres only")
        cls.schema = "network_test_" + uuid4().hex
        cls.bootstrap = create_engine(url)
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.addClassCleanup(cls.cleanup_owned_schema)
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema})
        Base.metadata.create_all(cls.engine)
        # Install the actual new revision's append-only guards, not a mock of it.
        path = Path(__file__).resolve().parents[1] / "alembic/versions/d1e3f7a9b521_private_property_network.py"
        spec = importlib.util.spec_from_file_location("network_migration_test", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with cls.engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
        app = FastAPI()
        app.include_router(property_network.router)
        app.include_router(property_management.router)

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
        assert re.fullmatch(r"network_test_[0-9a-f]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
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
            for number, (key, role) in enumerate(fixtures, 1001):
                identity = uuid4()
                self.ids[key], self.codes[key] = identity, number
                db.add(User(id=identity, user_code=number, email=f"synthetic-{key}@example.com", display_name=key,
                            role=role, status="suspended" if key == "suspended" else "active", email_verified=key != "unverified"))
            db.flush()
            self.listing, self.other_listing = uuid4(), uuid4()
            for identity, owner in ((self.listing, "owner"), (self.other_listing, "other")):
                db.add(Listing(id=identity, title=f"Synthetic {owner} property", type="villa", transaction="rent", currency="USD",
                               price=1000, location="Cabarete", images=[], features=[], status="active",
                               owner_id=self.ids[owner], submitted_by=self.ids[owner], assigned_realtor_id=self.ids["manager"] if owner == "owner" else None))
            for _ in range(46):
                db.add(ProfessionalProfile(id=uuid4(), user_id=None, version=1,
                    draft_data={"public_contact_consent": False}, review_status="draft", listing_status="hidden"))
            db.commit()
        self.patches = []
        self.addCleanup(self.stop_patches)
        self.patched(httpx.HTTPTransport, "handle_request", side_effect=AssertionError("No external HTTP transport in tests"))
        self.patched(storage, "_get_client", side_effect=AssertionError("No real Supabase client in tests"))
        self.stored = {}
        self.patched(storage, "store_image", side_effect=lambda key, data: self.stored.__setitem__(key, data))
        self.patched(storage, "read_image", side_effect=lambda row: self.stored[row.object_key])

    def patched(self, target, name, **kwargs):
        patcher = patch.object(target, name, **kwargs)
        self.patches.append(patcher)
        return patcher.start()

    def stop_patches(self):
        for patcher in reversed(self.patches):
            patcher.stop()

    def request(self, method, path, role="owner", payload=None, **kwargs):
        headers = {"x-test-role": role, "Authorization": "Bearer synthetic-only"}
        return self.client.request(method, "/property-network" + path, headers=headers, json=payload, **kwargs)

    def project(self, role="owner", listing=None):
        response = self.request("POST", "/projects", role, {
            "listing_id": str(listing or self.listing), "title": "Synthetic pool repair", "summary": "Private progress only",
            "property_class": "house_villa", "service_category_ids": ["pool_service"],
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def assign(self, project, target, role="professional"):
        current = self.request("GET", f"/projects/{project['id']}").json()
        response = self.request("POST", f"/projects/{project['id']}/members", payload={
            "expected_version": current["version"], "user_code": self.codes[target], "role": role,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def profile(self, role="owner", **changes):
        current = self.request("GET", "/me/profile", role).json()
        payload = {"expected_version": current["version"], "display_name": role, "bio": "Synthetic biography",
                   "status_text": "Ready", "visibility": "members", "wall_policy": "approval_required", "comments_enabled": True, **changes}
        response = self.request("PUT", "/me/profile", role, payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def update(self, project, role="owner", **changes):
        payload = {"submission_id": str(uuid4()), "body": "Pool filter repaired", "occurred_on": service.today_dr().isoformat(), "media_ids": [], **changes}
        response = self.request("POST", f"/projects/{project['id']}/updates", role, payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json(), payload

    def upload(self, role="owner", project=None):
        data = {"project_id": project["id"]} if project else {}
        response = self.client.post("/property-network/media", headers={"x-test-role": role, "Authorization": "Bearer synthetic-only"},
                                    files={"file": ("private.png", photo(), "image/png")}, data=data)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertNotIn("object_key", response.text)
        return response.json()

    def test_owner_creates_project_but_outsider_cannot_create_or_read(self):
        project = self.project()
        self.assertEqual(project["progress"], {"total": 0, "completed": 0, "percent": None})
        self.assertEqual(self.request("GET", f"/projects/{project['id']}", "outsider").status_code, 404)
        response = self.request("POST", "/projects", "other", {
            "listing_id": str(self.listing), "title": "No access", "property_class": "house_villa", "service_category_ids": [],
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.request("GET", "/projects", "outsider").json()["items"], [])

    def test_assignment_uses_existing_verified_users_and_does_not_publish_imports(self):
        project = self.project()
        members = self.assign(project, "pro")
        self.assertIn({"user_code": self.codes["pro"], "display_name": "pro", "role": "professional", "active": True}, members)
        for target in ("unverified", "suspended"):
            current = self.request("GET", f"/projects/{project['id']}").json()
            self.assertEqual(self.request("POST", f"/projects/{project['id']}/members", payload={
                "expected_version": current["version"], "user_code": self.codes[target], "role": "professional",
            }).status_code, 404)
        with Session(self.engine) as db:
            self.assertEqual(db.query(User).count(), 9)
            self.assertEqual(db.query(ProfessionalProfile).count(), 46)
            self.assertTrue(all(row.user_id is None and row.listing_status == "hidden" and row.review_status == "draft"
                                and row.published_data is None and row.draft_data["public_contact_consent"] is False
                                for row in db.query(ProfessionalProfile)))

    def test_professional_can_report_progress_but_cannot_see_pm_financial_records(self):
        project = self.project()
        self.assign(project, "pro")
        self.update(project, "pro")
        self.assertEqual(self.request("GET", f"/projects/{project['id']}", "pro").json()["my_role"], "professional")
        result = self.client.get("/property-management/properties", headers={"x-test-role": "pro", "Authorization": "Bearer synthetic-only"})
        self.assertEqual(result.status_code, 403)
        result = self.request("GET", f"/projects/{project['id']}/members", "pro")
        self.assertNotIn("email", result.text)
        self.assertNotIn("phone", result.text)
        self.assertNotIn(str(self.ids["owner"]), result.text)

    def test_viewer_reads_but_cannot_write_or_assign_and_manager_cannot_assign(self):
        project = self.project()
        self.assign(project, "viewer", "viewer")
        self.assign(project, "manager", "manager")
        self.assertEqual(self.request("GET", f"/projects/{project['id']}", "viewer").status_code, 200)
        self.assertEqual(self.request("POST", f"/projects/{project['id']}/milestones", "viewer", {"title": "Denied"}).status_code, 403)
        current = self.request("GET", f"/projects/{project['id']}").json()
        self.assertEqual(self.request("POST", f"/projects/{project['id']}/members", "manager", {
            "expected_version": current["version"], "user_code": self.codes["outsider"], "role": "viewer",
        }).status_code, 403)

    def test_revocation_blocks_new_reads_media_and_writes_immediately(self):
        project = self.project()
        self.assign(project, "pro")
        image = self.upload("pro", project)
        self.update(project, "pro", media_ids=[image["id"]])
        current = self.request("GET", f"/projects/{project['id']}").json()
        revoked = self.request("POST", f"/projects/{project['id']}/members/{self.codes['pro']}/revoke", payload={"expected_version": current["version"]})
        self.assertEqual(revoked.status_code, 200)
        for path in (f"/projects/{project['id']}", f"/projects/{project['id']}/updates", f"/media/{image['id']}"):
            self.assertEqual(self.request("GET", path, "pro").status_code, 404)

    def test_milestones_derive_progress_and_stale_edits_refuse(self):
        project = self.project()
        milestones = []
        for title in ("Diagnose", "Repair"):
            result = self.request("POST", f"/projects/{project['id']}/milestones", payload={"title": title})
            self.assertEqual(result.status_code, 201)
            milestones.append(result.json())
        item = milestones[0]
        change = {"expected_version": 1, "title": item["title"], "due_date": None, "status": "completed"}
        self.assertEqual(self.request("PUT", f"/milestones/{item['id']}", payload=change).status_code, 200)
        self.assertEqual(self.request("PUT", f"/milestones/{item['id']}", payload=change).status_code, 409)
        self.assertEqual(self.request("GET", f"/projects/{project['id']}").json()["progress"], {"total": 2, "completed": 1, "percent": 50.0})
        self.assertEqual(self.request("GET", f"/projects/{project['id']}/updates").json()["total"], 3)

    def test_progress_and_photo_comments_are_idempotent_and_corrections_retain_original(self):
        project = self.project()
        image = self.upload(project=project)
        update, payload = self.update(project, media_ids=[image["id"]])
        self.assertEqual(self.request("POST", f"/projects/{project['id']}/updates", payload=payload).json()["id"], update["id"])
        correction, _ = self.update(project, correction_of=update["id"], body="Correction: filter inspected")
        self.assertEqual(correction["kind"], "correction")
        comment = {"submission_id": str(uuid4()), "body": "Photo checked", "media_id": image["id"]}
        first = self.request("POST", f"/updates/{update['id']}/comments", payload=comment)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["media_id"], image["id"])
        self.assertEqual(self.request("POST", f"/updates/{update['id']}/comments", payload=comment).json()["id"], first.json()["id"])
        with Session(self.engine) as db:
            self.assertEqual(db.query(NetworkProjectUpdate).count(), 2)
            self.assertEqual(db.get(NetworkProjectUpdate, UUID(update["id"])).body, payload["body"])
            self.assertEqual(db.query(NetworkProjectComment).count(), 1)

    def test_cross_project_correction_photo_attachment_and_future_dates_refuse(self):
        project = self.project()
        other = self.project()
        image = self.upload(project=other)
        old, _ = self.update(other)
        base = {"submission_id": str(uuid4()), "body": "Invalid reference", "occurred_on": service.today_dr().isoformat()}
        for changes in ({"correction_of": old["id"]}, {"media_ids": [image["id"]]}, {"occurred_on": (service.today_dr() + timedelta(days=1)).isoformat()}):
            self.assertEqual(self.request("POST", f"/projects/{project['id']}/updates", payload={**base, **changes}).status_code, 422)

    def test_history_is_scoped_and_has_no_raw_actor_or_financial_projection(self):
        project = self.project()
        self.assign(project, "pro")
        self.update(project)
        result = self.request("GET", f"/properties/{self.listing}/history", "pro")
        self.assertEqual(result.status_code, 200)
        self.assertIn("id", result.json()["items"][0])
        for field in ("actor_id", "author", "email", "phone", "ledger", "tenant", "amount"):
            self.assertNotIn(field, result.json()["items"][0])
        self.assertEqual(self.request("GET", f"/properties/{self.listing}/history", "outsider").status_code, 404)

    def test_actual_migration_blocks_history_update_and_delete(self):
        project = self.project()
        update, _ = self.update(project)
        for sql in ("UPDATE network_project_updates SET body='rewritten' WHERE id=:id", "DELETE FROM network_project_updates WHERE id=:id"):
            with self.assertRaises(IntegrityError):
                with self.engine.begin() as connection:
                    connection.execute(text(sql), {"id": update["id"]})

    def test_profile_defaults_private_and_get_does_not_create_it(self):
        own = self.request("GET", "/me/profile").json()
        self.assertEqual((own["version"], own["visibility"], own["wall_policy"]), (0, "private", "own_only"))
        self.assertEqual(self.request("GET", f"/profiles/{self.codes['owner']}", "outsider").status_code, 404)
        self.assertEqual(self.request("GET", "/profiles").json()["items"], [])
        self.assertEqual(self.request("POST", f"/profiles/{self.codes['owner']}/posts", payload={"submission_id": str(uuid4()), "body": "Unsaved"}).status_code, 409)

    def test_opted_in_profile_has_safe_projection_and_self_only_can_edit(self):
        self.profile()
        result = self.request("GET", f"/profiles/{self.codes['owner']}", "outsider")
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["can_edit"])
        self.assertFalse(self.request("GET", f"/profiles/{self.codes['owner']}", "admin").json()["can_edit"])
        for field in ("email", "phone", "password_hash", "draft_data", "user_id"):
            self.assertNotIn(field, result.json())

    def test_null_member_code_cannot_create_profile_or_poison_discovery(self):
        with Session(self.engine) as db:
            db.get(User, self.ids["owner"]).user_code = None
            db.commit()
        result = self.request("PUT", "/me/profile", payload={
            "expected_version": 0, "display_name": "No routable member code", "visibility": "members",
        })
        self.assertEqual(result.status_code, 404)
        self.assertEqual(self.request("GET", "/profiles", "outsider").json()["items"], [])
        with Session(self.engine) as db:
            self.assertEqual(db.execute(text("SELECT count(*) FROM social_network_profiles")).scalar_one(), 0)

    def test_cross_wall_post_requires_policy_and_owner_approval_before_other_readers(self):
        self.profile(wall_policy="own_only")
        payload = {"submission_id": str(uuid4()), "body": "Requested wall post"}
        self.assertEqual(self.request("POST", f"/profiles/{self.codes['owner']}/posts", "pro", payload).status_code, 403)
        self.profile()
        pending = self.request("POST", f"/profiles/{self.codes['owner']}/posts", "pro", payload)
        self.assertEqual(pending.status_code, 201)
        self.assertEqual(pending.json()["status"], "pending")
        self.assertEqual(self.request("GET", f"/profiles/{self.codes['owner']}/posts", "outsider").json()["items"], [])
        approved = self.request("POST", f"/posts/{pending.json()['id']}/moderate", payload={"action": "approve"})
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(self.request("GET", f"/profiles/{self.codes['owner']}/posts", "outsider").json()["total"], 1)

    def test_comments_disable_hide_and_block_controls_apply_to_new_reads(self):
        self.profile()
        image = self.upload()
        posted = self.request("POST", f"/profiles/{self.codes['owner']}/posts", payload={
            "submission_id": str(uuid4()), "body": "Own photo post", "media_ids": [image["id"]],
        }).json()
        comment = self.request("POST", f"/posts/{posted['id']}/comments", "pro", {
            "submission_id": str(uuid4()), "body": "Comment on photo", "media_id": image["id"],
        })
        self.assertEqual(comment.status_code, 201)
        self.assertEqual(self.request("POST", f"/comments/{comment.json()['id']}/hide", payload={}).status_code, 200)
        self.assertEqual(self.request("GET", f"/posts/{posted['id']}/comments", "outsider").json()["items"], [])
        self.profile(comments_enabled=False)
        self.assertEqual(self.request("POST", f"/posts/{posted['id']}/comments", "pro", {
            "submission_id": str(uuid4()), "body": "Disabled",
        }).status_code, 403)
        self.assertEqual(self.request("PUT", f"/me/blocks/{self.codes['pro']}", payload={"blocked": True}).status_code, 200)
        self.assertEqual(self.request("GET", f"/profiles/{self.codes['owner']}", "pro").status_code, 404)
        self.assertEqual(self.request("GET", f"/media/{image['id']}", "pro").status_code, 404)

    def test_private_media_unattached_owner_only_and_attached_readers_follow_context(self):
        image = self.upload()
        self.assertEqual(self.request("GET", f"/media/{image['id']}", "outsider").status_code, 404)
        self.profile()
        self.request("POST", f"/profiles/{self.codes['owner']}/posts", payload={
            "submission_id": str(uuid4()), "body": "Member photo", "media_ids": [image["id"]],
        })
        result = self.request("GET", f"/media/{image['id']}", "outsider")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertEqual(result.headers["content-type"], "image/webp")
        self.profile(visibility="private")
        self.assertEqual(self.request("GET", f"/media/{image['id']}", "outsider").status_code, 404)

    def test_project_media_cannot_be_attached_to_social_wall_or_foreign_user(self):
        project = self.project()
        image = self.upload(project=project)
        self.profile()
        result = self.request("POST", f"/profiles/{self.codes['owner']}/posts", payload={
            "submission_id": str(uuid4()), "body": "No public/private promotion", "media_ids": [image["id"]],
        })
        self.assertEqual(result.status_code, 422)
        self.assign(project, "pro")
        result = self.request("POST", f"/projects/{project['id']}/updates", "pro", {
            "submission_id": str(uuid4()), "body": "Not my upload", "occurred_on": service.today_dr().isoformat(), "media_ids": [image["id"]],
        })
        self.assertEqual(result.status_code, 422)

    def test_input_limits_forged_authors_cookie_origin_and_no_store_errors(self):
        self.assertEqual(self.client.get("/property-network/projects").status_code, 401)
        project = self.project()
        bad = self.request("POST", f"/projects/{project['id']}/updates", payload={
            "submission_id": str(uuid4()), "body": "Forged", "author_id": str(self.ids["other"]), "occurred_on": service.today_dr().isoformat(),
        })
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(bad.headers["cache-control"], "no-store")
        self.assertEqual(self.request("GET", "/projects?page=1001").status_code, 422)
        self.assertEqual(self.client.put("/property-network/me/profile", headers={"x-test-role": "owner", "cookie": "ildr_token=synthetic"}, json={}).status_code, 403)
        big = self.client.post("/property-network/projects", headers={"x-test-role": "owner", "Authorization": "Bearer synthetic", "content-type": "application/json"}, content=b" " * 65537)
        self.assertEqual(big.status_code, 413)


class PrivateImageContracts(unittest.TestCase):
    def test_reencode_strips_metadata_and_records_real_dimensions_and_digest(self):
        data, width, height = storage.prepare_image(photo())
        self.assertEqual((width, height), (32, 24))
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertFalse(image.getexif())

    def test_invalid_oversized_or_nonimage_uploads_refuse(self):
        for data, expected in ((b"not an image", 415), (b"x" * (storage.MAX_BYTES + 1), 413)):
            with self.subTest(expected=expected), self.assertRaises(HTTPException) as caught:
                storage.prepare_image(data)
            self.assertEqual(caught.exception.status_code, expected)

    def test_missing_or_public_bucket_fails_closed_without_creation(self):
        client = MagicMock()
        for metadata in ({"public": True}, {}, {"public": None}):
            client.storage.get_bucket.return_value = metadata
            with patch.object(storage, "_get_client", return_value=client), self.assertRaises(RuntimeError):
                storage.store_image("network/synthetic/file.webp", b"synthetic")
        client.storage.create_bucket.assert_not_called()
        client.storage.from_.assert_not_called()

    def test_private_download_hash_and_size_are_checked_without_returning_a_url(self):
        data = b"synthetic-image-bytes"
        row = SimpleNamespace(object_key="network/synthetic/file.webp", byte_count=len(data), sha256=hashlib.sha256(data).hexdigest())
        bucket = MagicMock()
        bucket.download.return_value = data
        with patch.object(storage, "_private_bucket", return_value=bucket):
            self.assertEqual(storage.read_image(row), data)
            bucket.download.return_value = b"wrong"
            with self.assertRaises(RuntimeError):
                storage.read_image(row)
        bucket.get_public_url.assert_not_called()
        bucket.create_signed_url.assert_not_called()


if __name__ == "__main__":
    unittest.main()
