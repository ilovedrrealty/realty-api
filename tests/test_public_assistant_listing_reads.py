"""Owned read-tool definitions: current public handlers, never live inventory.

The integration class requires the fixed disposable loopback PostgreSQL and
creates/drops only its random owned model schema. No main/lifespan/model calls.
These tests are not evidence of genuine migration or hosted tool acceptance.
"""
from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal
import json
import inspect
import os
import re
import smtplib
import unittest
from unittest.mock import MagicMock, patch
from urllib import request as urllib_request
from uuid import UUID, uuid4

os.environ.update(
    DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-public-tool-tests-only",
    GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
    SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="",
)

import httpx
import requests
import resend
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from database import Base
import models
import models.trade_contact
from models.listing import Listing
from models.listing_location_review import ListingLocationReview
from models.user import User
from routes import listings
from schemas.listing import PublicListingPageResponse
from services import public_assistant_listing_reads as tools
from services.public_assistant_listing import PublicListingBoundaryError


def search_json(**changes):
    return json.dumps({"locale": "en", "price_currency": "USD", "rental_period": "month", **changes})


def detail_json(number=1, **changes):
    return json.dumps({"locale": "es", "listing_id": str(UUID(int=number)), **changes})


class PublicAssistantListingReadContractTests(unittest.TestCase):
    def test_invalid_tool_input_never_opens_a_session(self):
        with patch.object(tools, "_new_session") as factory:
            for read, raw in ((tools.search_public_listings, search_json(url="https://foreign.invalid")),
                              (tools.read_public_listing, detail_json(listing_id="../private"))):
                with self.assertRaises(PublicListingBoundaryError):
                    read(raw)
            factory.assert_not_called()

    def test_explicit_handler_defaults_and_cleanup_without_http(self):
        db = MagicMock()
        db.get_bind.return_value.dialect.name = "postgresql"
        db.in_transaction.return_value = False
        db.new = db.dirty = db.deleted = set()
        db.no_autoflush = nullcontext()
        options = MagicMock(return_value={"locations": [], "types": []})

        def existing_search(**kwargs):
            self.assertEqual(set(kwargs), set(inspect.signature(listings.get_active_listings).parameters))
            self.assertEqual({k: kwargs[k] for k in ("type", "region", "locations", "types", "min_roi", "features", "exclude_ids", "shape")},
                             {k: None for k in ("type", "region", "locations", "types", "min_roi", "features", "exclude_ids", "shape")})
            self.assertFalse(kwargs["include_aggregates"])
            kwargs["response"].headers["Cache-Control"] = "no-store"
            return PublicListingPageResponse(items=[], total=0, page=1, page_size=12)

        with patch.object(tools, "_new_session", return_value=db), \
                patch.object(tools, "_handlers", return_value=(existing_search, None, options)):
            result = tools.search_public_listings(search_json())
        self.assertEqual(result.total, 0)
        options.assert_called_once_with(db=db)
        statements = [str(call.args[0]) for call in db.execute.call_args_list]
        self.assertEqual(statements, ["SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY",
                                     "SET LOCAL statement_timeout = '3s'", "SET LOCAL lock_timeout = '1s'"])
        db.rollback.assert_called_once_with()
        db.close.assert_called_once_with()
        db.commit.assert_not_called()

    def test_no_store_failure_is_finite_and_cleanup_still_runs(self):
        db = MagicMock()
        db.get_bind.return_value.dialect.name = "postgresql"
        db.in_transaction.return_value = False
        db.new = db.dirty = db.deleted = set()
        db.no_autoflush = nullcontext()
        handler = MagicMock(return_value=PublicListingPageResponse(items=[], total=0, page=1, page_size=12))
        with patch.object(tools, "_new_session", return_value=db), \
                patch.object(tools, "_handlers", return_value=(handler, None, lambda **_: {"locations": [], "types": []})):
            with self.assertRaisesRegex(tools.PublicListingReadError, "public_no_store_required"):
                tools.search_public_listings(search_json())
        db.rollback.assert_called_once_with()
        db.close.assert_called_once_with()


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires fixed owned loopback PostgreSQL")
class PublicAssistantListingReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if (url.get_backend_name() != "postgresql" or url.host != "127.0.0.1" or url.port != 55449
                or url.database != "ildr_platform_test_20261025"):
            raise ValueError("Use only the fixed disposable loopback database")
        cls.schema = "public_tool_test_" + uuid4().hex
        cls.bootstrap = create_engine(url, connect_args={"connect_timeout": 5})
        cls.addClassCleanup(cls.cleanup_schema)
        with cls.bootstrap.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema, "connect_timeout": 5},
                                   pool_size=3, max_overflow=0, pool_timeout=5)
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False)
        Base.metadata.create_all(cls.engine)

    @classmethod
    def cleanup_schema(cls):
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        assert re.fullmatch(r"public_tool_test_[a-f0-9]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as db:
                db.execute(text(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE'))
        finally:
            cls.bootstrap.dispose()

    def setUp(self):
        with self.engine.begin() as db:
            names = ",".join(f'"{self.schema}"."{table.name}"' for table in Base.metadata.sorted_tables)
            db.execute(text("TRUNCATE " + names + " RESTART IDENTITY CASCADE"))
        self.provider_attempts = []

        def refuse(*_args, **_kwargs):
            self.provider_attempts.append(1)
            raise AssertionError("External transport is forbidden")

        for patcher in (
            patch.object(httpx.HTTPTransport, "handle_request", refuse),
            patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse),
            patch.object(requests.Session, "send", refuse), patch.object(urllib_request, "urlopen", refuse),
            patch.object(resend.Emails, "send", refuse), patch.object(smtplib.SMTP, "connect", refuse),
            patch.object(smtplib.SMTP_SSL, "connect", refuse), patch.object(tools, "_new_session", self.Session),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        with self.Session.begin() as db:
            owner = User(id=UUID(int=100), email="synthetic-tools@example.invalid", display_name="Synthetic owner", role="admin")
            db.add(owner)
            db.flush()
            fixtures = [(1, "Sosúa", "sale", "USD", "200000", None, None),
                        (2, "Cabarete", "rent", "USD", "65", "1200", "65"),
                        (3, "Cabarete", "rent", "DOP", "999", "59500", "2380"),
                        (4, "Sosúa", "both", "USD", "300000", "2000", "150"),
                        (5, "Withdrawn private area", "sale", "USD", "1", None, None)]
            for number, location, transaction, currency, price, month, day in fixtures:
                db.add(Listing(id=UUID(int=number), title=f"Synthetic listing {number}", location=location,
                    description="PRIVATE_PROSE_CONTACT", description_es="PRIVATE_PROSA_CONTACTO",
                    type="villa", transaction=transaction, currency=currency, price=Decimal(price),
                    price_per_month=Decimal(month) if month else None, price_per_day=Decimal(day) if day else None,
                    bedrooms=3, bathrooms=2, images=[], features=[], submitted_by=owner.id,
                    status="archived" if number == 5 else "active", view_count=7,
                    owner_paypal_email="PRIVATE_PAYMENT@example.invalid", co_listing_notes="PRIVATE_AGREEMENT_NOTE",
                    co_listing_agent_email="PUBLIC_CONTACT_NOT_FOR_MODEL@example.invalid",
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))

    def tearDown(self):
        self.assertEqual(self.provider_attempts, [])

    def test_current_detail_withdrawal_is_rechecked_and_does_not_count_views(self):
        first = tools.read_public_listing(detail_json())
        self.assertEqual(first.item.title, "Synthetic listing 1")
        with self.Session.begin() as db:
            listing = db.get(Listing, UUID(int=1))
            self.assertEqual(listing.view_count, 7)
            listing.status = "archived"
        with self.assertRaisesRegex(tools.PublicListingReadError, "listing_not_available"):
            tools.read_public_listing(detail_json())
        with self.Session() as db:
            self.assertEqual(db.get(Listing, UUID(int=1)).view_count, 7)
        with self.assertRaisesRegex(tools.PublicListingReadError, "listing_not_available"):
            tools.read_public_listing(detail_json(99))

    def test_supported_options_are_read_again_without_a_caller_catalog(self):
        before = tools.search_public_listings(search_json(locations=["Sosúa"]))
        self.assertEqual({item.listing_id for item in before.items}, {str(UUID(int=1)), str(UUID(int=4))})
        with self.Session.begin() as db:
            db.get(Listing, UUID(int=1)).location = "Fresh synthetic town"
            db.get(Listing, UUID(int=4)).location = "Fresh synthetic town"
        after = tools.search_public_listings(search_json(locations=["Fresh synthetic town"]))
        self.assertEqual(after.total, 2)
        for location in ("Sosúa", "Withdrawn private area", "https://foreign.invalid"):
            with self.assertRaisesRegex(PublicListingBoundaryError, "known_public_locations_required"):
                tools.search_public_listings(search_json(locations=[location]))

    def test_real_route_day_month_and_currency_values_are_preserved(self):
        month = tools.search_public_listings(search_json(purpose="rent", sort="low"))
        self.assertEqual([item.listing_id for item in month.items], [str(UUID(int=n)) for n in (3, 2, 4)])
        self.assertEqual([item.comparable_price.amount for item in month.items], [Decimal("1000"), Decimal("1200"), Decimal("2000")])
        day = tools.search_public_listings(search_json(purpose="rent", price_currency="DOP", rental_period="day", sort="low"))
        self.assertEqual([item.comparable_price.amount for item in day.items], [Decimal("2380"), Decimal("3867.5"), Decimal("8925")])
        self.assertTrue(all(item.comparable_price.period == "day" and item.comparable_price.currency == "DOP" for item in day.items))
        self.assertEqual(month.items[0].monthly_rent.amount, Decimal("59500"))
        self.assertEqual(month.items[0].monthly_rent.currency, "DOP")

    def test_actual_search_paging_counts_and_empty_page_match_public_inventory(self):
        pages = [tools.search_public_listings(search_json(purpose="sale", sort="low", page_size=1, page=n)) for n in (1, 2, 3)]
        self.assertEqual([page.total for page in pages], [2, 2, 2])
        self.assertEqual([item.listing_id for page in pages for item in page.items], [str(UUID(int=1)), str(UUID(int=4))])
        self.assertEqual(pages[2].items, [])
        self.assertTrue(all(page.projected_at.tzinfo is not None for page in pages))

    def test_actual_masked_projection_never_forwards_private_contact_or_evidence(self):
        with self.Session.begin() as db:
            first = db.get(Listing, UUID(int=1))
            first.latitude, first.longitude = Decimal("19.75"), Decimal("-70.5")
            db.add(ListingLocationReview(listing_id=first.id, review_status="verified", public_precision="exact",
                latitude=first.latitude, longitude=first.longitude, review_note="PRIVATE_EXACT_EVIDENCE"))
            for number, precision in ((2, "area"), (3, "hidden")):
                db.add(ListingLocationReview(listing_id=UUID(int=number), review_status="needs_review", public_precision=precision,
                    latitude=Decimal("19.71"), longitude=Decimal("-70.41"), review_note="PRIVATE_AREA_EVIDENCE"))
        outputs = [tools.read_public_listing(detail_json(n)) for n in (1, 2, 3)]
        self.assertEqual([(out.item.location_precision, out.item.location_verified) for out in outputs],
                         [("exact", True), ("area", False), ("hidden", False)])
        self.assertEqual(outputs[1].item.location, "Cabarete")
        self.assertIsNone(outputs[2].item.location)
        outputs.append(tools.search_public_listings(search_json()))
        for output in outputs:
            serialized = output.model_dump_json()
            for secret in ("PRIVATE_", "PUBLIC_CONTACT_NOT_FOR_MODEL", "example.invalid", "latitude", "longitude", "owner_id", "co_listing", "description"):
                # The explicit description-excluded flag is expected metadata.
                self.assertNotIn(secret, serialized.replace("public_description_included", "prose_omitted"))

    def test_read_only_transaction_refuses_handler_write_and_restores_pool_state(self):
        captured = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            captured.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture)
        self.addCleanup(event.remove, self.engine, "before_cursor_execute", capture)
        original_search, detail, options = tools._handlers()

        def unsafe_search(**kwargs):
            kwargs["db"].execute(text("UPDATE listings SET view_count = view_count + 1"))
            return original_search(**kwargs)

        with patch.object(tools, "_handlers", return_value=(unsafe_search, detail, options)):
            with self.assertRaisesRegex(tools.PublicListingReadError, "public_read_unavailable") as failure:
                tools.search_public_listings(search_json())
        self.assertNotIn("UPDATE", str(failure.exception))
        result = tools.search_public_listings(search_json())
        self.assertEqual(result.total, 4)
        self.assertGreaterEqual(sum("READ ONLY" in statement for statement in captured), 2)
        with self.Session() as db:
            self.assertEqual({row.view_count for row in db.query(Listing).all()}, {7})
            self.assertEqual(db.execute(text("SHOW transaction_read_only")).scalar(), "off")


if __name__ == "__main__":
    unittest.main()
