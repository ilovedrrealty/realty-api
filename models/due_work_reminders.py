"""Personal opt-in and occurrence state for live project work reminders."""
from sqlalchemy import Boolean, CheckConstraint, Column, Date, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from database import Base


class NetworkDueWorkPreference(Base):
    __tablename__ = "network_due_work_preferences"

    id = Column(UUID(as_uuid=True), primary_key=True)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("network_projects.id", ondelete="RESTRICT"), nullable=False)
    grant_version = Column(Integer, nullable=False)
    milestones_enabled = Column(Boolean, nullable=False)
    visits_enabled = Column(Boolean, nullable=False)
    lead_days = Column(Integer, nullable=False)
    version = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("actor_id", "project_id", "grant_version", name="uq_due_work_preference_grant"),
        CheckConstraint("grant_version >= 0 AND lead_days BETWEEN 0 AND 30 AND version >= 1", name="ck_due_work_preference_bounds"),
    )


class NetworkDueWorkDisposition(Base):
    __tablename__ = "network_due_work_dispositions"

    preference_id = Column(UUID(as_uuid=True), ForeignKey("network_due_work_preferences.id", ondelete="RESTRICT"), primary_key=True)
    kind = Column(Text, primary_key=True)
    item_id = Column(UUID(as_uuid=True), primary_key=True)
    occurrence_version = Column(Integer, primary_key=True)
    state = Column(Text, nullable=False)
    snoozed_until = Column(Date)
    version = Column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("kind IN ('milestone','visit') AND occurrence_version >= 1 AND version >= 1", name="ck_due_work_item_bounds"),
        CheckConstraint("state IN ('active','snoozed','dismissed','stopped') AND (state='snoozed') = (snoozed_until IS NOT NULL)",
                        name="ck_due_work_item_state"),
    )


class NetworkDueWorkAction(Base):
    __tablename__ = "network_due_work_actions"

    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    submission_id = Column(UUID(as_uuid=True), primary_key=True)
    preference_id = Column(UUID(as_uuid=True), ForeignKey("network_due_work_preferences.id", ondelete="RESTRICT"), nullable=False)
    kind = Column(Text)
    item_id = Column(UUID(as_uuid=True))
    occurrence_version = Column(Integer)
    operation = Column(Text, nullable=False)
    creation_sha256 = Column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("(operation='preferences' AND kind IS NULL AND item_id IS NULL AND occurrence_version IS NULL) OR "
                        "(operation IN ('snooze','dismiss','restore','stop') AND kind IN ('milestone','visit') "
                        "AND kind IS NOT NULL AND item_id IS NOT NULL AND occurrence_version IS NOT NULL AND occurrence_version >= 1)",
                        name="ck_due_work_action_scope"),
        CheckConstraint("creation_sha256 ~ '^[0-9a-f]{64}$'", name="ck_due_work_action_hash"),
    )
