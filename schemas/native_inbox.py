"""Actor-idempotent native inbox inputs; no provider or recipient contact fields."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, field_validator, model_validator

from schemas.property_network import Input


class Command(Input):
    submission_id: UUID


class VisibilityWrite(Command):
    expected_version: int = Field(ge=0, strict=True)
    visibility: Literal["private", "members"]


class BlockWrite(Command):
    blocked: StrictBool


class ConversationCreate(Command):
    recipient_id: UUID


class ReadWrite(Command):
    last_read_sequence: int = Field(ge=0, le=9007199254740991, strict=True)


class EntryWrite(Command):
    kind: Literal["message", "manual_whatsapp", "manual_call"]
    body: str = Field(min_length=1, max_length=2000, strict=True)
    occurred_at: datetime | None = None
    reported_direction: Literal["incoming", "outgoing"] | None = None
    call_outcome: Literal["connected", "voicemail", "missed", "no_answer", "declined", "unspecified"] | None = None
    review_id: UUID | None = None

    @field_validator("body", mode="before")
    @classmethod
    def plain_text(cls, value):
        if isinstance(value, str):
            if any((ord(c)<32 and c not in "\t\r\n") or 127<=ord(c)<=159 or 0xD800<=ord(c)<=0xDFFF for c in value):
                raise ValueError("Control characters are not allowed")
            return value.strip()
        return value

    @field_validator("occurred_at")
    @classmethod
    def aware_time(cls, value):
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Use a timezone-aware timestamp")
            value = value.astimezone(timezone.utc)
            if value.year < 1970:
                raise ValueError("Use a supported timestamp")
        return value

    @field_validator("occurred_at", mode="before")
    @classmethod
    def timestamp_input(cls, value):
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("Use an ISO timestamp")
        return value

    @model_validator(mode="after")
    def manual_context(self):
        if self.kind == "message":
            if any(value is not None for value in (self.occurred_at, self.reported_direction, self.call_outcome)):
                raise ValueError("In-app messages do not contain manual channel claims")
        elif self.occurred_at is None or self.reported_direction is None:
            raise ValueError("Manual records require time and reported direction")
        if (self.kind == "manual_call") != (self.call_outcome is not None):
            raise ValueError("Choose a call outcome only for manual calls")
        return self


class Source(Input):
    kind: Literal["native", "service_award", "service_negotiation"]
    id: UUID
    recipient_id: UUID | None = None

    @model_validator(mode="after")
    def recipient_context(self):
        if (self.kind == "service_negotiation") != (self.recipient_id is not None):
            raise ValueError("Choose a recipient only for a negotiation history")
        return self


class ReviewCreate(Command):
    source: Source
    reason: Literal["oversight", "support"]
