from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


PropertyType = Literal["villa", "apartment", "condo", "land", "commercial"]
PropertyCondition = Literal["new", "good", "fair", "needs_repairs", "under_construction"]
AreaLabel = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=2, max_length=80)]
Currency = Literal["USD", "DOP"]
Money = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
ReviewNote = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2000)]


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
    def single_line_location(cls, value: str | None) -> str | None:
        if value is not None and any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Enter a city, town, or neighborhood on one line")
        return value


class ValuationDraftSubject(SellerValuationSubject):
    property_type: PropertyType | None = None
    location: AreaLabel | None = None


class VersionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(strict=True, ge=0, le=2147483646)


class DraftChange(VersionChange):
    subject: ValuationDraftSubject


class AskingChange(VersionChange):
    listing_id: UUID


class SoldChange(VersionChange):
    property_type: PropertyType
    location: AreaLabel
    area_m2: Decimal | None = Field(default=None, gt=0, le=Decimal("99999999.99"), decimal_places=2)
    bedrooms: int | None = Field(default=None, strict=True, ge=0, le=1000)
    bathrooms: Decimal | None = Field(default=None, ge=0, le=1000, multiple_of=Decimal("0.5"))
    price: Money
    currency: Currency
    closed_on: date
    source_label: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=120)]
    source_reference: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=500)]

    @field_validator("location")
    @classmethod
    def single_line_location(cls, value: str) -> str:
        return SellerValuationSubject.single_line_location(value)


class SoldReviewChange(VersionChange):
    decision: Literal["verified", "rejected"]
    review_note: ReviewNote


class ReportFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["range", "insufficient_evidence"]
    notes: ReviewNote
    price_low: Money | None = None
    price_high: Money | None = None
    currency: Currency | None = None

    @model_validator(mode="after")
    def check_range(self):
        if self.outcome == "range":
            if self.price_low is None or self.price_high is None or self.currency is None or self.price_low > self.price_high:
                raise ValueError("Enter a valid reviewed price range")
        elif any(value is not None for value in (self.price_low, self.price_high, self.currency)):
            raise ValueError("An insufficient evidence report cannot state a price")
        return self


class ReportChange(VersionChange, ReportFields):
    pass


class FollowUpChange(VersionChange):
    follow_up_on: date
    note: ReviewNote


class AskingCandidate(BaseModel):
    listing_id: UUID
    title: str
    property_type: PropertyType
    location: str
    price: Decimal
    currency: Currency
    area_m2: Decimal | None
    bedrooms: int | None
    bathrooms: Decimal | None


class AskingCandidates(BaseModel):
    items: list[AskingCandidate]


class SoldReview(BaseModel):
    decision: Literal["verified", "rejected"]
    review_note: str
    reviewed_at: datetime
    reviewed_by: UUID


class Comparable(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: Literal["asking", "sold"]
    property_type: PropertyType
    location: str
    area_m2: Decimal | None
    bedrooms: int | None
    bathrooms: Decimal | None
    price: Decimal
    currency: Currency
    captured_at: datetime
    listing_id: UUID | None
    title: str | None
    closed_on: date | None
    source_label: str | None
    source_reference: str | None
    sold_review: SoldReview | None


class Report(ReportFields):
    status: Literal["current", "expired"]
    reviewed_at: datetime
    reviewed_by: UUID


class FollowUp(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    follow_up_on: date
    note: str
    created_at: datetime
    completed_at: datetime | None


class ValuationWorkspace(BaseModel):
    lead_id: UUID
    version: int
    started: bool
    intake_subject: SellerValuationSubject | None
    subject: ValuationDraftSubject
    comparables: list[Comparable]
    review: Report | None
    follow_ups: list[FollowUp]
