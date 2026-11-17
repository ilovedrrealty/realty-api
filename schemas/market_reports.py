from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class MarketReportGroup(BaseModel):
    property_type: Literal["villa", "apartment", "condo", "land", "commercial"]
    currency: Literal["USD", "DOP"]
    listing_count: int
    price_count: int
    price_min: Decimal | None
    price_median: Decimal | None
    price_max: Decimal | None
    size_count: int
    median_price_per_sqm: Decimal | None
    area_basis: Literal["living", "lot"] | None


class MarketReport(BaseModel):
    as_of: datetime
    local_date: date
    location: str | None
    total_listings: int
    unclassified_currency_count: int
    groups: list[MarketReportGroup]
