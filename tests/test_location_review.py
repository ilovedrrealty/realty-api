"""Location review/privacy against random schemas in disposable loopback Postgres."""
import importlib.util
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import unittest

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-location-review-only",
    GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="", SUPABASE_URL="",
    SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")
import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from alembic.migration import MigrationContext
from alembic.operations import Operations
from config import settings
from database import Base, get_db
import models
from models.user import User
from models.listing import Listing
from models.listing_edit import ListingEdit
from models.listing_event import ListingEvent
from models.listing_location_review import ListingLocationReview as Review, ListingLocationReviewRevision as Revision
from routes import location_review, listings, admin
from utils.location_quality import coordinate_pair, map_destination_coordinates
from utils.limiter import limiter


class DestinationParsingTests(unittest.TestCase):
    def test_never_uses_viewport_and_accepts_only_safe_destination_pairs(self):
        self.assertIsNone(map_destination_coordinates("https://www.google.com/maps/@19.75,-70.4,15z"))
        self.assertIsNone(map_destination_coordinates("https://maps.google.com/?q=19.75,-70.4&destination=18.5,-69.9"))
        self.assertEqual(map_destination_coordinates("https://maps.google.com/?q=19.75%2C-70.4"), (Decimal("19.75"), Decimal("-70.4")))
        self.assertEqual(map_destination_coordinates("https://www.google.com/maps/place/synthetic/data=!3d19.75!4d-70.4"), (Decimal("19.75"), Decimal("-70.4")))
        for value in ("https://user:secret@www.google.com/maps?q=19.75,-70.4", "https://www.google.com/maps?q=19.75,-70.4&key=SECRET", "https://evil.example/maps?q=19.75,-70.4"):
            self.assertIsNone(map_destination_coordinates(value))
        self.assertTrue(coordinate_pair(0, 0))
        self.assertTrue(coordinate_pair(-90, 180))
        self.assertFalse(coordinate_pair("NaN", 0))
        self.assertFalse(coordinate_pair(None, 0))


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires disposable loopback Postgres")
class LocationReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host not in ("127.0.0.1", "localhost", "::1") or url.get_backend_name() != "postgresql":
            raise ValueError("Location tests require disposable loopback-only Postgres")
        cls.schema = "location_review_test_" + uuid4().hex
        bootstrap = create_engine(url)
        with bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        bootstrap.dispose()
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema})
        Base.metadata.create_all(cls.engine)
        app = FastAPI()
        app.include_router(location_review.router); app.include_router(listings.router); app.include_router(admin.router)
        def local_db():
            with Session(cls.engine) as db:
                yield db
        app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(app)
        cls.old_limiter = limiter.enabled; limiter.enabled = False
        cls.ids, cls.headers = {}, {}
        with Session(cls.engine) as db:
            for role in ("admin", "buyer", "owner", "realtor"):
                user = User(email="synthetic-location-"+role+"@example.invalid", display_name="Synthetic "+role, role=role, email_verified=True, status="active")
                db.add(user); db.flush(); cls.ids[role] = user.id
                cls.headers[role] = {"Authorization": "Bearer "+jwt.encode({"sub": str(user.id), "exp": datetime.now(timezone.utc)+timedelta(hours=1)}, settings.jwt_secret, algorithm="HS256")}
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.client.close(); limiter.enabled = cls.old_limiter
        with cls.engine.begin() as connection:
            connection.execute(text("SET search_path TO public"))
            if not cls.schema.startswith("location_review_test_"):
                raise ValueError("Unexpected test schema")
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.engine.dispose()

    def setUp(self):
        with Session(self.engine) as db:
            db.query(Revision).delete(); db.query(Review).delete(); db.query(ListingEvent).delete(); db.query(ListingEdit).delete(); db.query(Listing).delete()
            self.listing_ids = []
            for index in range(4):
                row = Listing(title="Synthetic property "+str(index), location="Cabarete" if index < 3 else "Hidden synthetic area",
                    type="villa", transaction="sale", price=199000, currency="USD", features=[], images=[], status="active",
                    source_ref="synthetic-property-"+str(index), submitted_by=self.ids["admin"], assigned_realtor_id=self.ids["realtor"],
                    latitude=Decimal("19.75") if index == 0 else None, longitude=Decimal("-70.4") if index == 0 else None,
                    maps_url="https://maps.google.com/?q=19.75,-70.4" if index == 0 else None,
                    is_deal=True)
                db.add(row); db.flush(); self.listing_ids.append(str(row.id))
            db.commit()

    def call(self, method, path="", actor="admin", body=None, status=200):
        response = self.client.request(method, "/admin/location-review"+path, json=body, headers=self.headers.get(actor, {}))
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        return response.json()

    def body(self, listing_id=None, **changes):
        listing_id = listing_id or self.listing_ids[1]
        row = self.call("GET", "/"+listing_id)
        return {"expected_version": row["version"], "expected_listing_updated_at": row["listing_updated_at"],
            "latitude": "19.7543000", "longitude": "-70.4125000", "maps_url": "https://maps.google.com/?q=19.7543,-70.4125",
            "review_status": "verified", "public_precision": "exact", "source_type": "partner_record",
            "source_reference": "PRIVATE-SOURCE-REFERENCE", "review_note": "PRIVATE-LOCATION-NOTE", "confirmed_property": True, **changes}

    def test_admin_only_no_store_and_private_validation(self):
        for actor, status in ((None,401),("buyer",403),("owner",403),("realtor",403)):
            for path in ("", "/summary", "/"+self.listing_ids[0]):
                self.call("GET", path, actor, status=status)
            response = self.client.get("/admin/location-review/request-worksheet", headers=self.headers.get(actor, {}))
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.call("GET", "/"+str(uuid4()), status=404)
        self.call("PATCH", "/"+self.listing_ids[1], body={**self.body(), "maps_url": "https://user:PRIVATESECRET@www.google.com/maps"}, status=422)
        self.call("PATCH", "/"+self.listing_ids[1], body={**self.body(), "review_note": "PRIVATE\x00NOTE"}, status=422)
        with patch.object(location_review, "base_query", side_effect=SQLAlchemyError("PRIVATE-SOURCE-PARAMETER")):
            self.assertNotIn("PRIVATE", str(self.call("GET", "/summary", status=503)))

    def test_summary_preserves_legacy_points_without_claiming_review(self):
        result = self.call("GET", "/summary")
        self.assertEqual(result, {"total":4,"coordinate_available":1,"missing_coordinates":3,"verified":0,"unreviewed":4,"needs_review":0,"public_mapped":1,"private_positions":0,"with_source_reference":0})
        missing = self.call("GET")
        self.assertEqual(missing["total"], 3)
        public = self.client.get("/listings/map").json()
        self.assertEqual(public["mapped_total"],1)
        self.assertFalse(public["items"][0]["location_verified"])

    def test_legacy_map_link_without_saved_pair_is_not_exposed_as_area_location(self):
        listing_id = self.listing_ids[1]
        with Session(self.engine) as db:
            db.get(Listing, listing_id).maps_url = "https://maps.google.com/?q=19.75,-70.4"
            db.commit()
        response = self.client.get("/listings/"+listing_id)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        public = response.json()
        self.assertEqual(public["location_precision"], "area")
        self.assertIsNone(public["maps_url"])
        self.assertIsNone(public["latitude"])

    def test_verified_exact_point_has_canonical_public_link_and_private_evidence(self):
        listing_id = self.listing_ids[1]
        result = self.call("PATCH", "/"+listing_id, body=self.body())
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["review_status"], "verified")
        self.assertTrue(result["public_mapped"])
        public = self.client.get("/listings/"+listing_id).json()
        self.assertTrue(public["location_verified"])
        self.assertEqual(Decimal(str(public["latitude"])), Decimal("19.7543"))
        self.assertIn("query=19.7543000,-70.4125000", public["maps_url"])
        self.assertNotIn("PRIVATE", str(public))
        self.assertNotIn("review_note", public)
        self.assertEqual(len(result["history"]), 1)

    def assert_private_projection(self, listing_id, precision):
        for path in ("/listings/"+listing_id, "/listings?page_size=60&include_aggregates=false", "/listings/deal"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers.get("cache-control"), "no-store")
            body = response.json()
            candidates = body.get("items", [body]) if isinstance(body,dict) else body
            row = next(item for item in candidates if item["id"] == listing_id)
            self.assertIsNone(row["latitude"]); self.assertIsNone(row["longitude"]); self.assertIsNone(row["maps_url"])
            self.assertEqual(row["location_precision"], precision)
            self.assertNotIn("PRIVATE", str(row))
        public_map = self.client.get("/listings/map").json()
        self.assertNotIn(listing_id, [x["id"] for x in public_map["items"]])
        with Session(self.engine) as db:
            listing, review = db.get(Listing, listing_id), db.get(Review, listing_id)
            self.assertEqual((listing.latitude,listing.longitude,listing.maps_url),(None,None,None))
            self.assertEqual(review.latitude, Decimal("19.7543"))

    def test_area_precision_retains_raw_only_in_private_table(self):
        listing_id = self.listing_ids[1]
        self.call("PATCH", "/"+listing_id, body=self.body(public_precision="area"))
        self.assert_private_projection(listing_id, "area")
        summary = self.call("GET", "/summary")
        self.assertEqual((summary["verified"],summary["private_positions"],summary["public_mapped"]),(1,1,1))

    def test_hidden_precision_removes_exact_and_approximate_map_display(self):
        listing_id = self.listing_ids[3]
        self.call("PATCH", "/"+listing_id, body=self.body(listing_id, public_precision="hidden"))
        self.assert_private_projection(listing_id, "hidden")
        public = self.client.get("/listings/map").json()
        self.assertNotIn("Hidden synthetic area", [x["location"] for x in public["areas"]])
        self.assertEqual(public["ungrouped_total"],1)

    def test_pending_review_does_not_publish_new_point(self):
        result = self.call("PATCH", "/"+self.listing_ids[1], body=self.body(review_status="needs_review", confirmed_property=False))
        self.assertFalse(result["public_mapped"])
        self.assertTrue(result["has_coordinates"])
        self.assertEqual(self.call("GET", "/summary")["needs_review"],1)

    def test_stale_versions_never_overwrite_and_other_listing_changes_conflict(self):
        listing_id = self.listing_ids[1]
        body = self.body()
        saved = self.call("PATCH", "/"+listing_id, body=body)
        self.call("PATCH", "/"+listing_id, body={**body,"latitude":"19.8"}, status=409)
        self.assertEqual(self.call("GET", "/"+listing_id),saved)
        body = self.body()
        with patch.object(listings, "maybe_regenerate_share_image"):
            changed = self.client.put("/listings/"+listing_id,json={"title":"New synthetic title"},headers=self.headers["admin"])
        self.assertEqual(changed.status_code,200,changed.text)
        self.call("PATCH", "/"+listing_id, body=body,status=409)

    def test_simultaneous_reviews_cannot_both_accept_the_same_version(self):
        listing_id = self.listing_ids[1]
        body = self.body()
        def attempt(_):
            return self.client.patch("/admin/location-review/"+listing_id, json=body, headers=self.headers["admin"]).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(attempt, (1, 2))), [200, 409])
        with Session(self.engine) as db:
            self.assertEqual(db.get(Review, listing_id).version, 1)
            self.assertEqual(db.query(Revision).filter(Revision.listing_id == listing_id).count(), 1)

    def test_generic_admin_edit_invalidates_and_does_not_reveal_private_pair(self):
        listing_id = self.listing_ids[1]
        self.call("PATCH", "/"+listing_id, body=self.body(public_precision="area"))
        with patch.object(listings,"maybe_regenerate_share_image"):
            response = self.client.put("/listings/"+listing_id,json={"latitude":19.7543,"longitude":-70.4125,"location":"Updated synthetic area"},headers=self.headers["admin"])
        self.assertEqual(response.status_code,200,response.text)
        current = self.call("GET", "/"+listing_id)
        self.assertEqual((current["version"],current["review_status"],current["public_precision"]),(2,"unreviewed","area"))
        self.assert_private_projection(listing_id,"area")

    def test_pending_edit_approval_preserves_hidden_policy(self):
        listing_id = self.listing_ids[1]
        self.call("PATCH", "/"+listing_id,body=self.body(public_precision="hidden"))
        response = self.client.put("/listings/"+listing_id,json={"latitude":19.7543,"longitude":-70.4125,"location":"Updated synthetic area"},headers=self.headers["realtor"])
        self.assertEqual(response.status_code,200,response.text)
        with Session(self.engine) as db:
            edit_id = str(db.query(ListingEdit).filter(ListingEdit.listing_id==listing_id,ListingEdit.status=="pending").one().id)
        with patch.object(admin,"maybe_regenerate_share_image"):
            response = self.client.post("/admin/listing-edits/"+edit_id+"/approve",headers=self.headers["admin"])
        self.assertEqual(response.status_code,204,response.text)
        self.assertEqual(self.call("GET","/"+listing_id)["review_status"],"unreviewed")
        self.assert_private_projection(listing_id,"hidden")

    def test_viewport_or_mismatched_destination_cannot_be_verification_evidence(self):
        for url in ("https://www.google.com/maps/@19.7543,-70.4125,15z", "https://maps.google.com/?q=18.5,-69.9"):
            self.call("PATCH","/"+self.listing_ids[1],body=self.body(source_type="map_destination",maps_url=url),status=422)
        self.call("PATCH","/"+self.listing_ids[1],body=self.body(source_type="map_destination"))

    def test_invalid_pairs_confirmation_and_urls_are_rejected_without_echo(self):
        invalid=[{"latitude":"NaN"},{"longitude":None},{"latitude":"91"},{"longitude":"-181"},{"confirmed_property":False},{"source_reference":""},{"source_type":None},{"maps_url":"https://www.google.com/maps?q=19,-70&access_token=PRIVATESECRET"},{"user_id":str(self.ids["buyer"])}]
        for changes in invalid:
            response=self.call("PATCH","/"+self.listing_ids[1],body=self.body(**changes),status=422)
            self.assertNotIn("PRIVATESECRET",str(response))
        self.assertEqual(self.call("GET","/summary")["public_mapped"],1)

    def test_worksheet_is_bounded_formula_safe_and_has_no_private_evidence(self):
        with Session(self.engine) as db:
            row=db.get(Listing,self.listing_ids[1]);row.title="=SYNTHETIC_FORMULA";db.commit()
        response=self.client.get("/admin/location-review/request-worksheet",headers=self.headers["admin"])
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.headers.get("cache-control"),"no-store")
        self.assertIn("'=SYNTHETIC_FORMULA",response.text)
        self.assertNotIn("PRIVATE",response.text)
        self.assertNotIn("synthetic-property-0",response.text)

    def test_projection_mismatch_is_redacted_without_mutating_evidence(self):
        listing_id=self.listing_ids[1]
        self.call("PATCH","/"+listing_id,body=self.body())
        with Session(self.engine) as db:
            row=db.get(Listing,listing_id);row.latitude=Decimal("18.5");db.commit()
        response=self.client.get("/listings/"+listing_id).json()
        self.assertIsNone(response["latitude"])
        self.assertIsNone(response["maps_url"])
        self.assertEqual(self.call("GET","/"+listing_id)["review_status"],"needs_review")
        self.assertEqual(self.call("GET","/summary")["needs_review"],1)

    def test_migration_is_idempotent_private_and_non_destructive(self):
        path=Path(__file__).resolve().parents[1]/"alembic/versions/b12d41e2c9f0_private_listing_location_review.py"
        spec=importlib.util.spec_from_file_location("synthetic_location_migration",path)
        migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
        with self.engine.begin() as connection:
            with patch.object(migration,"op",Operations(MigrationContext.configure(connection))):
                migration.upgrade();migration.upgrade();migration.downgrade()
            for table in ("listing_location_reviews","listing_location_review_revisions"):
                secure=connection.execute(text("SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND c.relname=:table"),{"schema":self.schema,"table":table}).scalar_one()
                self.assertTrue(secure)
                for role in ("anon","authenticated"):
                    if connection.execute(text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:role)"),{"role":role}).scalar():
                        self.assertFalse(connection.execute(text("SELECT has_table_privilege(:role,:table,'SELECT')"),{"role":role,"table":self.schema+"."+table}).scalar())
        self.assertEqual(self.client.get("/listings/map").json()["mapped_total"],1)


if __name__ == "__main__":
    unittest.main()
