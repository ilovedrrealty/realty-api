from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictBool, StringConstraints, model_validator


Coverage = Literal["complete", "partial", "unavailable"]
EntryKind = Literal["completed_showing", "owner_feedback", "marketing_update"]
MilestoneKind = Literal["submitted", "approved", "archived", "rejected", "edit_approved"]


class PerformanceRange(BaseModel):
    start: date
    end: date
    time_zone: Literal["America/Santo_Domingo"]


class DailyViews(BaseModel):
    date: date
    count: int | None
    coverage: Coverage


class PerformanceViews(BaseModel):
    lifetime: int
    range_total: int | None
    tracked_from: datetime | None
    coverage: Coverage
    days: list[DailyViews]


class PerformanceTotals(BaseModel):
    property_inquiries: int
    confirmed_viewings: int
    recorded_completed_showings: int


class PerformanceMilestone(BaseModel):
    kind: MilestoneKind
    occurred_on: date


class PerformanceEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: EntryKind
    occurred_on: date
    text: str | None


class NewPerformanceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    kind: EntryKind
    occurred_on: date
    text: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=1000)] | None = None
    share_with_owner: StrictBool | None = None

    @model_validator(mode="after")
    def validate_sharing(self):
        if self.kind == "completed_showing":
            if self.text is not None or self.share_with_owner is not None:
                raise ValueError("Record a completed showing using its date only")
        elif self.text is None or self.share_with_owner is not True or "\x00" in self.text:
            raise ValueError("Enter a summary to explicitly share with the owner")
        return self


class ListingPerformance(BaseModel):
    listing_id: UUID
    title: str
    status: Literal["draft", "pending_approval", "active", "rejected", "archived"]
    can_record: bool
    range: PerformanceRange
    views: PerformanceViews
    totals: PerformanceTotals
    milestones: list[PerformanceMilestone]
    entries: list[PerformanceEntry]
    milestones_truncated: bool
    entries_truncated: bool
