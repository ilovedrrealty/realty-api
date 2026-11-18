"""Public asking-market snapshot tests against an owned loopback Postgres."""

import os
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

os.environ.update(
    DATABASE_URL="sqlite+pysqlite:///:memory:",
    JWT_SECRET="synthetic-market-report-tests-only",
    GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="",
    RESEND_API_KEY="", SUPABASE_URL="", SUPABASE_SERVICE_KEY="",
    PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="",
)

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from database import Base, get_db
from models.listing import Listing
from models.listing_location_review import ListingLocationReview
from models.user import User
from routes import market_reports


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires disposable loopback Postgres")
class MarketReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host not in ("127.0.0.1", "localhost", "::1") or url.get_backend_name() != "postgresql":
            raise ValueError("Market report tests require a separate loopback-only Postgres")
        cls.schema = f"market_report_test_{uuid4().hex}"
        cls.engine = create_engine(url)
        cls.connection = cls.engine.connect()
        cls.connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.connection.execute(text(f'SET search_path TO "{cls.schema}"'))
        Base.metadata.create_all(cls.connection, tables=[User.__table__, Listing.__table__, ListingLocationReview.__table__])
        cls.connection.commit()
        cls.db = Session(cls.connection)
        owner = User(role="admin", display_name="Synthetic hidden owner", email="market-owner@example.invalid")
        cls.db.add(owner)
        cls.db.flush()

        def listing(number, price, **fields):
            common = dict(
                id=UUID(int=number), location="Sosúa", type="villa", currency="USD",
                title=f"Synthetic private fixture label {number}", transaction="sale", status="active",
                submitted_by=owner.id, price=Decimal(price),
            )
            return Listing(**{**common, **fields})

        rows = [
            listing(1, "100.01", location="  SOSU\u0301A\t", area_sqft=1000),
            listing(2, "100.02", transaction="both", area_sqft=2000,
                    price_per_month=700, price_per_day=50, is_deal=True, deal_discount_value=20),
            listing(3, "400.00", area_sqft=1000),
            listing(4, "500.00", location="Sosúa extended private location", area_sqft=1000),
            listing(5, "995000.50", currency="DOP", area_sqft=1000),
            listing(6, "500000.00", type="apartment", currency="DOP", lot_size_sqft=555),
            listing(7, "99999.99", type="land", area_sqft=100, lot_size_sqft=1000),
            listing(8, "30000.00", type="land", area_sqft=1400),
            listing(9, "10000.00", type="commercial", area_sqft=5000, lot_size_sqft=4000),
            listing(10, "2000.00", status="archived"),
            listing(11, "2000.00", status="rejected"),
            listing(12, "2000.00", status="draft"),
            listing(13, "2000.00", status="pending_approval"),
            listing(14, "2000.00", transaction="rent", price_per_month=500),
            listing(15, "3.00", type="condo", area_sqft=-3),
            listing(16, "0.00", type="condo", area_sqft=123),
            listing(17, "-5.00", type="condo", area_sqft=100),
            listing(18, "NaN", type="condo", area_sqft=100),
            listing(19, "300.00", currency="EUR"),
            listing(20, "9999999999.99", type="commercial"),
        ]
        cls.db.add_all(rows)
        cls.db.flush()
        cls.db.add(ListingLocationReview(
            listing_id=rows[2].id, public_precision="hidden",
            latitude=Decimal("19.75"), longitude=Decimal("-70.50"),
            source_reference="synthetic private address evidence",
        ))
        cls.db.add(ListingLocationReview(listing_id=rows[1].id, public_precision="area"))
        cls.db.commit()
        app = FastAPI()
        app.include_router(market_reports.router)
        app.dependency_overrides[get_db] = lambda: cls.db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.db.close()
        cls.connection.rollback()
        assert cls.schema.startswith("market_report_test_") and len(cls.schema) == 51
        cls.connection.execute(text("SET search_path TO public"))
        cls.connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.connection.commit()
        cls.connection.close()
        cls.engine.dispose()

    def report(self, **params):
        response = self.client.get("/site/market-reports", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        return response, response.json()

    @staticmethod
    def group(report, property_type, currency="USD"):
        return next(group for group in report["groups"]
                    if (group["property_type"], group["currency"]) == (property_type, currency))

    def test_site_snapshot_uses_one_statement_reports_own_scope_and_no_listing_details(self):
        statements = []

        def count_statement(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(self.connection, "before_cursor_execute", count_statement)
        try:
            before = datetime.now(timezone.utc)
            response, data = self.report()
            after = datetime.now(timezone.utc)
        finally:
            event.remove(self.connection, "before_cursor_execute", count_statement)
        self.assertEqual(len(statements), 1)
        self.assertEqual(set(data), {
            "as_of", "local_date", "location", "total_listings", "unclassified_currency_count", "groups",
        })
        as_of = datetime.fromisoformat(data["as_of"])
        self.assertLessEqual(before, as_of)
        self.assertLessEqual(as_of, after)
        self.assertEqual(data["local_date"], as_of.astimezone(ZoneInfo("America/Santo_Domingo")).date().isoformat())
        self.assertIsNone(data["location"])
        self.assertEqual(data["total_listings"], 15)
        self.assertEqual(data["unclassified_currency_count"], 1)
        self.assertEqual(sum(group["listing_count"] for group in data["groups"]), 14)
        self.assertEqual(self.group(data, "villa")["listing_count"], 4)
        for private in ("fixture label", "extended private location", "market-owner", "private address evidence",
                        "latitude", "longitude", "listing_id", "contact", "review"):
            self.assertNotIn(private, response.text)

    def test_canonical_exact_area_excludes_hidden_evidence_and_preserves_native_sale_prices(self):
        _, data = self.report(location=" SOSU\u0301A ")
        self.assertEqual(data["location"], "Sosúa")
        self.assertEqual(data["total_listings"], 13)
        self.assertEqual(data["unclassified_currency_count"], 1)
        usd = self.group(data, "villa")
        self.assertEqual(usd["listing_count"], 2)
        self.assertEqual(usd["price_count"], 2)
        self.assertEqual(usd["size_count"], 2)
        self.assertEqual(usd["price_min"], "100.01")
        self.assertEqual(usd["price_median"], "100.015")
        self.assertEqual(usd["price_max"], "100.02")
        self.assertEqual(usd["median_price_per_sqm"], "0.81")
        dop = self.group(data, "villa", "DOP")
        self.assertEqual(dop["listing_count"], 1)
        self.assertEqual(dop["price_median"], "995000.500")

    def test_size_basis_and_price_counts_are_separate_and_valid_high_prices_are_kept(self):
        _, data = self.report()
        land = self.group(data, "land")
        self.assertEqual((land["area_basis"], land["listing_count"], land["price_count"], land["size_count"]),
                         ("lot", 2, 2, 1))
        self.assertEqual(land["median_price_per_sqm"], "1076.39")
        apt = self.group(data, "apartment", "DOP")
        self.assertEqual((apt["area_basis"], apt["price_count"], apt["size_count"]), ("living", 1, 0))
        self.assertIsNone(apt["median_price_per_sqm"])
        commercial = self.group(data, "commercial")
        self.assertEqual((commercial["area_basis"], commercial["price_count"], commercial["size_count"]), (None, 2, 0))
        self.assertEqual(commercial["price_max"], "9999999999.99")
        self.assertIsNone(commercial["median_price_per_sqm"])
        condos = self.group(data, "condo")
        self.assertEqual((condos["listing_count"], condos["price_count"], condos["size_count"]), (4, 1, 0))
        self.assertEqual(condos["price_min"], "3.00")

    def test_empty_known_area_and_invalid_or_repeated_filters_are_distinct(self):
        _, empty = self.report(location="Santiago")
        self.assertEqual((empty["location"], empty["total_listings"], empty["groups"]), ("Santiago", 0, []))
        for params in ([("location", "")], [("location", "private label")],
                       [("location", "x" * 81)], [("location", "Sosúa"), ("location", "Sosúa")]):
            response = self.client.get("/site/market-reports", params=params)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.json(), {"detail": "Choose one recognized listing area."})

    def test_unpublishing_updates_all_counts_and_price_statistics_at_once(self):
        listing = self.db.get(Listing, UUID(int=1))
        listing.status = "archived"
        self.db.commit()
        try:
            _, data = self.report(location="Sosúa")
            usd = self.group(data, "villa")
            self.assertEqual(data["total_listings"], 12)
            self.assertEqual((usd["listing_count"], usd["price_count"], usd["size_count"]), (1, 1, 1))
            self.assertEqual(usd["price_median"], "100.020")
        finally:
            listing.status = "active"
            self.db.commit()
