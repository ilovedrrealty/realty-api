"""Read-only owner inquiry DTOs, without CRM or attribution internals."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field

InquiryStatus = Literal["new", "assigned", "schedule", "contacted", "closed", "unknown"]


class OwnerLead(BaseModel):
    id: UUID
    type: Literal["property_inquiry"] = "property_inquiry"
    property_id: UUID
    property_title: str
    name: str
    email: str | None
    phone: str | None
    message: str | None
    status: InquiryStatus
    created_at: datetime
    assigned_at: datetime | None
    contacted_at: datetime | None
    closed_at: datetime | None
    assigned_realtor_id: UUID | None
    assigned_realtor_name: str | None


class OwnerLeadSummary(BaseModel):
    all_total: int = Field(ge=0)
    open_total: int = Field(ge=0)
    closed_total: int = Field(ge=0)
    unknown_total: int = Field(ge=0)


class OwnerLeadPage(BaseModel):
    items: list[OwnerLead]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: Literal[50] = 50
    state: Literal["all", "open", "closed"]
    summary: OwnerLeadSummary
