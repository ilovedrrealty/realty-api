"""Bounded plain text and an actor-scoped idempotency key."""
from uuid import UUID

from pydantic import Field, field_validator

from schemas.property_network import Input


class ServiceConversationWrite(Input):
    submission_id: UUID
    body: str = Field(min_length=1, max_length=2000, strict=True)

    @field_validator("body", mode="before")
    @classmethod
    def plain_text(cls, value):
        if not isinstance(value, str):
            return value
        if any((ord(character) < 32 and character not in "\t\n\r") or 127 <= ord(character) <= 159 for character in value):
            raise ValueError("Control characters are not allowed")
        return value.strip()
