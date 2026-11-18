from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator


Title = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)]
ShareText = Annotated[str, StringConstraints(strict=True, max_length=2000)]
Version = Annotated[int, Field(strict=True, ge=1, le=2147483647)]
UserCode = Annotated[int, Field(strict=True, gt=0, le=2147483647)]
ListingIds = Annotated[list[UUID], Field(max_length=20)]


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShortlistFields(EmptyBody):
    title: Title
    shared_notes: ShareText
    shared_preferences: ShareText

    @field_validator("title", "shared_notes", "shared_preferences")
    @classmethod
    def plain_text(cls, value):
        if any((ord(char) < 32 and char not in "\r\n\t") or 0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("Use valid plain text")
        return value


class ShortlistWrite(ShortlistFields):
    listing_ids: ListingIds

    @field_validator("listing_ids")
    @classmethod
    def distinct_listings(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Choose each property once")
        return value


class ShortlistUpdate(ShortlistWrite):
    expected_version: Version
    listing_ids: ListingIds = Field(default_factory=list)


class ShortlistArchive(EmptyBody):
    expected_version: Version


class ShortlistShare(EmptyBody):
    recipient_user_code: UserCode
    expires_in_days: int = Field(strict=True)

    @field_validator("expires_in_days")
    @classmethod
    def allowed_duration(cls, value):
        if value not in (7, 30, 90):
            raise ValueError("Choose a 7-, 30-, or 90-day link")
        return value


class ShortlistSummary(BaseModel):
    id: UUID
    title: str
    expires_at: AwareDatetime | None


class ShortlistList(BaseModel):
    items: list[ShortlistSummary]


class ShortlistItemView(BaseModel):
    id: UUID
    title: str
    location: str
    transaction: Literal["sale", "rent", "both"]


class ShareView(BaseModel):
    id: UUID
    recipient_user_code: UserCode
    expires_at: AwareDatetime
    state: Literal["active", "expired", "revoked", "unavailable"]


class ShortlistView(ShortlistFields):
    id: UUID
    version: Version
    items: list[ShortlistItemView]


class OwnerShortlistView(ShortlistView):
    share: ShareView | None


class ReceivedShortlistView(ShortlistView):
    expires_at: AwareDatetime
