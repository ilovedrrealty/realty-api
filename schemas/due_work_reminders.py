"""Separate project reminder preferences and occurrence-scoped actions."""
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, field_validator, model_validator

from schemas.maintenance_reminders import local_date
from schemas.property_network import Input


DueWorkKind = Literal["milestone", "visit"]


class DueWorkPreferencesWrite(Input):
    submission_id: UUID
    expected_version: int = Field(ge=0, strict=True)
    milestones_enabled: StrictBool
    visits_enabled: StrictBool
    lead_days: int = Field(ge=0, le=30, strict=True)


class DueWorkActionWrite(Input):
    submission_id: UUID
    expected_version: int = Field(ge=0, strict=True)
    occurrence_version: int = Field(ge=1, le=2147483647, strict=True)
    operation: Literal["snooze", "dismiss", "restore", "stop"]
    snoozed_until: date | None = None

    @field_validator("snoozed_until", mode="before")
    @classmethod
    def snooze_date(cls, value):
        return local_date(value)

    @model_validator(mode="after")
    def snooze_field(self):
        if (self.operation == "snooze") != (self.snoozed_until is not None):
            raise ValueError("Provide a date only when snoozing")
        return self
