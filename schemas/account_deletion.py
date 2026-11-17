"""Inputs and persisted state for account-deletion review."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator


RequestStatus = Literal["none", "pending_review", "reviewed", "retention_hold", "cancelled"]


class DeletionState(BaseModel):
    request_id: UUID | None
    status: RequestStatus
    requested_at: datetime | None
    review_after: datetime | None = None
    version: int
    reviewed_at: datetime | None
    review_reason: str | None


class RequestDeletion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    confirm_email: EmailStr
    confirm_request: Literal[True]

    @field_validator("confirm_request", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


class CancelDeletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    expected_version: int = Field(ge=1)


class ReviewDeletion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    status: Literal["reviewed", "retention_hold"]
    reason: Annotated[str, StringConstraints(min_length=1, max_length=1000)]


class AdminDeletionState(DeletionState):
    user_id: UUID
    display_name: str
    email: str


class AdminDeletionList(BaseModel):
    items: list[AdminDeletionState]
    total: int
