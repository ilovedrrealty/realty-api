"""CSV location preservation; synthetic local Postgres only, no uploads or HTTP."""
import csv
import io
import os
from decimal import Decimal
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-bulk-location-tests-only",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
                  SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from database import Base
import models
from models.listing import Listing
from models.listing_event import ListingEvent
from models.listing_location_review import ListingLocationReview
from models.user import User
from utils import bulk_import


class LocationParsingTests(unittest.TestCase):
    def test_csv_parser_preserves_optional_columns_and_keeps_old_headers_valid(self):
        base = {column: "value" for column in bulk_import.REQUIRED_COLUMNS}
        for fields in (base, {**base, "maps_url": "https://maps.app.goo.gl/synthetic", "latitude": "19.7543000", "longitude": "-70.4125000"}):
            stream = io.StringIO()
            writer = csv.DictWriter(stream, fieldnames=list(fields))
            writer.writeheader(); writer.writerow(fields)
            row = bulk_import.parse_csv(stream.getvalue().encode("utf-8-sig"))[0]
            self.assertEqual(row, fields)

    def test_complete_finite_coordinate_pair_including_zero_and_boundary_values(self):
        for latitude, longitude in (("19.7543000", "-70.4125000"), ("0", "0"), ("-90", "180"), ("90", "-180")):
            result = bulk_import._parse_location_fields({"latitude": latitude, "longitude": longitude})
            self.assertEqual(result, (None, Decimal(latitude), Decimal(longitude), []))
        self.assertEqual(bulk_import._parse_location_fields({}), (None, None, None, []))
        self.assertEqual(bulk_import._parse_location_fields({"latitude": " ", "longitude": ""}), (None, None, None, []))

    def test_incomplete_nonfinite_invalid_and_out_of_range_pairs_return_row_errors(self):
        invalid = [("19.7", ""), ("", "-70.4"), ("NaN", "-70"), ("sNaN", "0"), ("Infinity", "0"), ("0", "-Infinity"),
                   ("91", "0"), ("-90.0000001", "0"), ("0", "180.0000001"), ("0", "-181"), ("Cabarete", "-70"), ("19,7", "-70")]
        for latitude, longitude in invalid:
            with self.subTest(latitude=latitude, longitude=longitude):
                self.assertTrue(bulk_import._parse_location_fields({"latitude": latitude, "longitude": longitude})[3])

    def test_known_maps_links_are_preserved_without_fetching_or_extracting_viewport_coordinates(self):
        for url in ("https://www.google.com/maps/search/?api=1&query=Cabarete", "https://google.com/maps/@19.7543,-70.4125,15z",
                    "https://maps.google.com/?q=19.7543,-70.4125", "https://maps.app.goo.gl/syntheticToken", "https://goo.gl/maps/syntheticToken"):
            with self.subTest(url=url), patch.object(bulk_import.httpx, "get") as fetch:
                self.assertEqual(bulk_import._parse_location_fields({"maps_url": url}), (url, None, None, []))
                fetch.assert_not_called()

    def test_nonmaps_hosts_credentials_local_addresses_and_ambiguous_urls_are_rejected(self):
        invalid = ["http://www.google.com/maps", "javascript:alert(1)", "https://localhost/maps", "https://127.0.0.1/maps",
                   "https://www.google.com.evil.example/maps", "https://www.google.com@localhost/maps",
                   "https://user:secret@www.google.com/maps", "https://@www.google.com/maps", "https://www.google.com:8443/maps",
                   "https://www.google.com/url?q=https://example.com", "https://www.google.com/maps-other",
                   "https://maps.app.goo.gl/", "https://goo.gl/not-maps/token", "https://www.google.com/maps/../url?q=x",
                   "https://www.google.com/maps/%2e%2e/url?q=x", "https://www.google.com\\@example.com/maps",
                   "https://www.google.com/maps/api/staticmap?center=19,-70&key=SYNTHETIC_ONLY",
                   "https://www.google.com/maps/api/staticmap?center=19,-70",
                   "https://www.google.com/maps/search/?api=1&key=SYNTHETIC_ONLY",
                   "https://www.google.com/maps/?api_key=SYNTHETIC_ONLY", "https://maps.app.goo.gl/synthetic?access_token=SYNTHETIC_ONLY",
                   "https://maps.google.com/?token=SYNTHETIC_ONLY", "https://www.google.com/maps/?%6Bey=SYNTHETIC_ONLY",
                   "https://www.google.com/maps/\nprivate", "https://[malformed/maps", "https://www.google.com:invalid/maps"]
        for url in invalid:
            with self.subTest(url=url):
                errors = bulk_import._parse_location_fields({"maps_url": url})[3]
                self.assertTrue(errors)
                self.assertNotIn("secret", str(errors))
                self.assertNotIn("SYNTHETIC_ONLY", str(errors))


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires disposable loopback Postgres")
class LocationImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host not in ("127.0.0.1", "localhost", "::1") or url.get_backend_name() != "postgresql":
            raise ValueError("Tests require a separate loopback-only Postgres")
        cls.schema = "bulk_location_test_" + uuid4().hex
        bootstrap = create_engine(url)
        with bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        bootstrap.dispose()
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema})
        Base.metadata.create_all(cls.engine)
        with Session(cls.engine) as db:
            admin = User(email="synthetic-location-admin@example.com", display_name="Synthetic location admin", role="admin", email_verified=True, status="active")
            db.add(admin); db.commit(); db.refresh(admin)
            cls.admin_id, cls.admin_code = admin.id, admin.user_code

    @classmethod
    def tearDownClass(cls):
        with cls.engine.begin() as connection:
            connection.execute(text('SET search_path TO public'))
            assert cls.schema.startswith("bulk_location_test_") and len(cls.schema) == 51
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.engine.dispose()

    def setUp(self):
        self.db = Session(self.engine)
        self.db.query(ListingEvent).delete(); self.db.query(Listing).delete(); self.db.commit()
        self.addCleanup(self.db.close)
        self.download_patch = patch.object(bulk_import, "_download_photo", return_value=(b"synthetic-only", "image/jpeg"))
        self.upload_patch = patch.object(bulk_import, "upload_image", return_value="https://example.com/synthetic-property-photo.jpg")
        self.download = self.download_patch.start(); self.upload = self.upload_patch.start()
        self.addCleanup(self.download_patch.stop); self.addCleanup(self.upload_patch.stop)

    def row(self, **changes):
        return {"title": "Synthetic CSV property", "property_type": "apartment", "purpose": "sale", "region": "Cabarete",
                "currency": "USD", "price": "199000", "description_en": "Synthetic fixture only", "co_listing_enabled": "no",
                "assigned_agent_id": str(self.admin_code), "source_ref": "synthetic-" + uuid4().hex,
                "photo_urls": "https://example.com/synthetic-input.jpg", **changes}

    def test_explicit_pair_and_maps_url_are_private_evidence_with_existing_photo_import(self):
        row = self.row(maps_url="https://www.google.com/maps/@19.75,-70.41,15z", latitude="19.7543000", longitude="-70.4125000")
        result = bulk_import._import_row(self.db, row, self.admin_id)
        self.assertEqual(result["status"], "succeeded", result)
        self.db.commit(); self.db.expire_all()
        saved = self.db.get(Listing, UUID(result["listing_id"]))
        self.assertEqual((saved.maps_url, saved.latitude, saved.longitude), (None, None, None))
        review = self.db.get(ListingLocationReview, saved.id)
        self.assertEqual((review.review_status, review.public_precision), ("unreviewed", "area"))
        self.assertEqual(review.maps_url, row["maps_url"])
        self.assertEqual((review.latitude, review.longitude), (Decimal(row["latitude"]), Decimal(row["longitude"])))
        self.assertEqual(saved.images, ["https://example.com/synthetic-property-photo.jpg"])
        self.assertEqual(self.db.query(ListingEvent).count(), 1)
        self.download.assert_called_once(); self.upload.assert_called_once()

    def test_older_csv_and_maps_only_rows_create_no_invented_coordinates(self):
        for fields in ({}, {"maps_url": "https://www.google.com/maps/@19.75,-70.41,15z"}):
            row = self.row(**fields)
            stream = io.StringIO()
            writer = csv.DictWriter(stream, fieldnames=list(row)); writer.writeheader(); writer.writerow(row)
            parsed = bulk_import.parse_csv(stream.getvalue().encode())[0]
            result = bulk_import._import_row(self.db, parsed, self.admin_id)
            self.assertEqual(result["status"], "succeeded", result)
            self.db.commit(); self.db.expire_all()
            saved = self.db.get(Listing, UUID(result["listing_id"]))
            self.assertIsNone(saved.latitude); self.assertIsNone(saved.longitude)
            self.assertIsNone(saved.maps_url)
            review = self.db.get(ListingLocationReview, saved.id)
            if fields.get("maps_url"):
                self.assertEqual(review.maps_url, fields["maps_url"])
                self.assertEqual((review.latitude, review.longitude), (None, None))
            else:
                self.assertIsNone(review)

    def test_invalid_locations_fail_before_download_upload_or_listing_event_side_effects(self):
        for changes in ({"latitude": "19.7"}, {"longitude": "-70.4"}, {"latitude": "NaN", "longitude": "-70"},
                        {"latitude": "Infinity", "longitude": "-70"}, {"latitude": "19", "longitude": "-Infinity"},
                        {"latitude": "91", "longitude": "-70"}, {"maps_url": "https://localhost/maps"}, {"maps_url": "https://user:secret@www.google.com/maps"}):
            result = bulk_import._import_row(self.db, self.row(**changes), self.admin_id)
            self.assertEqual(result["status"], "failed", result)
            self.assertTrue(result["message"])
            self.assertEqual(self.db.query(Listing).count(), 0)
            self.assertEqual(self.db.query(ListingEvent).count(), 0)
            self.download.assert_not_called(); self.upload.assert_not_called()

    def test_repeated_source_ref_never_overwrites_existing_url_or_coordinates(self):
        original = self.row(maps_url="https://maps.app.goo.gl/syntheticOriginal", latitude="19.7543000", longitude="-70.4125000")
        result = bulk_import._import_row(self.db, original, self.admin_id)
        self.db.commit()
        self.download.reset_mock(); self.upload.reset_mock()
        changed = {**original, "maps_url": "https://localhost/maps", "latitude": "NaN", "longitude": "99"}
        skipped = bulk_import._import_row(self.db, changed, self.admin_id)
        self.assertEqual(skipped["status"], "skipped")
        self.db.expire_all()
        saved = self.db.get(Listing, UUID(result["listing_id"]))
        self.assertEqual((saved.maps_url, saved.latitude, saved.longitude), (None, None, None))
        review = self.db.get(ListingLocationReview, saved.id)
        self.assertEqual((review.maps_url, review.latitude, review.longitude, review.version),
            (original["maps_url"], Decimal(original["latitude"]), Decimal(original["longitude"]), 1))
        self.assertEqual(self.db.query(Listing).count(), 1)
        self.download.assert_not_called(); self.upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
