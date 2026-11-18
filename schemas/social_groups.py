"""Group-scoped contracts; membership never authorizes a property or project."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

Visibility = Literal["private", "network"]
GroupRole = Literal["admin", "moderator", "member"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def safe_text(cls, value):
        if isinstance(value, str) and any(ord(char) < 32 and char not in "\n\r\t" for char in value):
            raise ValueError("Control characters are not allowed")
        return value


class Submission(Input):
    submission_id: UUID


class GroupFields(Input):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    purpose: Literal["community", "professional", "local_area", "property_project"] = "community"
    area_label: str = Field(default="", max_length=160)
    visibility: Visibility = "private"
    join_policy: Literal["approval_required", "open"] = "approval_required"
    post_policy: Literal["members", "moderated"] = "members"
    comments_enabled: StrictBool = True


class GroupCreate(GroupFields, Submission):
    owner_alias: str = Field(min_length=1, max_length=100)


class GroupEdit(GroupFields, Submission):
    expected_version: int = Field(ge=1)
    image_media_id: UUID | None = None
    publication_acknowledged: StrictBool = False


class ArchiveGroup(Submission):
    expected_version: int = Field(ge=1)
    action: Literal["archive"]
    reason: str = Field(default="", max_length=500)


class MembershipAction(Submission):
    expected_membership_version: int = Field(ge=0)
    action: Literal["join", "request", "cancel", "accept_invite", "decline_invite", "leave"]
    member_alias: str = Field(default="", max_length=100)


class ManageMember(Submission):
    expected_membership_version: int = Field(ge=0)
    action: Literal["invite", "approve", "decline", "revoke_invite", "set_role", "remove", "block", "unblock"]
    role: GroupRole | None = None
    expires_in_days: int = Field(default=7, ge=1, le=30)
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def role_only_for_promotion(self):
        if (self.action == "set_role") != (self.role is not None):
            raise ValueError("Choose a role only when changing a role")
        return self


class CreateInviteLink(Submission):
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    expires_in_days: int = Field(default=7, ge=1, le=30)
    max_uses: int = Field(default=100, ge=1, le=1000)


class RevokeInviteLink(Submission):
    expected_version: int = Field(ge=1)


class ResolveInvite(Input):
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class AcceptInvite(ResolveInvite, Submission):
    member_alias: str = Field(min_length=1, max_length=100)


class GroupPostCreate(Submission):
    body: str = Field(default="", max_length=4000)
    media_ids: list[UUID] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def valid_content(self):
        if not self.body and not self.media_ids or len(set(self.media_ids)) != len(self.media_ids):
            raise ValueError("Write a post or choose unique photos")
        return self


class ModeratePost(Submission):
    expected_version: int = Field(ge=1)
    action: Literal["approve", "hide"]
    reason: str = Field(default="", max_length=500)


class GroupCommentCreate(Submission):
    body: str = Field(min_length=1, max_length=2000)
    media_id: UUID | None = None


class HideComment(Submission):
    expected_version: int = Field(ge=1)
    action: Literal["hide"]
    reason: str = Field(default="", max_length=500)
