"""Bounded collaboration inputs; no client-selected authors or recipient accounts."""
from datetime import date
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from utils.professional_options import CATEGORY_IDS, PROPERTY_CLASS_IDS


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def clean_text(cls, value):
        if isinstance(value, str) and any(ord(c) < 32 and c not in "\n\r\t" for c in value):
            raise ValueError("Control characters are not allowed")
        return value


class Version(Input):
    expected_version: int = Field(ge=1)


class ProjectCreate(Input):
    listing_id: UUID
    title: str = Field(min_length=1, max_length=180)
    summary: str = Field(default="", max_length=4000)
    property_class: str
    service_category_ids: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("property_class")
    @classmethod
    def known_class(cls, value):
        if value not in PROPERTY_CLASS_IDS:
            raise ValueError("Choose a known property class")
        return value

    @field_validator("service_category_ids")
    @classmethod
    def known_services(cls, value):
        if len(set(value)) != len(value) or set(value) - CATEGORY_IDS:
            raise ValueError("Choose unique known service categories")
        return value


class ProjectEdit(Version):
    title: str = Field(min_length=1, max_length=180)
    summary: str = Field(default="", max_length=4000)
    status: Literal["open", "in_progress", "completed", "archived"]


class MemberWrite(Version):
    user_code: int = Field(gt=0)
    role: Literal["manager", "professional", "viewer"]


class MilestoneCreate(Input):
    title: str = Field(min_length=1, max_length=180)
    due_date: date | None = None


class MilestoneEdit(MilestoneCreate, Version):
    status: Literal["open", "in_progress", "completed", "cancelled"]


class ProgressWrite(Input):
    submission_id: UUID
    body: str = Field(min_length=1, max_length=4000)
    occurred_on: date
    correction_of: UUID | None = None
    media_ids: list[UUID] = Field(default_factory=list, max_length=6)

    @field_validator("media_ids")
    @classmethod
    def unique_media(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Choose unique uploaded photos")
        return values


class CommentWrite(Input):
    submission_id: UUID
    body: str = Field(min_length=1, max_length=2000)
    media_id: UUID | None = None


class ProfileWrite(Input):
    expected_version: int = Field(ge=0)
    display_name: str = Field(min_length=1, max_length=100)
    bio: str = Field(default="", max_length=2000)
    status_text: str = Field(default="", max_length=280)
    visibility: Literal["private", "members"] = "private"
    wall_policy: Literal["own_only", "approval_required"] = "own_only"
    comments_enabled: StrictBool = True


class PostWrite(Input):
    submission_id: UUID
    body: str = Field(min_length=1, max_length=4000)
    media_ids: list[UUID] = Field(default_factory=list, max_length=6)

    @field_validator("media_ids")
    @classmethod
    def unique_media(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Choose unique uploaded photos")
        return values


class ModeratePost(Input):
    action: Literal["approve", "hide"]


class BlockWrite(Input):
    blocked: StrictBool
