"""Explicit sharing inputs; the only shared text is entered by the sender."""
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from schemas.maintenance_reminders import SourceKind
from schemas.property_network import Input


class HistoryShareItem(Input):
    update_id: UUID
    summary: str = Field(min_length=1, max_length=600)


class HistoryShareCreate(Input):
    submission_id: UUID
    source_kind: SourceKind
    source_id: UUID
    anchor_project_id: UUID
    recipient_user_code: int = Field(gt=0, le=2147483647, strict=True)
    label: str = Field(min_length=1, max_length=120)
    expires_in_days: int = Field(strict=True)
    items: list[HistoryShareItem] = Field(min_length=1, max_length=25)

    @field_validator("expires_in_days")
    @classmethod
    def allowed_duration(cls, value):
        if value not in (1, 7, 14, 30):
            raise ValueError("Choose an expiry of 1, 7, 14 or 30 days")
        return value

    @model_validator(mode="after")
    def distinct_updates(self):
        if len({item.update_id for item in self.items}) != len(self.items):
            raise ValueError("Select each history entry once")
        return self


class HistoryShareAction(Input):
    submission_id: UUID
    expected_version: int = Field(ge=1, le=3, strict=True)
    operation: Literal["accept", "decline", "revoke"]
