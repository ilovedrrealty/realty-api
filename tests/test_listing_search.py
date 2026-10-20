"""Synthetic search integration tests against a separate, loopback-only Postgres.

Set SEARCH_FILTER_TEST_DATABASE_URL to a disposable local Postgres database,
then run `python -m unittest discover -s tests -v`. A random schema is created
and removed. No production database, keys or external services are used.
"""

import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

# Isolate all application configuration before importing it.
os.environ.update(
    DATABASE_URL="sqlite+pysqlite:///:memory:",
    JWT_SECRET="synthetic-search-tests-only",
    GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="",
    RESEND_API_KEY="", SUPABASE_URL="", SUPABASE_SERVICE_KEY="",
    PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="",
)

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from database import Base, get_db
from models.listing import Listing
from models.user import User
from routes import listings


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires disposable local Postgres")
class ListingSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host not in ("127.0.0.1", "localhost", "::1") or url.get_backend_name() != "postgresql":
            raise ValueError("Search tests require a separate loopback-only Postgres")
        cls.schema = "search_filter_test_" + uuid4().hex
        cls.engine = create_engine(url)
        cls.connection = cls.engine.connect()
        cls.connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.connection.execute(text(f'SET search_path TO "{cls.schema}"'))
        Base.metadata.create_all(cls.connection)
        cls.connection.commit()
        cls.db = Session(cls.connection)
        owner = User(email="synthetic-search@example.invalid", display_name="Synthetic search owner", role="admin")
        cls.db.add(owner)
        cls.db.flush()
        fixtures = [
            (1, "Sosúa", "villa", "sale", "USD", 200000, None, None),
            (2, "SOSU\u0301A", "apartment", "sale", "USD", 100000, None, None),
            (3, " Punta  Cana\t", "villa", "sale", "DOP", 11900000, None, None),
            (4, "Cabarete", "villa", "rent", "USD", 65, 1200, 65),
            (5, "Cabarete", "apartment", "rent", "USD", 50, 700, 50),
            (6, "Cabarete", "condo", "rent", "USD", 300, None, 300),
            (7, "San Pedro de Macorís", "land", "sale", "USD", 10000, None, None),
            (8, "San Pedro de Macoris", "land", "sale", "USD", 20000, None, None),
            (9, "Sosúa", "villa", "both", "USD", 300000, 2000, 150),
            (10, "50%_Town", "commercial", "sale", "USD", 900000, None, None),
            (11, "Cabarete", "villa", "rent", "DOP", 2380, 59500, 2380),
        ]
        for n, location, property_type, transaction, currency, price, month, day in fixtures:
            cls.db.add(Listing(
                id=UUID(int=n), title=f"Synthetic property {n}", location=location,
                type=property_type, transaction=transaction, currency=currency,
                price=price, price_per_month=month, price_per_day=day,
                bedrooms=3, bathrooms=2, area_sqft=1000, features=["Pool"] if n in (1, 3) else [],
                images=[], submitted_by=owner.id, status="active",
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ))
        cls.db.add(Listing(
            id=UUID(int=12), title="Inactive synthetic property", location="Hidden town",
            type="villa", transaction="sale", currency="USD", price=1,
            features=[], images=[], submitted_by=owner.id, status="archived",
        ))
        cls.db.commit()
        app = FastAPI()
        app.include_router(listings.router)
        app.dependency_overrides[get_db] = lambda: cls.db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.db.close()
        cls.connection.rollback()
        # Only the random schema owned by this test is removed.
        assert cls.schema.startswith("search_filter_test_") and len(cls.schema) == 51
        cls.connection.execute(text("SET search_path TO public"))
        cls.connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.connection.commit()
        cls.connection.close()
        cls.engine.dispose()

    def get_search(self, params=()):
        response = self.client.get("/listings", params=list(params))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @contextmanager
    def map_coordinates(self):
        originals = {}
        for number in range(1, 13):
            row = self.db.get(Listing, UUID(int=number))
            originals[number] = (row.latitude, row.longitude)
            row.latitude = Decimal('19.70') + Decimal(number) / 100 if number in (1, 2, 3, 4, 9, 11, 12) else None
            row.longitude = Decimal('-70.4') if row.latitude is not None else None
            if number == 5:
                row.latitude = Decimal('19.7')  # Partial pair remains unplaced.
            if number == 6:
                row.latitude, row.longitude = Decimal('19.7'), Decimal('181')
        self.db.commit()
        try:
            yield
        finally:
            self.db.rollback()
            for number, original in originals.items():
                row = self.db.get(Listing, UUID(int=number))
                row.latitude, row.longitude = original
            self.db.commit()

    def get_map(self, params=()):
        response = self.client.get('/listings/map', params=list(params))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_map_covers_all_public_matches_beyond_card_pagination_and_exposes_only_whitelisted_fields(self):
        with self.map_coordinates():
            first = self.get_search([('page_size', 2)])
            second = self.get_search([('page_size', 2), ('page', 2)])
            result = self.get_map([('page', 2), ('page_size', 2)])
            self.assertEqual(result['total'], 11)
            self.assertEqual(result['mapped_total'], 6)
            self.assertEqual(result['unmapped_total'], 5)
            self.assertEqual(result['returned'], 6)
            self.assertEqual(set(self.ids(result)), {1, 2, 3, 4, 9, 11})
            self.assertTrue(set(self.ids(result)) - set(self.ids(first)) - set(self.ids(second)))
            self.assertNotIn(12, self.ids(result))  # Archived listing has valid coordinates but stays private.
            self.assertEqual(sum(area['count'] for area in result['areas']) + result['ungrouped_total'], 5)
            for item in result['items']:
                self.assertEqual(set(item), {'id', 'title', 'location', 'latitude', 'longitude', 'price', 'currency', 'transaction', 'price_period'})
            self.assertFalse(result['truncated'])

    def test_map_uses_the_exact_card_filter_and_currency_contract_including_multiple_areas_and_rental_periods(self):
        with self.map_coordinates():
            cases = [
                [('locations', 'sosua'), ('locations', 'Punta Cana'), ('types', 'villa'), ('types', 'apartment'), ('features', 'Pool')],
                [('purpose', 'sale'), ('price_currency', 'USD'), ('usd_to_dop', 59.5), ('min_price', 190000), ('max_price', 210000)],
                [('purpose', 'rent'), ('price_currency', 'USD'), ('usd_to_dop', 59.5), ('rental_period', 'month'), ('max_price', 1000)],
                [('purpose', 'rent'), ('price_currency', 'USD'), ('usd_to_dop', 59.5), ('rental_period', 'day'), ('max_price', 70)],
                [('region', 'Sosúa'), ('type', 'villa'), ('beds', 3), ('exclude_ids', str(UUID(int=9)))],
            ]
            for params in cases:
                with self.subTest(params=params):
                    cards, result = self.get_search(params), self.get_map(params)
                    self.assertEqual(result['total'], cards['total'])
                    expected = {item['id']: item for item in cards['items'] if item['latitude'] is not None and item['longitude'] is not None and abs(float(item['longitude'])) <= 180}
                    self.assertEqual({item['id'] for item in result['items']}, set(expected))
                    for item in result['items']:
                        card = expected[item['id']]
                        self.assertEqual(item['price'], card['search_price'] if card['search_price_currency'] else card['price'])
                        self.assertEqual(item['currency'], card['search_price_currency'] or card['currency'])
                        self.assertEqual(item['price_period'], card['search_price_period'])

    def test_map_limits_and_viewport_are_explicit_while_full_filter_totals_and_area_counts_remain_global(self):
        with self.map_coordinates():
            limited = self.get_map([('limit', 1)])
            self.assertTrue(limited['truncated'])
            self.assertEqual(limited['returned'], 1)
            self.assertEqual(limited['mapped_total'], 6)
            viewport = self.get_map([('south', 19.705), ('north', 19.725), ('west', -71), ('east', -70)])
            self.assertEqual(set(self.ids(viewport)), {1, 2})
            self.assertEqual(viewport['total'], 11)
            self.assertEqual(viewport['mapped_total'], 6)
            self.assertEqual(viewport['areas'], limited['areas'])
            self.assertEqual(viewport['viewport_total'], 2)
            for params in ([('limit', 5001)], [('south', 19)], [('south', 20), ('north', 19), ('west', -71), ('east', -70)], [('south', 'nan')]):
                self.assertEqual(self.client.get('/listings/map', params=params).status_code, 422)

    @staticmethod
    def ids(result):
        return [UUID(item["id"]).int for item in result["items"]]

    def test_multiple_locations_and_types_use_or_within_and_across(self):
        result = self.get_search([
            ("locations", "sosua"), ("locations", "Cabarete"),
            ("types", "villa"), ("types", "apartment"),
        ])
        self.assertEqual(set(self.ids(result)), {1, 2, 4, 5, 9, 11})
        self.assertEqual(result["total"], 6)
        result = self.get_search([("locations", "sosua"), ("types", "villa"), ("features", "Pool")])
        self.assertEqual(self.ids(result), [1])

    def test_normalization_legacy_links_and_multi_precedence(self):
        for value in ("Sosua", "Sosúa", " SOSÚA ", "Sosu\u0301a"):
            self.assertEqual(set(self.ids(self.get_search([("locations", value)]))), {1, 2, 9})
        self.assertEqual(set(self.ids(self.get_search([("locations", "San Pedro de Macoris")]))), {7, 8})
        for value in ("Punta Cana", "  Punta   Cana\t"):
            self.assertEqual(self.ids(self.get_search([("locations", value)])), [3])
        self.assertEqual(set(self.ids(self.get_search([("region", "sosua"), ("type", "Villa")]))), {1, 9})
        result = self.get_search([
            ("region", "Punta Cana"), ("locations", "Sosua"),
            ("type", "land"), ("types", "apartment"),
        ])
        self.assertEqual(self.ids(result), [2])

    def test_location_wildcards_are_literals(self):
        self.assertEqual(self.get_search([("locations", "%")])["total"], 0)
        self.assertEqual(self.ids(self.get_search([("locations", "50%_Town")])), [10])
        self.assertEqual(self.ids(self.get_search([("region", "%_")])), [10])

    def test_explicit_sale_rent_and_omitted_purpose(self):
        self.assertEqual(set(self.ids(self.get_search([("purpose", "sale")]))), {1, 2, 3, 7, 8, 9, 10})
        self.assertEqual(set(self.ids(self.get_search([("purpose", "rent")]))), {4, 5, 6, 9, 11})
        self.assertEqual(self.get_search()["total"], 11)

    def test_count_pagination_and_tied_sort_stay_consistent(self):
        params = [("locations", "Sosua"), ("locations", "Cabarete"), ("page_size", 2)]
        pages = [self.get_search(params + [("page", page)]) for page in range(1, 5)]
        self.assertTrue(all(page["total"] == 7 for page in pages))
        combined = [value for page in pages for value in self.ids(page)]
        self.assertEqual(combined, [11, 9, 6, 5, 4, 2, 1])
        self.assertEqual(len(combined), len(set(combined)))

    def test_filter_options_merge_variants_exclude_inactive_and_remain_compact(self):
        response = self.client.get("/listings/filter-options")
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        locations = {option["value"]: option["count"] for option in result["locations"]}
        self.assertEqual(locations["Sosúa"], 3)
        self.assertEqual(locations["San Pedro de Macorís"], 2)
        self.assertNotIn("Hidden town", locations)
        self.assertEqual(sum(locations.values()), 11)
        self.assertEqual(sum(option["count"] for option in result["types"]), 11)
        self.assertTrue(all(set(option) == {"value", "count"} for option in result["locations"] + result["types"]))

    def test_sale_prices_convert_dop_and_apply_same_budget_sort_median(self):
        params = [("purpose", "sale"), ("price_currency", "USD"), ("usd_to_dop", 59.5)]
        result = self.get_search(params + [("min_price", 195000), ("max_price", 205000), ("sort", "low")])
        self.assertEqual(set(self.ids(result)), {1, 3})
        self.assertEqual(result["median_price"], 200000)
        for item in result["items"]:
            self.assertEqual(Decimal(item["search_price"]), 200000)
            self.assertEqual(item["search_price_currency"], "USD")
            self.assertIsNone(item["search_price_period"])
        native = next(item for item in result["items"] if UUID(item["id"]).int == 3)
        self.assertEqual(native["currency"], "DOP")
        self.assertEqual(Decimal(native["price"]), 11900000)
        all_sales = self.get_search(params + [("sort", "low")])
        self.assertEqual(self.ids(all_sales), [7, 8, 2, 3, 1, 9, 10])

    def test_monthly_and_daily_rental_prices_are_never_generic_price(self):
        params = [("purpose", "rent"), ("price_currency", "USD"), ("usd_to_dop", 59.5)]
        monthly = self.get_search(params + [("max_price", 1000), ("sort", "low")])
        self.assertEqual(self.ids(monthly), [5, 11])
        self.assertEqual(monthly["median_price"], 850)
        self.assertTrue(all(item["search_price_period"] == "month" for item in monthly["items"]))
        daily = self.get_search(params + [("rental_period", "day"), ("max_price", 70), ("sort", "low")])
        self.assertEqual(self.ids(daily), [11, 5, 4])
        self.assertEqual(daily["median_price"], 50)
        all_monthly = self.get_search(params + [("sort", "high")])
        self.assertEqual(self.ids(all_monthly), [9, 4, 11, 5, 6])
        self.assertIsNone(all_monthly["items"][-1]["search_price"])
        self.assertEqual(Decimal(all_monthly["items"][-1]["price"]), 300)

    def test_target_dop_changes_comparison_currency_without_editing_native_prices(self):
        result = self.get_search([
            ("purpose", "sale"), ("locations", "Sosua"), ("price_currency", "DOP"),
            ("usd_to_dop", 60), ("min_price", 11900000), ("max_price", 12100000),
        ])
        self.assertEqual(self.ids(result), [1])
        self.assertEqual(Decimal(result["items"][0]["search_price"]), 12000000)
        self.assertEqual(Decimal(result["items"][0]["price"]), 200000)
        self.assertEqual(self.db.get(Listing, UUID(int=1)).currency, "USD")

    def test_clients_omitting_price_currency_keep_legacy_price_contract(self):
        result = self.get_search([("purpose", "rent"), ("max_price", 1)])
        # Actual rent records keep the old budget exemption; explicit 'both'
        # still uses its sale price unless the currency contract is enabled.
        self.assertEqual(set(self.ids(result)), {4, 5, 6, 11})
        self.assertTrue(all(item["search_price"] is None for item in result["items"]))

    def test_untrusted_list_and_currency_inputs_are_bounded(self):
        invalid = [
            [("locations", "")], [("locations", " ")], [("locations", "x" * 81)],
            [("locations", "Sosua")] * 61,
            [("types", "castle")], [("types", "villa")] * 6,
            [("price_currency", "EUR")], [("rental_period", "week")],
        ]
        invalid.extend([[("usd_to_dop", value)] for value in ("nan", "inf", "-inf", "0", "1001")])
        for params in invalid:
            with self.subTest(params=params):
                response = self.client.get("/listings", params=params)
                self.assertEqual(response.status_code, 422, response.text)
