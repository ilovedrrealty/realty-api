"""Strict owner-only reminder commands using Dominican calendar dates."""
import re
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, field_validator, model_validator

from schemas.property_network import Input


SourceKind = Literal["private", "listing"]
Repeat = Literal["none", "weekly", "monthly", "quarterly", "yearly"]
Operation = Literal["complete", "snooze", "pause", "resume", "stop"]
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def local_date(value):
    if not isinstance(value, str) or DATE.fullmatch(value) is None:
        raise ValueError("Use a local date in YYYY-MM-DD format")
    return date.fromisoformat(value)


class MaintenancePreferencesWrite(Input):
    submission_id: UUID
    expected_version: int = Field(ge=0, strict=True)
    enabled: StrictBool
    lead_days: int = Field(ge=0, le=30, strict=True)


class MaintenanceScheduleCreate(Input):
    submission_id: UUID
    title: str = Field(min_length=1, max_length=180, strict=True)
    repeat: Repeat
    first_due_on: date

    @field_validator("first_due_on", mode="before")
    @classmethod
    def due_date(cls, value):
        return local_date(value)


class MaintenanceActionWrite(Input):
    submission_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    operation: Operation
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
