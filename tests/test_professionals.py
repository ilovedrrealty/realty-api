"""Actual JWT + disposable Postgres tests; no production services or customer data."""
import concurrent.futures
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock
from uuid import UUID, uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-professional-tests-only",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
                  SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")

import jwt
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from config import settings
from database import Base, get_db
import models
from models.professional_profile import ProfessionalProfile, ProfessionalProfileRevision
from models.user import User
from schemas.website_settings import WebsiteContentSettings
from routes import admin, auth, professionals
from utils.limiter import limiter
from utils import professional_photos


def draft(name="Synthetic professional", **changes):
    return {"business_name": name, "contact_name": "Synthetic contact", "category_ids": ["property_law"],
            "service_area_ids": ["north_coast"], "descriptions": {"en": "Synthetic public business description", "es": ""},
            "languages": ["en", "es"], "public_phone": "+18095550101", "public_email": "public@example.com",
            "public_whatsapp": "", "website_url": "https://example.com/", "photo_url": "", "public_contact_consent": True, **changes}


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires disposable loopback Postgres")
class ProfessionalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host not in ("127.0.0.1", "localhost", "::1") or url.get_backend_name() != "postgresql":
            raise ValueError("Tests require disposable loopback-only Postgres")
        cls.schema = "professional_test_" + uuid4().hex
        bootstrap = create_engine(url)
        with bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        bootstrap.dispose()
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema}, pool_size=8)
        Base.metadata.create_all(cls.engine)
        cls.app = FastAPI()
        cls.app.include_router(professionals.router)
        cls.app.include_router(auth.router)
        cls.app.include_router(admin.router)
        def local_db():
            with Session(cls.engine) as db:
                yield db
        cls.app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(cls.app)
        cls.original_limiter = limiter.enabled
        limiter.enabled = False
        cls.accounts = {}
        cls.headers = {}
        with Session(cls.engine) as db:
            for name, role, verified, status in [("buyer", "buyer", True, "active"), ("other", "buyer", True, "active"),
                ("owner", "owner", True, "active"), ("realtor", "realtor", True, "active"),
                ("admin", "admin", True, "active"), ("admin_b", "admin", True, "active"),
                ("unverified", "buyer", False, "active"), ("suspended", "buyer", True, "suspended")]:
                user = User(email=name + "-private@example.com", display_name="Synthetic " + name, role=role, email_verified=verified,
                            status=status, phone="+18095550999", paypal_email="private-payout@example.com", calendly_url="https://calendly.com/private")
                db.add(user)
                db.flush()
                cls.accounts[name] = user.id
                cls.headers[name] = {"Authorization": "Bearer " + jwt.encode({"sub": str(user.id), "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, settings.jwt_secret, algorithm="HS256")}
            db.commit()

    @classmethod
    def tearDownClass(cls):
        limiter.enabled = cls.original_limiter
        cls.client.close()
        with cls.engine.begin() as connection:
            connection.execute(text('SET search_path TO public'))
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.engine.dispose()

    def setUp(self):
        with Session(self.engine) as db:
            db.query(ProfessionalProfileRevision).delete()
            db.query(ProfessionalProfile).delete()
            for name in ("buyer", "other", "owner", "realtor", "admin", "admin_b"):
                user = db.get(User, self.accounts[name])
                user.status, user.email_verified = "active", True
            db.commit()

    def call(self, method, path, actor="buyer", body=None, status=200):
        response = self.client.request(method, path, headers=self.headers.get(actor, {}), json=body)
        self.assertEqual(response.status_code, status, response.text)
        if path.startswith(("/professional", "/admin/professionals")):
            self.assertEqual(response.headers.get("cache-control"), "no-store")
        return response.json()

    def save(self, data=None, actor="buyer", version=0, status=200):
        return self.call("PUT", "/professional-profile", actor, {"expected_version": version, "draft": data if data is not None else draft()}, status)

    def submit(self, own, actor="buyer", status=200):
        return self.call("POST", "/professional-profile/submit", actor, {"expected_version": own["version"]}, status)

    def moderate(self, own, action="approve", actor="admin", status=200, note="Synthetic private review"):
        return self.call("POST", f"/admin/professionals/{own['id']}/{action}", actor, {"expected_version": own["version"], "note": note}, status)

    def publish(self, data=None, actor="buyer"):
        return self.moderate(self.submit(self.save(data, actor), actor))

    def managed(self, data=None, actor="admin", status=201):
        return self.call("POST", "/admin/professionals", actor, {"draft": data if data is not None else draft()}, status)

    def managed_save(self, own, data=None, actor="admin_b", status=200):
        return self.call("PUT", f"/admin/professionals/{own['id']}", actor,
                         {"expected_version": own["version"], "draft": data if data is not None else own["draft"]}, status)

    def managed_submit(self, own, actor="admin", status=200):
        return self.call("POST", f"/admin/professionals/{own['id']}/submit", actor, {"expected_version": own["version"]}, status)

    def test_options_missing_profile_and_real_roles_unchanged_for_every_account_type(self):
        options = self.call("GET", "/professionals/options", actor=None)
        self.assertGreaterEqual(len(options["categories"]), 20)
        for kind in ("categories", "areas", "languages"):
            self.assertEqual(len(options[kind]), len({item["id"] for item in options[kind]}))
            self.assertTrue(all(item["label"]["en"] and item["label"]["es"] for item in options[kind]))
        for actor, role in (("buyer", "buyer"), ("owner", "owner"), ("realtor", "realtor"), ("admin", "admin"), ("admin_b", "admin")):
            self.assertIsNone(self.call("GET", "/professional-profile", actor))
            own = self.save(actor=actor)
            self.assertEqual(self.call("GET", "/auth/me", actor)["role"], role)
            self.assertEqual(own["listing_status"], "hidden")
            self.assertEqual(own["review_status"], "draft")
        self.assertEqual(self.call("GET", "/professional-profile", "other"), None)

    def test_incomplete_draft_is_private_and_submission_requires_explicit_public_contacts(self):
        own = self.save({})
        self.submit(own, status=422)
        self.assertEqual(own["draft"]["public_email"], "")
        self.assertEqual(own["draft"]["public_phone"], "")
        self.assertEqual(self.call("GET", "/professionals", actor=None)["items"], [])
        own = self.save(draft(public_contact_consent=False), version=own["version"])
        self.submit(own, status=422)
        self.save(draft(public_contact_consent="yes"), actor="other", status=422)
        self.save(draft(public_contact_consent=True, public_phone="", public_email="", public_whatsapp=""), version=own["version"])
        own = self.call("GET", "/professional-profile")
        self.submit(own, status=422)

    def test_moderation_auth_and_self_ownership_never_grant_realtor_or_admin_rights(self):
        pending = self.submit(self.save())
        for actor in (None, "other", "owner", "realtor", "unverified", "suspended"):
            self.moderate(pending, actor=actor, status=401 if actor is None else 403)
            self.call("GET", "/admin/professionals", actor, status=401 if actor is None else 403)
        for actor in (None, "unverified", "suspended"):
            self.call("GET", "/professional-profile", actor, status=401 if actor is None else 403)
        self.save({**draft(), "user_id": str(self.accounts["other"])}, actor="other", status=422)
        self.call("PUT", "/professional-profile", "other", {"expected_version": 0, "draft": draft(), "listing_status": "published"}, 422)
        self.moderate(pending, action="reject", note="  ", status=422)
        approved = self.moderate(pending)
        self.assertEqual(approved["owner"]["role"], "buyer")
        self.call("GET", "/admin/agents", "buyer", status=403)

    def test_public_response_is_whitelisted_and_pending_edits_never_change_public_snapshot(self):
        approved = self.publish()
        public = self.call("GET", f"/professionals/{approved['id']}", actor=None)
        self.assertEqual(set(public), {"id", *professionals.PUBLIC_FIELDS, "approved_at"})
        self.assertNotIn("private@example.com", str(public))
        for key in ("owner", "user_id", "draft", "version", "review_note", "paypal_email", "calendly_url", "public_contact_consent"):
            self.assertNotIn(key, public)
        changed = self.save(draft("Unreviewed name", category_ids=["electrical"]), version=approved["version"])
        self.assertEqual(changed["listing_status"], "published")
        pending = self.submit(changed)
        self.assertEqual(self.call("GET", f"/professionals/{approved['id']}", actor=None)["business_name"], "Synthetic professional")
        self.assertEqual(self.call("GET", "/professionals?category=electrical", actor=None)["total"], 0)
        rejected = self.moderate(pending, action="reject")
        self.assertEqual(rejected["review_status"], "rejected")
        self.assertEqual(self.call("GET", f"/professionals/{approved['id']}", actor=None)["business_name"], "Synthetic professional")

    def test_suspension_survives_edits_approval_and_withdraw_then_reinstate_stays_hidden(self):
        approved = self.publish()
        pending = self.submit(self.save(draft("Pending change"), version=approved["version"]))
        suspended = self.moderate(pending, "suspend")
        self.call("GET", f"/professionals/{approved['id']}", actor=None, status=404)
        reviewed = self.moderate(suspended)
        self.assertEqual(reviewed["listing_status"], "suspended")
        own = self.call("POST", "/professional-profile/withdraw", body={"expected_version": reviewed["version"]})
        self.assertEqual(own["listing_status"], "suspended")
        own = self.save(draft("Still private"), version=own["version"])
        self.assertEqual(own["listing_status"], "suspended")
        self.submit(own, status=403)
        reinstated = self.moderate(own, "reinstate")
        self.assertEqual(reinstated["listing_status"], "hidden")
        self.assertEqual(reinstated["review_status"], "draft")
        self.moderate(reinstated, status=409)
        self.assertEqual(self.call("GET", "/professionals", actor=None)["total"], 0)
        self.assertEqual(self.moderate(self.submit(reinstated))["listing_status"], "published")

    def test_withdraw_cancels_pending_review_and_consent_revocation_hides_immediately(self):
        approved = self.publish()
        own = self.save(draft(public_contact_consent=False), version=approved["version"])
        self.assertEqual(own["listing_status"], "hidden")
        self.assertIsNotNone(own["published_profile"])
        self.call("GET", f"/professionals/{approved['id']}", actor=None, status=404)
        self.submit(own, status=422)
        pending = self.submit(self.save(draft(), version=own["version"]))
        withdrawn = self.call("POST", "/professional-profile/withdraw", body={"expected_version": pending["version"]})
        self.assertEqual(withdrawn["review_status"], "draft")
        self.assertEqual(withdrawn["listing_status"], "hidden")
        self.moderate(withdrawn, status=409)

    def test_directory_filters_are_bounded_literal_and_use_approved_area_coverage(self):
        self.publish(draft("Alpha law", service_area_ids=["north_coast"]))
        self.publish(draft("Beta electrical", category_ids=["electrical"], service_area_ids=["nationwide"]), "other")
        page = self.call("GET", "/professionals?area=sosua&limit=1", actor=None)
        self.assertEqual((page["total"], len(page["items"])), (2, 1))
        self.assertEqual(self.call("GET", "/professionals?category=property_law&area=sosua", actor=None)["total"], 1)
        self.assertEqual(self.call("GET", "/professionals?area=nationwide", actor=None)["total"], 1)
        self.assertEqual(self.call("GET", "/professionals?category=property_law&category=electrical", actor=None)["total"], 2)
        self.assertEqual(self.call("GET", "/professionals?q=%25", actor=None)["total"], 0)
        self.assertEqual(self.call("GET", "/professionals?q=Alpha", actor=None)["total"], 1)
        for path in ("/professionals?category=unknown", "/professionals?area=unknown", "/professionals?limit=51", "/professionals?offset=10001", "/professionals?language=unknown"):
            self.call("GET", path, actor=None, status=422)

    def test_owner_inactivation_or_loss_of_verification_hides_existing_public_profile(self):
        approved = self.publish()
        for field, value in (("status", "suspended"), ("email_verified", False)):
            with Session(self.engine) as db:
                owner = db.get(User, self.accounts["buyer"])
                owner.status, owner.email_verified = "active", True
                setattr(owner, field, value)
                db.commit()
            self.call("GET", f"/professionals/{approved['id']}", actor=None, status=404)

    def test_stale_versions_and_concurrent_save_reject_lost_updates(self):
        own = self.save()
        def update(name):
            return self.client.put("/professional-profile", headers=self.headers["buyer"], json={"expected_version": own["version"], "draft": draft(name)})
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(update, ("First concurrent", "Second concurrent")))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        latest = self.call("GET", "/professional-profile")
        self.assertEqual(latest["version"], own["version"] + 1)
        pending = self.submit(latest)
        self.moderate(latest, status=409)
        approved = self.moderate(pending)
        self.moderate(pending, action="reject", status=409)
        self.assertEqual(len(approved["history"]), approved["version"])

    def test_concurrent_first_create_has_one_profile_and_one_validation_conflict(self):
        def create(_):
            return self.client.put("/professional-profile", headers=self.headers["buyer"], json={"expected_version": 0, "draft": draft()})
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(create, (1, 2)))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        with Session(self.engine) as db:
            self.assertEqual(db.query(ProfessionalProfile).count(), 1)
            self.assertEqual(db.query(ProfessionalProfileRevision).count(), 1)

    def test_draft_validation_and_body_limit_block_unsupported_fields_urls_and_photos(self):
        for data in (draft(license_reference="unsupported"), draft(category_ids=["unknown"]), draft(category_ids=["electrical", "electrical"]),
                     draft(website_url="javascript:alert(1)"), draft(website_url="https://127.0.0.1/"), draft(website_url="https://example.com/?token=hidden"),
                     draft(photo_url=f"https://example.com/{uuid4()}.webp")):
            self.save(data, status=422)
        self.call("PUT", "/professional-profile", body={"expected_version": 0, "draft": draft(), "padding": "x" * (129 * 1024)}, status=413)

    def test_photo_validation_reencodes_to_immutable_business_asset_without_avatar_deletion(self):
        image = io.BytesIO()
        Image.new("RGB", (24, 16), "red").save(image, "PNG")
        original_url = settings.supabase_url
        settings.supabase_url = "https://synthetic-professional.supabase.co"
        self.addCleanup(setattr, settings, "supabase_url", original_url)
        storage = MagicMock()
        storage.storage.from_.return_value.get_public_url.side_effect = lambda path: f"{settings.supabase_url}/storage/v1/object/public/{settings.storage_bucket}/{path}"
        with patch.object(professional_photos, "_get_client", return_value=storage):
            urls = []
            for _ in range(2):
                response = self.client.post("/professional-profile/photo", headers=self.headers["buyer"], files={"file": ("photo.png", image.getvalue(), "image/png")})
                self.assertEqual(response.status_code, 200, response.text)
                urls.append(response.json()["photo_url"])
            self.assertNotEqual(*urls)
            self.assertIsNone(self.call("GET", "/professional-profile"))
            uploads = storage.storage.from_.return_value.upload.call_args_list
            self.assertEqual(len(uploads), 2)
            for call in uploads:
                self.assertEqual(call.args[2], {"content-type": "image/webp", "upsert": "false"})
                self.assertEqual(Image.open(io.BytesIO(call.args[1])).format, "WEBP")
            storage.storage.from_.return_value.remove.assert_not_called()
            self.save(draft(photo_url=urls[0]))
            self.save(draft(photo_url=urls[0]), actor="other", status=422)
        for data in (b"not an image", b"x" * (5 * 1024 * 1024 + 1)):
            response = self.client.post("/professional-profile/photo", headers=self.headers["buyer"], files={"file": ("bad.jpg", data, "image/jpeg")})
            self.assertIn(response.status_code, (413, 415))
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_chunked_multipart_upload_without_content_length_has_a_hard_receive_cap(self):
        boundary = "synthetic-professional-upload"
        def chunks():
            yield f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="too-large.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()
            for _ in range(43):
                yield b"x" * 131072
            yield f"\r\n--{boundary}--\r\n".encode()
        response = self.client.post("/professional-profile/photo", headers={**self.headers["buyer"], "Content-Type": "multipart/form-data; boundary=" + boundary}, content=chunks())
        self.assertNotIn("content-length", response.request.headers)
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_migration_is_additive_idempotent_rls_protected_and_preserves_history_on_downgrade(self):
        approved = self.publish()
        path = Path(__file__).parents[1] / "alembic/versions/4614c6845ccd_add_moderated_professional_profiles.py"
        spec = importlib.util.spec_from_file_location("professional_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
                migration.downgrade()
            flags = dict(connection.execute(text("SELECT relname, relrowsecurity FROM pg_class JOIN pg_namespace ON pg_namespace.oid=pg_class.relnamespace WHERE nspname=:schema AND relname IN ('professional_profiles','professional_profile_revisions')"), {"schema": self.schema}).all())
            self.assertEqual(flags, {"professional_profiles": True, "professional_profile_revisions": True})
            self.assertEqual(connection.execute(text("SELECT count(*) FROM pg_policies WHERE schemaname=:schema AND tablename LIKE 'professional%'"), {"schema": self.schema}).scalar(), 0)
        detail = self.call("GET", f"/admin/professionals/{approved['id']}", "admin_b")
        self.assertEqual(len(detail["history"]), approved["version"])
        self.assertEqual(self.call("GET", "/auth/me")["role"], "buyer")

    def test_directory_copy_and_optional_menu_route_are_content_not_private_profiles(self):
        content = WebsiteContentSettings.model_validate({
            "translations": {"en": {"professionals": {"title": "DR Professionals"}}, "es": {"professionals": {"title": "Profesionales RD"}}},
            "navigation": [{"route": "professionals", "label": {"en": "Professionals", "es": "Profesionales"}, "enabled": True}],
        })
        self.assertEqual(content.navigation[0].route, "professionals")
        self.assertEqual(content.translations.en["professionals"]["title"], "DR Professionals")
        with self.assertRaises(ValueError):
            WebsiteContentSettings.model_validate({"professional_profiles": [draft()]})

    def test_admin_managed_creation_never_creates_accounts_or_sends_invitations(self):
        with Session(self.engine) as db:
            before = {str(user.id): user.role for user in db.query(User).all()}
        with patch("utils.email._send") as mail, patch("utils.ghl.create_contact") as crm:
            first = self.managed()
            second = self.managed(actor="admin_b")
            mail.assert_not_called()
            crm.assert_not_called()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["management_type"], "admin_managed")
        self.assertIsNone(first["owner"])
        self.assertEqual(first["history"][0]["action"], "admin_create")
        self.assertEqual(first["history"][0]["actor_id"], str(self.accounts["admin"]))
        for actor in ("admin", "admin_b", "buyer", "owner", "realtor"):
            self.assertIsNone(self.call("GET", "/professional-profile", actor))
        queue = self.call("GET", "/admin/professionals", "admin_b")
        self.assertEqual(queue["total"], 2)
        self.assertTrue(all(row["owner"] is None and row["management_type"] == "admin_managed" for row in queue["items"]))
        with Session(self.engine) as db:
            self.assertEqual({str(user.id): user.role for user in db.query(User).all()}, before)
            self.assertEqual(db.query(ProfessionalProfile).filter(ProfessionalProfile.user_id.is_(None)).count(), 2)

    def test_managed_routes_require_admin_and_never_edit_account_owned_profiles(self):
        own = self.save()
        managed = self.managed()
        for actor in (None, "buyer", "owner", "realtor", "unverified", "suspended"):
            denied = 401 if actor is None else 403
            self.managed(actor=actor, status=denied)
            self.managed_save(managed, actor=actor, status=denied)
            self.managed_submit(managed, actor=actor, status=denied)
        self.managed_save(own, status=403)
        self.managed_submit(own, status=403)
        response = self.client.post(f"/admin/professionals/{own['id']}/photo", headers=self.headers["admin"], files={"file": ("bad.png", b"image", "image/png")})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.call("POST", "/admin/professionals", "admin", {"draft": draft(), "user_id": str(self.accounts["buyer"])}, 422)
        self.managed(draft(user_id=str(self.accounts["buyer"])), status=422)
        self.managed(draft(photo_url="https://example.com/forged.webp"), status=422)
        self.assertEqual(self.call("GET", "/professional-profile")["version"], own["version"])

    def test_managed_submit_and_explicit_approval_require_permission_and_complete_business_data(self):
        managed = self.managed({})
        self.managed_submit(managed, status=422)
        self.moderate(managed, status=409)
        managed = self.managed_save(managed, draft(public_contact_consent=False))
        self.managed_submit(managed, status=422)
        self.managed_save(managed, draft(public_contact_consent="yes"), status=422)
        managed = self.managed_save(managed, draft())
        self.managed_save({**managed, "version": managed["version"] - 1}, draft(), status=409)
        pending = self.managed_submit(managed, actor="admin_b")
        self.assertEqual(pending["review_status"], "pending")
        self.assertEqual(self.call("GET", "/professionals", None)["total"], 0)
        approved = self.moderate(pending, actor="admin")
        self.assertEqual(approved["listing_status"], "published")
        self.assertEqual(approved["management_type"], "admin_managed")
        self.assertIsNone(approved["owner"])
        self.assertEqual(approved["history"][1]["action"], "admin_submit")
        public = self.call("GET", f"/professionals/{managed['id']}", None)
        self.assertEqual(public["business_name"], "Synthetic professional")
        for private in ("owner", "user_id", "management_type", "history", "review_note", "public_contact_consent"):
            self.assertNotIn(private, public)

    def test_managed_pending_edits_use_approved_snapshot_and_public_custom_service_search_only(self):
        data = draft(category_ids=["electrical", "other_services"], custom_services=["Smart home automation", "Gate installation"])
        approved = self.moderate(self.managed_submit(self.managed(data)))
        self.assertEqual(self.call("GET", "/professionals?q=automation", None)["total"], 1)
        changed = self.managed_save(approved, {**data, "custom_services": ["Irrigation systems"]})
        self.assertEqual(changed["listing_status"], "published")
        self.assertEqual(self.call("GET", "/professionals?q=Irrigation", None)["total"], 0)
        self.assertEqual(self.call("GET", "/professionals?q=automation", None)["total"], 1)
        pending = self.managed_submit(changed, actor="admin_b")
        rejected = self.moderate(pending, action="reject", actor="admin_b")
        self.assertEqual(self.call("GET", "/professionals?q=automation&category=electrical&area=sosua", None)["total"], 1)
        revised = self.moderate(self.managed_submit(self.managed_save(rejected)))
        self.assertEqual(self.call("GET", "/professionals?q=Irrigation", None)["items"][0]["custom_services"], ["Irrigation systems"])
        self.assertEqual(self.call("GET", "/professionals?q=automation", None)["total"], 0)
        self.assertEqual(revised["version"], len(revised["history"]))

    def test_managed_suspension_and_contact_permission_revocation_are_independent(self):
        approved = self.moderate(self.managed_submit(self.managed()))
        hidden = self.managed_save(approved, draft(public_contact_consent=False))
        self.assertEqual(hidden["listing_status"], "hidden")
        self.call("GET", f"/professionals/{approved['id']}", None, status=404)
        pending = self.managed_submit(self.managed_save(hidden, draft()))
        suspended = self.moderate(pending, action="suspend")
        self.managed_submit(suspended, status=403)
        reviewed = self.moderate(suspended, actor="admin_b")
        self.assertEqual(reviewed["listing_status"], "suspended")
        changed = self.managed_save(reviewed, draft(public_contact_consent=False))
        self.assertEqual(changed["listing_status"], "suspended")
        reinstated = self.moderate(changed, action="reinstate")
        self.assertEqual(reinstated["listing_status"], "hidden")
        self.assertEqual(reinstated["review_status"], "draft")
        self.moderate(reinstated, status=409)

    def test_managed_expected_version_blocks_concurrent_admin_lost_updates(self):
        own = self.managed()
        def update(actor):
            return self.client.put(f"/admin/professionals/{own['id']}", headers=self.headers[actor],
                                   json={"expected_version": own["version"], "draft": draft(actor)})
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, ("admin", "admin_b")))
        self.assertEqual(sorted(response.status_code for response in results), [200, 409])
        latest = self.call("GET", f"/admin/professionals/{own['id']}", "admin")
        self.assertEqual(latest["version"], 2)
        self.assertEqual(len(latest["history"]), 2)

    def test_custom_services_are_bounded_unique_and_require_other_without_breaking_incomplete_drafts(self):
        for labels in (["x"], ["x" * 81], ["one", "ONE"], ["Café", "cafe"], ["line\nfeed"], ["   "], [f"service{i}" for i in range(9)]):
            self.save(draft(category_ids=["other_services"], custom_services=labels), status=422)
        self.save(draft(custom_services=["Unsupported without Other"]), status=422)
        incomplete = self.save(draft(category_ids=["other_services"]))
        self.submit(incomplete, status=422)
        valid = self.save(draft(category_ids=["electrical", "plumbing", "other_services"], custom_services=["  Smart   controls  ", "Café equipment"]), version=incomplete["version"])
        self.assertEqual(valid["draft"]["custom_services"], ["Smart controls", "Café equipment"])
        published = self.moderate(self.submit(valid))
        self.assertEqual(self.call("GET", f"/professionals/{published['id']}", None)["custom_services"], ["Smart controls", "Café equipment"])

    def test_historical_snapshots_default_custom_services_without_backfilling_or_republishing(self):
        approved = self.publish()
        with Session(self.engine) as db:
            row = db.get(ProfessionalProfile, approved["id"])
            row.draft_data = {key: value for key, value in row.draft_data.items() if key != "custom_services"}
            row.published_data = {key: value for key, value in row.published_data.items() if key != "custom_services"}
            db.commit()
        public = self.call("GET", f"/professionals/{approved['id']}", None)
        self.assertEqual(public["custom_services"], [])
        self.assertEqual(self.call("GET", "/professional-profile")["draft"]["custom_services"], [])
        summary = self.call("GET", "/admin/professionals", "admin")["items"][0]
        self.assertEqual(summary["custom_services"], [])
        self.assertEqual(summary["management_type"], "account_owned")
        self.assertEqual(summary["version"], approved["version"])

    def test_managed_photo_assets_are_profile_scoped_immutable_and_do_not_change_saved_versions(self):
        managed, second = self.managed(), self.managed(actor="admin_b")
        image = io.BytesIO()
        Image.new("RGB", (24, 16), "blue").save(image, "PNG")
        original_url = settings.supabase_url
        settings.supabase_url = "https://synthetic-professional.supabase.co"
        self.addCleanup(setattr, settings, "supabase_url", original_url)
        storage = MagicMock()
        storage.storage.from_.return_value.get_public_url.side_effect = lambda path: f"{settings.supabase_url}/storage/v1/object/public/{settings.storage_bucket}/{path}"
        with patch.object(professional_photos, "_get_client", return_value=storage):
            urls = []
            for actor in ("admin", "admin_b"):
                response = self.client.post(f"/admin/professionals/{managed['id']}/photo", headers=self.headers[actor], files={"file": ("photo.png", image.getvalue(), "image/png")})
                self.assertEqual(response.status_code, 200, response.text)
                urls.append(response.json()["photo_url"])
            self.assertNotEqual(*urls)
            self.assertIn(f"/professionals/managed/{managed['id']}/", urls[0])
            self.assertEqual(self.call("GET", f"/admin/professionals/{managed['id']}", "admin")["version"], managed["version"])
            self.managed_save(second, draft(photo_url=urls[0]), status=422)
            self.save(draft(photo_url=urls[0]), status=422)
            saved = self.managed_save(managed, draft(photo_url=urls[0]))
            self.assertEqual(self.moderate(self.managed_submit(saved))["published_profile"]["photo_url"], urls[0])
            for upload in storage.storage.from_.return_value.upload.call_args_list:
                self.assertEqual(upload.args[2]["upsert"], "false")
                self.assertEqual(Image.open(io.BytesIO(upload.args[1])).format, "WEBP")
            storage.storage.from_.return_value.remove.assert_not_called()
        response = self.client.post(f"/admin/professionals/{managed['id']}/photo", headers=self.headers["buyer"], files={"file": ("photo.png", image.getvalue(), "image/png")})
        self.assertEqual(response.status_code, 403)

    def test_nullable_owner_migration_retains_managed_rows_history_and_existing_owner_visibility_rules(self):
        owned = self.publish()
        path = Path(__file__).parents[1] / "alembic/versions/e77ca75c44ba_allow_admin_managed_professional_.py"
        spec = importlib.util.spec_from_file_location("managed_professional_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with self.engine.begin() as connection:
            connection.execute(text("ALTER TABLE professional_profiles ALTER COLUMN user_id SET NOT NULL"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
            self.assertFalse(connection.scalar(text("SELECT attnotnull FROM pg_attribute WHERE attrelid='professional_profiles'::regclass AND attname='user_id'")))
        managed = self.moderate(self.managed_submit(self.managed()))
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            self.assertFalse(connection.scalar(text("SELECT attnotnull FROM pg_attribute WHERE attrelid='professional_profiles'::regclass AND attname='user_id'")))
            self.assertEqual(connection.scalar(text("SELECT count(*) FROM pg_policies WHERE schemaname=:schema AND tablename LIKE 'professional%'"), {"schema": self.schema}), 0)
            flags = dict(connection.execute(text("SELECT relname, relrowsecurity FROM pg_class JOIN pg_namespace ON pg_namespace.oid=pg_class.relnamespace WHERE nspname=:schema AND relname IN ('professional_profiles','professional_profile_revisions')"), {"schema": self.schema}).all())
            self.assertTrue(all(flags.values()))
        with Session(self.engine) as db:
            db.get(User, self.accounts["buyer"]).status = "suspended"
            db.get(User, self.accounts["admin"]).status = "suspended"
            db.commit()
        self.call("GET", f"/professionals/{owned['id']}", None, status=404)
        self.assertEqual(self.call("GET", f"/professionals/{managed['id']}", None)["id"], managed["id"])
        self.assertEqual(len(self.call("GET", f"/admin/professionals/{managed['id']}", "admin_b")["history"]), managed["version"])


if __name__ == "__main__":
    unittest.main()
