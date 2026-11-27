"""Pure request preparation and structured public-fact projection.

Nothing here performs a search/read, opens a URL, queries SQL, activates a model,
or proves freshness. A future orchestrator must make the actual anonymous,
bounded no-store read and validate its response before using these projectors.
Localized description/room/media retrieval is deliberately still unfinished.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import re
import unicodedata
from typing import Literal

from pydantic import ValidationError

from schemas.listing import ListingFilterOptionsResponse, PublicListingPageResponse, PublicListingResponse
from schemas.public_assistant_listing import (
    AssistantListingDetail, AssistantListingFact, AssistantListingPage, ListingCitation,
    ListingPriceFact, PublicListingDetailInput, PublicListingSearchInput,
)

CANONICAL_ORIGIN = "https://ilovedrrealty.com"
MAX_TOOL_INPUT_BYTES = 16 * 1024
MAX_RESULT_BYTES = 64 * 1024
_TYPES = {"villa", "apartment", "condo", "land", "commercial"}


class PublicListingBoundaryError(ValueError):
    """Finite local error code; never stringify source records/validation errors."""


@dataclass(frozen=True)
class PreparedPublicRead:
    path: str
    query: tuple[tuple[str, str], ...] = ()
    method: Literal["GET"] = field(default="GET", init=False)


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _parse(raw, model):
    # Model tool-call JSON is data, never Python/SQL or an arbitrary HTTP request.
    if not isinstance(raw, (str, bytes)):
        raise PublicListingBoundaryError("invalid_tool_input")
    try:
        encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
        if len(encoded) > MAX_TOOL_INPUT_BYTES:
            raise ValueError("input_limit")
        value = json.loads(encoded.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        if type(value) is not dict:
            raise ValueError("input_object_required")
        return model.model_validate(value)
    except (ValueError, TypeError, RecursionError, UnicodeError, ValidationError):
        raise PublicListingBoundaryError("invalid_tool_input") from None


def parse_search_input(raw: str | bytes) -> PublicListingSearchInput:
    return _parse(raw, PublicListingSearchInput)


def parse_detail_input(raw: str | bytes) -> PublicListingDetailInput:
    return _parse(raw, PublicListingDetailInput)


def prepare_search(request: PublicListingSearchInput, options: ListingFilterOptionsResponse) -> PreparedPublicRead:
    """Prepare only existing /listings query keys, not a new filter engine.

    options must come from the existing public filter-options read, not model
    arguments. Exact current location choices prevent arbitrary SQL/URL inputs
    becoming executable requests. Values remain ordinary encoded query data.
    """
    if not isinstance(request, PublicListingSearchInput) or not isinstance(options, ListingFilterOptionsResponse):
        raise PublicListingBoundaryError("search_context_required")
    if set(request.locations) - {item.value for item in options.locations}:
        raise PublicListingBoundaryError("known_public_locations_required")
    query = [("page", str(request.page)), ("page_size", str(request.page_size)),
             ("price_currency", request.price_currency), ("rental_period", request.rental_period),
             ("usd_to_dop", str(request.usd_to_dop)), ("sort", request.sort), ("include_aggregates", "false")]
    for name in ("purpose", "min_price", "max_price", "beds"):
        value = getattr(request, name)
        if value is not None:
            query.append((name, str(value)))
    query.extend(("locations", value) for value in request.locations)
    query.extend(("types", value) for value in request.types)
    return PreparedPublicRead("/listings", tuple(query))


def prepare_detail(request: PublicListingDetailInput) -> PreparedPublicRead:
    if not isinstance(request, PublicListingDetailInput):
        raise PublicListingBoundaryError("detail_context_required")
    return PreparedPublicRead("/listings/" + request.listing_id)


def _label(value, limit):
    # Preserve actual publisher text, with an explicit truncation flag. This is
    # field minimization, not a claim that arbitrary public prose is contact-free.
    if not isinstance(value, str):
        raise PublicListingBoundaryError("public_label_invalid")
    bounded = value[:limit]
    if any(unicodedata.category(c) in {"Cs", "Cc"} for c in bounded):
        raise PublicListingBoundaryError("public_label_invalid")
    return bounded, len(value) > limit


def _price(value, currency, period):
    if (currency not in {"USD", "DOP"} or not isinstance(value, Decimal)
            or not value.is_finite() or value <= 0 or value > 1_000_000_000_000):
        return None
    return ListingPriceFact(amount=value, currency=currency, period=period)


def _stamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _fact(row, locale, search=None):
    if not isinstance(row, PublicListingResponse) or row.status != "active":
        raise PublicListingBoundaryError("active_public_response_required")
    title, title_cut = _label(row.title, 200)
    location, location_cut = (None, False) if row.location_precision == "hidden" else _label(row.location, 80)
    construction = re.sub(r"[\s-]+", "_", (row.construction_status or "").strip().lower())
    published_price = construction not in {"pre_construction", "under_construction"}
    sale = _price(row.price, row.currency, "sale") if published_price and row.transaction in {"sale", "both"} else None
    daily = _price(row.price_per_day, row.currency, "day") if published_price and row.transaction in {"rent", "both"} else None
    monthly = _price(row.price_per_month, row.currency, "month") if published_price and row.transaction in {"rent", "both"} else None
    comparable = None
    if search is not None:
        period = search.rental_period if search.purpose == "rent" or row.transaction == "rent" else None
        if row.search_price_currency != search.price_currency or row.search_price_period != period:
            raise PublicListingBoundaryError("search_price_context_mismatch")
        # The existing route computes comparable currency/period. Never repeat
        # its conversion, infer a rental period, or use generic price as rent.
        if published_price:
            comparable = _price(row.search_price, row.search_price_currency, period or "sale")
    bedrooms = row.bedrooms if type(row.bedrooms) is int and 0 <= row.bedrooms <= 100 else None
    bathrooms = row.bathrooms if type(row.bathrooms) in (int, float) and math.isfinite(row.bathrooms) and 0 <= row.bathrooms <= 100 else None
    return AssistantListingFact(
        listing_id=str(row.id), locale=locale, title=title, title_truncated=title_cut,
        property_type=row.type if row.type in _TYPES else "unknown",
        transaction=row.transaction if row.transaction in {"sale", "rent", "both"} else "unknown",
        location=location, location_truncated=location_cut, location_precision=row.location_precision,
        location_verified=row.location_verified, bedrooms=bedrooms, bathrooms=bathrooms,
        sale_price=sale, daily_rent=daily, monthly_rent=monthly, comparable_price=comparable,
        listing_updated_at=_stamp(row.updated_at),
        citation=ListingCitation(url=CANONICAL_ORIGIN + "/listing/" + str(row.id) + "/",
                                 label="Public listing" if locale == "en" else "Ficha pública"),
    )


def _bounded(result):
    if len(result.model_dump_json().encode("utf-8")) > MAX_RESULT_BYTES:
        raise PublicListingBoundaryError("facts_output_limit")
    return result


def project_search(request: PublicListingSearchInput, page: PublicListingPageResponse, *, projected_at: datetime) -> AssistantListingPage:
    if (not isinstance(request, PublicListingSearchInput) or not isinstance(page, PublicListingPageResponse)
            or page.page != request.page or page.page_size != request.page_size
            or len(page.items) > request.page_size or len({row.id for row in page.items}) != len(page.items)
            or _stamp(projected_at) is None):
        raise PublicListingBoundaryError("search_response_context_mismatch")
    return _bounded(AssistantListingPage(
        locale=request.locale, items=[_fact(row, request.locale, request) for row in page.items],
        total=page.total, page=page.page, page_size=page.page_size, search_currency=request.price_currency,
        rental_period=request.rental_period, estimated_usd_to_dop=request.usd_to_dop,
        fx_kind="request_estimate" if "usd_to_dop" in request.model_fields_set else "route_default_estimate",
        projected_at=_stamp(projected_at),
    ))


def project_detail(request: PublicListingDetailInput, row: PublicListingResponse, *, projected_at: datetime) -> AssistantListingDetail:
    if (not isinstance(request, PublicListingDetailInput) or not isinstance(row, PublicListingResponse)
            or str(row.id) != request.listing_id or _stamp(projected_at) is None):
        raise PublicListingBoundaryError("detail_response_context_mismatch")
    return _bounded(AssistantListingDetail(locale=request.locale, item=_fact(row, request.locale),
                                            projected_at=_stamp(projected_at)))
