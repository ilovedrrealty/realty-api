"""Pure public/private DTO boundaries; no application, settings, DB or provider."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import unittest
from uuid import UUID

from schemas.listing import (
    ListingResponse,
    PublicListingDetailResponse,
    PublicListingPageResponse,
    PublicListingResponse,
)


PRIVATE_VALUES = {
    "owner_paypal_email": "private-payee@example.invalid",
    "co_listing_notes": "PRIVATE_BROKER_NOTES",
    "co_listing_commission_split": Decimal("42"),
    "co_listing_status": "PRIVATE_NEGOTIATION_STATE",
    "co_listing_agreement_accepted": True,
    "co_listing_agreement_url": "https://private.example.invalid/agreement.pdf",
    "rejection_reason": "PRIVATE_REVIEW_REASON",
    "source_ref": "PRIVATE_IMPORT_SOURCE",
    "submitted_by": UUID(int=91),
    "submitted_by_email": "private-account@example.invalid",
    "owner_id": UUID(int=92),
    "assigned_realtor_id": UUID(int=93),
    "leads_count": 47,
    "has_pending_deal_request": True,
    "has_pending_edit": True,
}


def listing_values():
    return {
        "id": UUID(int=1), "title": "Synthetic published property",
        "description": "Public description", "description_es": "Descripción pública",
        "type": "villa", "transaction": "rent", "price": Decimal("1200"),
        "location": "Published area", "bedrooms": 2, "bathrooms": 1.5,
        "area_sqft": 1000, "lot_size_sqft": 2000, "roi": Decimal("4.5"),
        "seller_financing": False, "hoa": True, "hoa_fee": Decimal("10"),
        "tax_exempt": False, "gated_community": True, "construction_status": "completed",
        "year_built": 2024, "features": ["Pool"], "maps_url": None,
        "latitude": None, "longitude": None, "tag": "For Rent", "tags": ["For Rent"],
        "video_links": ["https://media.example.invalid/video"],
        "tour_3d_url": "https://media.example.invalid/tour",
        "images": ["https://media.example.invalid/room.jpg"],
        "status": "active", "currency": "USD", "price_per_day": Decimal("60"),
        "price_per_month": Decimal("1200"), "search_price": Decimal("71400"),
        "search_price_currency": "DOP", "search_price_period": "month",
        "co_listing_enabled": True, "co_listing_brokerage": "Published brokerage",
        "co_listing_agent_name": "Published business contact",
        "co_listing_agent_email": "public-broker@example.invalid",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 2, 1, tzinfo=timezone.utc),
        "submitted_by_name": "Published advisor",
        **PRIVATE_VALUES,
    }


class PublicListingProjectionTests(unittest.TestCase):
    def test_public_projection_never_reads_private_orm_attributes(self):
        class GuardedRow(SimpleNamespace):
            def __getattribute__(self, name):
                if name in PRIVATE_VALUES:
                    raise AssertionError("Private ORM attribute read")
                return super().__getattribute__(name)

        row = GuardedRow(**listing_values())
        public = PublicListingResponse.model_validate(row)
        data = public.model_dump()
        self.assertEqual(data["title"], "Synthetic published property")
        self.assertIsNone(data["submitted_by_email"])
        self.assertFalse((PRIVATE_VALUES.keys() - {"submitted_by_email"}) & data.keys())

    def test_private_response_retains_operational_values_but_public_output_does_not(self):
        private = ListingResponse.model_validate(listing_values())
        before = private.model_dump()
        public = PublicListingResponse.model_validate(private).model_dump()
        for key, value in PRIVATE_VALUES.items():
            self.assertEqual(before[key], value)
        self.assertEqual(private.model_dump(), before)
        self.assertIsNone(public["submitted_by_email"])
        self.assertFalse((PRIVATE_VALUES.keys() - {"submitted_by_email"}) & public.keys())
        self.assertEqual(public["co_listing_agent_email"], "public-broker@example.invalid")

    def test_page_and_detail_preserve_public_prices_media_location_and_contact_contract(self):
        values = listing_values()
        values.update(location_precision="area", location_verified=False,
                      future_private_field="PRIVATE_FUTURE_FIELD")
        public = PublicListingResponse.model_validate(values)
        page = PublicListingPageResponse(items=[public], total=1, page=1, page_size=24)
        tour = {"groups": [{"id": "living-room", "label_en": "Living room", "label_es": "Sala",
                            "photos": [{"url": values["images"][0]}]}]}
        detail = PublicListingDetailResponse(**public.model_dump(), room_photo_tour=tour)
        data = detail.model_dump()
        for key in ("id", "title", "description", "description_es", "price", "currency",
                    "price_per_day", "price_per_month", "search_price", "search_price_currency",
                    "search_price_period", "images", "video_links", "tour_3d_url", "latitude",
                    "longitude", "maps_url", "location_precision", "location_verified",
                    "submitted_by_name", "created_at", "updated_at"):
            self.assertEqual(data[key], values[key])
        self.assertEqual(page.model_dump()["items"][0], public.model_dump())
        self.assertEqual(data["room_photo_tour"]["groups"][0]["photos"][0]["url"], values["images"][0])
        self.assertIsNone(data["submitted_by_email"])
        self.assertFalse((PRIVATE_VALUES.keys() - {"submitted_by_email"}) & data.keys())
        self.assertNotIn("future_private_field", data)


if __name__ == "__main__":
    unittest.main()
