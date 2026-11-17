from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


PropertyType = Literal["villa", "apartment", "condo", "land", "commercial"]
PropertyCondition = Literal["new", "good", "fair", "needs_repairs", "under_construction"]
AreaLabel = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=2, max_length=80)]


class SellerValuationSubject(BaseModel):
    """Private property details supplied with a seller's valuation request."""

    model_config = ConfigDict(extra="forbid")

    property_type: PropertyType
    location: AreaLabel
    area_m2: Decimal | None = Field(default=None, gt=0, le=Decimal("99999999.99"), decimal_places=2)
    bedrooms: int | None = Field(default=None, strict=True, ge=0, le=1000)
    bathrooms: Decimal | None = Field(default=None, ge=0, le=1000, multiple_of=Decimal("0.5"))
    condition: PropertyCondition | None = None

    @field_validator("location")
    @classmethod
    def single_line_location(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Enter a city, town, or neighborhood on one line")
        return value
