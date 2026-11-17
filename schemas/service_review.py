"""Bounded private feedback on an awarded service."""
from uuid import UUID

from pydantic import Field, field_validator

from schemas.property_network import Input
from schemas.service_conversation import ServiceConversationWrite


class ServiceReviewWrite(Input):
    submission_id: UUID
    stars: int = Field(ge=1, le=5, strict=True)
    comment: str | None = Field(default=None, max_length=2000, strict=True)

    @field_validator("comment", mode="before")
    @classmethod
    def plain_comment(cls, value):
        comment = ServiceConversationWrite.plain_text(value)
        return None if comment == "" else comment
