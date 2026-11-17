"""Private named referral inputs and their explicit, contact-free projections."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool

from schemas.property_network import Input
from schemas.service_workflow import ReviewedMatch


Relationship = Literal["worked_together", "know_their_work", "no_prior_work"]
Status = Literal["proposed", "offered", "accepted", "declined", "revoked"]
Action = Literal["offer", "accept", "decline", "revoke"]


class ReferralQuery(ReviewedMatch):
    page: int = Field(default=1, ge=1, le=1000)


class ReferralCreate(ReviewedMatch):
    submission_id: UUID
    provider_user_code: int = Field(gt=0, le=2147483647, strict=True)
    relationship: Relationship
    share_confirmed: StrictBool


class ReferralAction(Input):
    submission_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    share_confirmed: StrictBool


class ReferralView(Input):
    id: UUID
    version: int
    status: Status
    category_id: str
    property_class: str
    area_id: str
    language: str | None
    relationship: Relationship
    created_at: datetime
    updated_at: datetime
    provider_alias: str | None
    can_offer: bool
    can_accept: bool
    can_decline: bool
    can_revoke: bool
    request_id: UUID | None


class ReferralPage(Input):
    items: list[ReferralView]
    total: int
    page: int
    page_size: int


class RequestReferrals(ReferralPage):
    can_suggest: bool
    can_offer: bool
