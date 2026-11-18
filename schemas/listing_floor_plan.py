"""Public floorplan metadata and explicit publication requests."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator

from schemas.editorial import Input
from utils.transaction_document_storage import MAX_DOCUMENT_BYTES


class FloorPlanAsset(Input):
    id: UUID
    content_type: Literal["application/pdf", "image/jpeg", "image/png"]
    byte_count: int = Field(ge=1, le=MAX_DOCUMENT_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime


class FloorPlanState(Input):
    draft: FloorPlanAsset | None
    published: FloorPlanAsset | None


class PublishFloorPlan(Input):
    draft_id: UUID
    expected_published_id: UUID | None
    owner_permission_confirmed: bool = Field(strict=True)

    @field_validator("owner_permission_confirmed")
    @classmethod
    def confirm_permission(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Confirm the property owner's approval")
        return value


class WithdrawFloorPlan(Input):
    published_id: UUID
