"""Pure future-orchestrator boundary cases; no HTTP, app, settings or database."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import unittest
from uuid import UUID

from pydantic import ValidationError

from schemas.listing import ListingFilterOptionsResponse, PublicListingPageResponse, PublicListingResponse
from schemas.public_assistant_listing import PublicListingDetailInput, PublicListingSearchInput
from services.public_assistant_listing import (
    CANONICAL_ORIGIN, MAX_RESULT_BYTES, MAX_TOOL_INPUT_BYTES, PublicListingBoundaryError,
    parse_detail_input, parse_search_input, prepare_detail, prepare_search, project_detail, project_search,
)

ID = "00000000-0000-0000-0000-000000000001"
NOW = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)


def row(**changes):
    values = dict(id=UUID(ID), title="Villa de muestra", description="PRIVATE_PROSE_CONTACT_NOT_RETRIEVED",
        type="villa", transaction="both", price=Decimal("250000"), currency="USD", location="Sosúa",
        bedrooms=3, bathrooms=2.5, area_sqft=2000, lot_size_sqft=3000, roi=Decimal("6"), seller_financing=False,
        hoa=False, hoa_fee=None, tax_exempt=False, gated_community=False, construction_status="completed",
        year_built=None, features=[], maps_url=None, images=[], tag=None,
        price_per_day=Decimal("80"), price_per_month=Decimal("1500"), status="active",
        search_price=Decimal("89250"), search_price_currency="DOP", search_price_period="month",
        updated_at=NOW, location_precision="area", location_verified=False,
        co_listing_agent_email="PRIVATE_STRUCTURED_CONTACT@example.invalid")
    values.update(changes)
    return PublicListingResponse.model_validate(values)


def search(**changes):
    return PublicListingSearchInput.model_validate({"locale":"es", "purpose":"rent", "price_currency":"DOP",
        "rental_period":"month", "page_size":12, **changes})


class PublicAssistantListingTests(unittest.TestCase):
    def test_wire_inputs_reject_unknown_fields_nonfinite_duplicates_and_size(self):
        valid = dict(locale="en", price_currency="USD", rental_period="day")
        self.assertEqual(parse_search_input(json.dumps(valid)).locale, "en")
        for invalid in ({**valid, "url":"https://foreign.invalid"}, {**valid, "sql":"SELECT private"},
                        {**valid, "locale":"fr"}, {**valid, "page":"1"}, {**valid, "page":True},
                        {**valid, "page_size":25}, {**valid, "min_price":9, "max_price":1},
                        {**valid, "types":["villa","villa"]}, {**valid, "types":["unknown"]}):
            with self.assertRaises(PublicListingBoundaryError):
                parse_search_input(json.dumps(invalid))
        for raw in ('{"locale":"en","locale":"es"}', '{"min_price":NaN}', '[]', b'\xff',
                    ' ' * (MAX_TOOL_INPUT_BYTES + 1)):
            with self.assertRaises(PublicListingBoundaryError):
                parse_search_input(raw)

    def test_search_request_only_uses_existing_fixed_route_and_current_options(self):
        request = search(locations=["Sosúa", "Cabarete"], types=["villa", "condo"], min_price=10, max_price=900)
        options = ListingFilterOptionsResponse(locations=[{"value":"Sosúa","count":1},{"value":"Cabarete","count":1}], types=[])
        prepared = prepare_search(request, options)
        self.assertEqual((prepared.method, prepared.path), ("GET", "/listings"))
        self.assertEqual([v for k,v in prepared.query if k=="locations"], ["Sosúa", "Cabarete"])
        self.assertEqual([v for k,v in prepared.query if k=="types"], ["villa", "condo"])
        self.assertEqual(dict(prepared.query)["price_currency"], "DOP")
        self.assertEqual(dict(prepared.query)["rental_period"], "month")
        self.assertEqual(dict(prepared.query)["include_aggregates"], "false")
        for location in ("https://foreign.invalid", "Sosúa; SELECT private", "Unknown place"):
            with self.assertRaisesRegex(PublicListingBoundaryError, "known_public_locations_required"):
                prepare_search(search(locations=[location]), options)

    def test_detail_uuid_cannot_become_arbitrary_url_path_or_sql(self):
        request = parse_detail_input(json.dumps({"locale":"en", "listing_id":ID.upper()}))
        self.assertEqual(prepare_detail(request).path, "/listings/" + ID)
        for value in ("https://foreign.invalid/" + ID, "../" + ID, "SELECT private", ID + "?token=private"):
            with self.assertRaises(PublicListingBoundaryError):
                parse_detail_input(json.dumps({"locale":"en", "listing_id":value}))

    def test_projection_does_not_read_structured_contacts_or_prose(self):
        forbidden = {"description", "description_es", "co_listing_agent_email", "co_listing_agent_contact",
                     "co_listing_brokerage_email", "submitted_by_email", "submitted_by_name", "images", "maps_url"}
        class GuardedPublic(PublicListingResponse):
            def __getattribute__(self, name):
                if name in forbidden:
                    raise AssertionError("Unnecessary field read")
                return super().__getattribute__(name)
        source = GuardedPublic.model_validate(row().model_dump())
        result = project_detail(PublicListingDetailInput(locale="es", listing_id=ID), source, projected_at=NOW)
        text = result.model_dump_json()
        self.assertNotIn("PRIVATE_", text)
        self.assertNotIn("example.invalid", text)
        self.assertFalse(result.item.public_description_included)
        self.assertFalse(result.item.availability_verified)
        self.assertEqual(result.item.title, "Villa de muestra")
        self.assertEqual(result.item.citation.url, CANONICAL_ORIGIN + "/listing/" + ID + "/")
        self.assertEqual(result.item.citation.label, "Ficha pública")

    def test_search_comparison_price_is_route_value_not_a_new_conversion(self):
        request = search()
        listing = row(search_price=Decimal("89123.45"))
        page = PublicListingPageResponse(items=[listing],total=1,page=1,page_size=12)
        result = project_search(request, page, projected_at=NOW)
        self.assertEqual(result.items[0].comparable_price.amount, Decimal("89123.45"))
        self.assertEqual(result.items[0].comparable_price.currency, "DOP")
        self.assertEqual(result.items[0].comparable_price.period, "month")
        self.assertEqual(result.items[0].monthly_rent.amount, Decimal("1500"))
        self.assertEqual(result.items[0].sale_price.amount, Decimal("250000"))
        self.assertEqual(result.fx_kind, "route_default_estimate")
        for changes in ({"search_price_currency":"USD"},{"search_price_period":"day"}):
            bad = PublicListingPageResponse(items=[row(**changes)],total=1,page=1,page_size=12)
            with self.assertRaisesRegex(PublicListingBoundaryError, "search_price_context_mismatch"):
                project_search(request, bad, projected_at=NOW)

    def test_rent_period_is_never_guessed_from_generic_price(self):
        listing = row(transaction="rent", price=Decimal("9999"), price_per_day=None, price_per_month=None)
        result = project_detail(PublicListingDetailInput(locale="en",listing_id=ID),listing,projected_at=NOW)
        self.assertIsNone(result.item.sale_price)
        self.assertIsNone(result.item.daily_rent)
        self.assertIsNone(result.item.monthly_rent)
        listing = row(construction_status="Under Construction")
        result = project_detail(PublicListingDetailInput(locale="en",listing_id=ID),listing,projected_at=NOW)
        self.assertIsNone(result.item.sale_price)
        self.assertIsNone(result.item.monthly_rent)

    def test_location_policy_and_untrusted_labels_remain_honest_and_bounded(self):
        request = PublicListingDetailInput(locale="en",listing_id=ID)
        for precision,label in (("exact","Sosúa"),("area","Sosúa"),("hidden",None)):
            result = project_detail(request,row(location_precision=precision),projected_at=NOW)
            self.assertEqual(result.item.location,label)
            self.assertEqual(result.item.location_precision,precision)
        result = project_detail(request,row(title="x"*1000,location="y"*100),projected_at=NOW)
        self.assertEqual(len(result.item.title),200)
        self.assertEqual(len(result.item.location),80)
        self.assertTrue(result.item.title_truncated)
        self.assertTrue(result.item.location_truncated)
        # Public strings are data, not guaranteed contact-free or instructions.
        result = project_detail(request,row(title="Published name @ publisher"),projected_at=NOW)
        self.assertEqual(result.item.title,"Published name @ publisher")
        for value in ("bad\nlabel", "bad\x00label"):
            with self.assertRaisesRegex(PublicListingBoundaryError,"public_label_invalid"):
                project_detail(request,row(title=value),projected_at=NOW)

    def test_wrong_target_withdrawn_page_mismatch_duplicates_and_naive_time_refuse(self):
        request = PublicListingDetailInput(locale="en",listing_id=ID)
        for listing in (row(id=UUID(int=2)), row(status="archived")):
            with self.assertRaises(PublicListingBoundaryError):
                project_detail(request,listing,projected_at=NOW)
        with self.assertRaises(PublicListingBoundaryError):
            project_detail(request,row(),projected_at=NOW.replace(tzinfo=None))
        for page in (PublicListingPageResponse(items=[row()],total=1,page=2,page_size=12),
                     PublicListingPageResponse(items=[row(),row()],total=2,page=1,page_size=12)):
            with self.assertRaises(PublicListingBoundaryError):
                project_search(search(),page,projected_at=NOW)

    def test_empty_search_and_output_schema_caps_without_success_fabrication(self):
        request = search(usd_to_dop=60)
        result = project_search(request,PublicListingPageResponse(items=[],total=0,page=1,page_size=12),projected_at=NOW)
        self.assertEqual(result.items,[])
        self.assertEqual(result.total,0)
        self.assertEqual(result.fx_kind,"request_estimate")
        self.assertLessEqual(len(result.model_dump_json().encode()), MAX_RESULT_BYTES)
        with self.assertRaises(ValidationError):
            type(result).model_validate({**result.model_dump(),"recipient_email":"private@example.invalid"})


if __name__ == "__main__":
    unittest.main()
