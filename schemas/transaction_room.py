"""Bounded private transaction-room views and versioned mutations."""

from datetime import date
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BeforeValidator, Field, StringConstraints, model_validator

from schemas.editorial import Input


def _date(value):
    if type(value) is date:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return date.fromisoformat(value)
    raise ValueError("Use a calendar date in YYYY-MM-DD format")


Day = Annotated[date, BeforeValidator(_date)]
Title = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=160)]
RoomTitle = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=120)]
Version = Annotated[int, Field(strict=True, ge=1, le=2147483647)]
MemberCode = Annotated[int, Field(strict=True, ge=1, le=2147483647)]
Role = Literal["admin", "realtor", "buyer", "professional"]
MemberRole = Literal["realtor", "buyer", "professional"]
RoomStatus = Literal["open", "closed", "retired"]
MemberState = Literal["pending", "active", "revoked"]
TaskStatus = Literal["open", "in_progress", "done", "cancelled"]


class CreateRoom(Input):
    title: RoomTitle


class VersionChange(Input):
    expected_version: Version


class InviteProfessional(VersionChange):
    member_code: MemberCode


class AcceptInvitation(Input):
    pass


class TaskEvidence(Input):
    document_id: UUID
    version_id: UUID


class TaskEvidenceView(TaskEvidence):
    title: Title
    number: int = Field(ge=1)
    reviewed_at: AwareDatetime | None
    reviewer_name: str | None


class CreateTask(VersionChange):
    title: Title
    assignee_user_id: UUID | None = None
    due_on: Day | None = None
    evidence: TaskEvidence | None = None


class ChangeTask(VersionChange):
    title: Title | None = None
    assignee_user_id: UUID | None = None
    due_on: Day | None = None
    status: TaskStatus | None = None
    evidence: TaskEvidence | None = None

    @model_validator(mode="after")
    def require_an_edit(self):
        fields = self.model_fields_set - {"expected_version"}
        if (not fields or "title" in fields and self.title is None
                or "status" in fields and self.status is None):
            raise ValueError("Provide a valid task update")
        return self


class ChangeMilestone(VersionChange):
    title: Title
    target_on: Day | None = None
    actual_on: Day | None = None


class RoomSummary(Input):
    id: UUID
    lead_id: UUID
    listing_id: UUID
    title: RoomTitle
    status: RoomStatus
    version: Version
    role: Role


class Rooms(Input):
    items: list[RoomSummary]


class LeadRoom(Input):
    room: RoomSummary | None


class Member(Input):
    user_id: UUID | None
    grant_id: UUID | None
    member_code: MemberCode | None
    display_name: str
    role: MemberRole
    state: MemberState


class TaskView(Input):
    id: UUID
    title: Title
    assignee_user_id: UUID | None
    due_on: Day | None
    status: TaskStatus
    version: Version
    requires_evidence_review: bool = False
    evidence: TaskEvidenceView | None = None


class MilestoneView(Input):
    id: UUID
    title: Title
    target_on: Day | None
    actual_on: Day | None
    version: Version


class RoomDetail(RoomSummary):
    members: list[Member]
    tasks: list[TaskView]
    milestones: list[MilestoneView]


class InvitationView(Input):
    id: UUID
    invited_at: AwareDatetime


class PendingInvitations(Input):
    items: list[InvitationView]


class HistoryEvent(Input):
    sequence: Version
    action: str
    actor_role: Literal["admin", "realtor", "buyer", "professional", "system"]
    created_at: AwareDatetime
    target_id: UUID | None


class History(Input):
    items: list[HistoryEvent]
