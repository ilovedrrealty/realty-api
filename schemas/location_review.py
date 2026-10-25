"""Bounded Admin-only review input; no URL is fetched or geocoded."""
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

ReviewStatus = Literal["unreviewed", "needs_review", "verified"]
PublicPrecision = Literal["exact", "area", "hidden"]
SourceType = Literal["owner_confirmation", "partner_record", "site_visit", "property_document", "map_destination"]
Latitude = Annotated[Decimal, Field(ge=-90, le=90, allow_inf_nan=False, max_digits=10, decimal_places=7)]
Longitude = Annotated[Decimal, Field(ge=-180, le=180, allow_inf_nan=False, max_digits=10, decimal_places=7)]


class SaveLocationReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    expected_listing_updated_at: datetime
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    maps_url: str | None = Field(default=None, max_length=2048)
    review_status: ReviewStatus
    public_precision: PublicPrecision
    source_type: SourceType | None = None
    source_reference: str = Field(default="", max_length=500)
    review_note: str = Field(default="", max_length=1000)
    confirmed_property: bool = False

    @model_validator(mode="after")
    def validate_evidence(self):
        from utils.location_quality import validate_review_input
        self.maps_url = (self.maps_url or "").strip() or None
        self.source_reference = self.source_reference.strip()
        self.review_note = self.review_note.strip()
        validate_review_input(self)
        return self


class LocationReviewRow(BaseModel):
    listing_id: UUID
    title: str
    location: str
    source_ref: str | None
    listing_status: str
    listing_updated_at: datetime
    version: int
    review_status: ReviewStatus
    public_precision: PublicPrecision
    has_coordinates: bool
    public_mapped: bool
    projection_mismatch: bool
    reviewed_at: datetime | None


class LocationReviewDetail(LocationReviewRow):
    latitude: Decimal | None
    longitude: Decimal | None
    maps_url: str | None
    source_type: str | None
    source_reference: str
    review_note: str
    reviewed_by: UUID | None
    history: list[dict]


class LocationReviewPage(BaseModel):
    items: list[LocationReviewRow]
    total: int
    limit: int
    offset: int


class LocationReviewSummary(BaseModel):
    total: int
    coordinate_available: int
    missing_coordinates: int
    verified: int
    unreviewed: int
    needs_review: int
    public_mapped: int
    private_positions: int
    with_source_reference: int
