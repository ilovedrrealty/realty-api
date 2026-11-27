"""Metadata only. Message/contact/provider details never enter these DTOs."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class AssignmentNotice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["lead_assigned_agent", "owner_upgrade_customer", "owner_assigned_agent"]
    status: Literal["pending", "processing", "accepted", "failed", "needs_review", "skipped"]
    attempts: int
    created_at: datetime
    attempted_at: datetime | None
    accepted_at: datetime | None
    outcome_uncertain: bool


class AssignmentHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    action: Literal["assigned", "cleared"]
    created_at: datetime
    notices: list[AssignmentNotice]


class AssignmentHistoryPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int
    limit: int
    offset: int
    tracked: bool
    items: list[AssignmentHistoryItem]
