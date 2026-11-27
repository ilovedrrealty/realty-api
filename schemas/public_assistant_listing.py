"""First structured listing boundary, not a model, HTTP client or complete AI tool.

No structured contact/actor/payment fields belong here. Public title/location
strings remain untrusted publisher text; this is not a personal-data redactor.
"""
from decimal import Decimal
import re
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


Locale = Literal["en", "es"]
Currency = Literal["USD", "DOP"]
Period = Literal["day", "month"]
PropertyType = Literal["villa", "apartment", "condo", "land", "commercial"]
Location = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=80)]
BoundedPrice = Annotated[float, Field(ge=0, le=1_000_000_000_000, allow_inf_nan=False)]
ID_PATTERN = r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}"


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PublicListingSearchInput(StrictInput):
    locale: Locale
    price_currency: Currency
    rental_period: Period
    purpose: Literal["sale", "rent", "investment"] | None = None
    locations: list[Location] = Field(default_factory=list, max_length=60)
    types: list[PropertyType] = Field(default_factory=list, max_length=5)
    min_price: BoundedPrice | None = None
    max_price: BoundedPrice | None = None
    beds: int | None = Field(default=None, ge=0, le=50)
    sort: Literal["new", "low", "high"] = "new"
    page: int = Field(default=1, ge=1, le=1000)
    page_size: int = Field(default=12, ge=1, le=24)
    # Same estimate/default as the existing route, never a live exchange rate.
    usd_to_dop: float = Field(default=59.5, ge=1, le=1000, allow_inf_nan=False)

    @field_validator("locations", "types")
    @classmethod
    def unique_choices(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Use distinct search choices")
        return value

    @model_validator(mode="after")
    def ordered_prices(self):
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("Minimum price must not exceed maximum price")
        return self


class PublicListingDetailInput(StrictInput):
    locale: Locale
    listing_id: str

    @field_validator("listing_id")
    @classmethod
    def canonical_uuid_only(cls, value):
        if re.fullmatch(ID_PATTERN, value) is None:
            raise ValueError("Use a listing UUID, not a URL")
        return value.lower()


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ListingCitation(Fact):
    url: str = Field(max_length=100)
    label: Literal["Public listing", "Ficha pública"]


class ListingPriceFact(Fact):
    amount: Decimal = Field(gt=0, le=1_000_000_000_000, allow_inf_nan=False)
    currency: Currency
    period: Literal["sale", "day", "month"]


class AssistantListingFact(Fact):
    listing_id: str = Field(pattern="^" + ID_PATTERN + "$")
    locale: Locale
    title: str = Field(max_length=200)
    title_truncated: bool
    property_type: Literal["villa", "apartment", "condo", "land", "commercial", "unknown"]
    transaction: Literal["sale", "rent", "both", "unknown"]
    location: str | None = Field(default=None, max_length=80)
    location_truncated: bool
    location_precision: Literal["exact", "area", "hidden"]
    location_verified: bool
    bedrooms: int | None = Field(default=None, ge=0, le=100)
    bathrooms: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    sale_price: ListingPriceFact | None = None
    daily_rent: ListingPriceFact | None = None
    monthly_rent: ListingPriceFact | None = None
    comparable_price: ListingPriceFact | None = None
    listing_updated_at: AwareDatetime | None = None
    citation: ListingCitation
    record_status: Literal["active"] = "active"
    availability_verified: Literal[False] = False
    public_description_included: Literal[False] = False


class AssistantListingPage(Fact):
    locale: Locale
    items: list[AssistantListingFact] = Field(max_length=24)
    total: int = Field(ge=0, le=2_147_483_647)
    page: int = Field(ge=1, le=1000)
    page_size: int = Field(ge=1, le=24)
    search_currency: Currency
    rental_period: Period
    estimated_usd_to_dop: float = Field(ge=1, le=1000, allow_inf_nan=False)
    fx_kind: Literal["request_estimate", "route_default_estimate"]
    projected_at: AwareDatetime


class AssistantListingDetail(Fact):
    locale: Locale
    item: AssistantListingFact
    projected_at: AwareDatetime
