"""Input for an awarded service visit and its completion review."""
import re
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictBool, field_validator, model_validator

from schemas.property_network import Input


class ServiceWorkWrite(Input):
    submission_id: UUID
    expected_version: int = Field(ge=0, strict=True)
    operation: Literal["propose_visit", "accept_visit", "decline_visit", "cancel_visit",
                       "request_completion", "request_changes", "approve_completion"]
    starts_at: AwareDatetime | None = None
    duration_minutes: int | None = Field(default=None, ge=15, le=480, strict=True)
    note: str | None = Field(default=None, min_length=1, max_length=2000)
    completed_on: date | None = None
    share_in_history: StrictBool | None = None

    @field_validator("starts_at", mode="before")
    @classmethod
    def explicit_start_time(cls, value):
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("Use a visit time with an explicit timezone")
        if isinstance(value, str) and not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})", value):
            raise ValueError("Use a visit time with an explicit timezone")
        return value

    @field_validator("completed_on", mode="before")
    @classmethod
    def explicit_completion_date(cls, value):
        if value is not None and (not isinstance(value, (str, date)) or isinstance(value, datetime)):
            raise ValueError("Use a calendar date")
        if isinstance(value, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Use a calendar date")
        return value

    @model_validator(mode="after")
    def fields_for_operation(self):
        required = {
            "propose_visit": {"starts_at", "duration_minutes"},
            "request_completion": {"note", "completed_on"},
            "request_changes": {"note"},
            "approve_completion": {"share_in_history"},
        }.get(self.operation, set())
        supplied = {name for name in ("starts_at", "duration_minutes", "note", "completed_on", "share_in_history")
                    if getattr(self, name) is not None}
        if supplied != required or self.operation == "approve_completion" and self.share_in_history is not True:
            raise ValueError("Provide the fields required for this service action")
        return self
