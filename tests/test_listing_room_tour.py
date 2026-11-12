"""Reviewed room tours through the listing routes on disposable PostgreSQL."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import UUID

import test_location_review as location_fixture

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import Session

from config import settings
from models.listing import Listing
from models.listing_edit import ListingEdit
from routes import admin, listings


ORIGIN = "https://synthetic-room-tour.supabase.co"
GALLERY = f"{ORIGIN}/storage/v1/object/public/listing-images/"
IMAGES = [GALLERY + "fixture-owner/living.jpg?", GALLERY + "fixture-owner/bedroom.webp"]


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class ListingRoomPhotoTourTests(unittest.TestCase):
    _fixture = location_fixture.LocationReviewTests
    tearDownClass = classmethod(_fixture.tearDownClass.__func__)

    @classmethod
    def setUpClass(cls):
        cls._fixture.setUpClass.__func__(cls)

    def setUp(self):
        self._fixture.setUp(self)
        self.listing_id = self.listing_ids[0]
        self.listing_path = f"/listings/{self.listing_id}"
        self.editor_path = self.listing_path + "/room-photo-tour"
        with Session(self.engine) as db:
            listing = db.get(Listing, UUID(self.listing_id))
            listing.submitted_by = self.ids["owner"]
            listing.owner_id = self.ids["owner"]
            listing.transaction = "rent"
            listing.images = IMAGES
            db.commit()
        for replacement in (
            patch.object(settings, "supabase_url", ORIGIN),
            patch.object(settings, "storage_bucket", "listing-images"),
            patch.object(listings, "maybe_regenerate_share_image"),
            patch.object(admin, "maybe_regenerate_share_image"),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def request(self, method, path, actor=None, body=None, status=200):
        result = self.client.request(method, path, headers=self.headers.get(actor, {}), json=body)
        self.assertEqual(result.status_code, status, result.text)
        return result

    def tour(self, photos=None):
        return {"groups": [{
            "id": "living-room", "label_en": "Living room", "label_es": "Sala",
            "photos": photos if photos is not None else [
                {"url": IMAGES[0], "caption_en": "Bright room", "caption_es": "Sala luminosa"},
                {"url": IMAGES[1]},
            ],
        }]}

    def stage(self, tour, actor="owner", status=202):
        return self.request("PUT", self.editor_path, actor, {"room_photo_tour": tour}, status)

    def editor(self, actor="owner"):
        response = self.request("GET", self.editor_path, actor)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        return response.json()

    def detail(self):
        response = self.request("GET", self.listing_path)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        return response.json()

    def pending(self):
        response = self.request("GET", "/admin/listing-edits", "admin")
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        rows = response.json()
        matches = [row for row in rows if row["listing_id"] == self.listing_id]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def review(self, edit_id, action="approve", status=204):
        return self.request("POST", f"/admin/listing-edits/{edit_id}/{action}", "admin",
                            {"reason": "Synthetic review reason"} if action == "reject" else None, status)

    def test_approved_detail_only_and_explicit_clear(self):
        original = {"listing_id": self.listing_id, "images": IMAGES, "room_photo_tour": None, "pending_review": "none"}
        self.assertEqual(self.editor(), original)
        response = self.stage(self.tour())
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertEqual(set(response.json()), {"message", "listing_id"})
        self.assertIsNone(self.detail()["room_photo_tour"])
        self.assertEqual(self.editor(), {**original, "pending_review": "room_photo_tour"})
        edit = self.pending()
        self.assertEqual(edit["proposed_data"], {"room_photo_tour": self.tour()})
        self.assertIsNone(edit["current_data"]["room_photo_tour"])
        self.review(edit["id"])
        self.assertEqual(self.detail()["room_photo_tour"], self.tour())
        self.assertEqual(self.detail()["images"], IMAGES)
        for rows in (self.request("GET", "/listings").json()["items"], self.request("GET", "/listings/deal").json()):
            self.assertIn(self.listing_id, {row["id"] for row in rows})
            self.assertTrue(all("room_photo_tour" not in row for row in rows))

        self.stage(None, "admin")
        self.assertEqual(self.detail()["room_photo_tour"], self.tour())
        self.review(self.pending()["id"])
        self.assertIsNone(self.detail()["room_photo_tour"])

    def test_pending_scope_and_current_assignment(self):
        self.request("GET", self.editor_path, status=401)
        self.request("GET", self.editor_path, "buyer", status=404)
        self.stage(self.tour(), "buyer", status=404)
        self.request("PUT", self.listing_path, "owner", {"description": "Synthetic ordinary draft"})
        ordinary = self.pending()
        self.assertEqual(self.editor()["pending_review"], "other")
        self.stage(self.tour(), status=409)
        self.assertEqual(self.pending()["id"], ordinary["id"])
        self.review(ordinary["id"])
        self.assertEqual(self.detail()["description"], "Synthetic ordinary draft")
        self.request("PUT", self.listing_path, "owner", {"room_photo_tour": None}, status=422)

        self.stage(self.tour(), "realtor")
        realtor_edit = self.pending()["id"]
        self.stage(None, "owner", status=409)
        self.request("PUT", self.listing_path, "owner", {"title": "Synthetic changed title"}, status=409)
        self.assertEqual(self.pending()["id"], realtor_edit)
        self.stage(self.tour([{"url": IMAGES[1]}]), "realtor")
        self.assertNotEqual(self.pending()["id"], realtor_edit)
        self.request("PUT", f"/admin/listings/{self.listing_id}/assign", "admin", {"realtor_id": None}, 204)
        self.request("GET", self.editor_path, "realtor", status=404)
        self.review(self.pending()["id"], status=409)
        self.review(self.pending()["id"], "reject")

    def test_allow_only_exact_approved_public_gallery_urls_and_metadata(self):
        unsafe = [
            IMAGES[0][:-1], IMAGES[1] + "?synthetic=1", IMAGES[1] + "#",
            IMAGES[1].replace("synthetic-room-tour", "unrelated-fixture"),
            IMAGES[1].replace("/public/", "/sign/"), IMAGES[1].replace("listing-images", "private-fixture"),
            IMAGES[1].replace("fixture-owner/", "professionals/"),
            IMAGES[1].replace("https://", "https://fixture@"),
        ]
        # Put malformed URLs into the legacy gallery too, to exercise validation independently of membership.
        with Session(self.engine) as db:
            db.get(Listing, UUID(self.listing_id)).images = [*IMAGES, *unsafe[1:]]
            db.commit()
        self.assertEqual(self.editor()["images"], IMAGES)
        self.assertEqual(self.detail()["images"], [*IMAGES, *unsafe[1:]])
        for candidate in unsafe:
            with self.subTest(candidate=candidate):
                self.stage(self.tour([{"url": candidate}]), status=422)
        self.stage(self.tour([{"url": IMAGES[0], "caption_en": "One language"}]), status=422)
        self.stage(self.tour([{"url": IMAGES[0]}, {"url": IMAGES[0]}]), status=422)
        self.assertEqual(self.editor()["pending_review"], "none")
        self.stage(self.tour())

    def test_gallery_changes_permanently_invalidate_old_approval_and_pending(self):
        self.stage(self.tour())
        self.review(self.pending()["id"])
        self.stage(self.tour([{"url": IMAGES[1]}]))
        stale = self.pending()["id"]
        self.request("PUT", self.listing_path, "admin", {"images": IMAGES[1:]})
        self.assertIsNone(self.detail()["room_photo_tour"])
        self.request("PUT", self.listing_path, "admin", {"images": IMAGES})
        self.assertEqual(self.detail()["images"], IMAGES)
        self.assertIsNone(self.detail()["room_photo_tour"])
        self.review(stale, status=409)
        self.stage(self.tour())
        self.review(self.pending()["id"])
        self.assertEqual(self.detail()["room_photo_tour"], self.tour())

    def test_migration_old_rows_and_archive_close_pending(self):
        path = Path(__file__).resolve().parents[1] / "alembic/versions/d8f3a7c2e615_listing_room_photo_tour.py"
        spec = importlib.util.spec_from_file_location("listing_room_tour_fixture_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with self.engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.downgrade()
            migration.upgrade()
            migration.upgrade()
        self.assertEqual(self.detail()["images"], IMAGES)
        self.assertIsNone(self.detail()["room_photo_tour"])
        self.stage(self.tour())
        self.review(self.pending()["id"])
        self.stage(None)
        stale = self.pending()["id"]
        self.request("DELETE", self.listing_path, "owner", status=204)
        self.request("GET", self.listing_path, status=404)
        self.request("GET", self.editor_path, "owner", status=404)
        self.review(stale, status=409)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(Listing, UUID(self.listing_id)).room_photo_tour_snapshot)
            self.assertIsNone(db.get(ListingEdit, UUID(stale)).room_photo_tour_base_gallery_sha256)
