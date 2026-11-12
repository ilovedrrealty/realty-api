"""Private lifecycle of a single awarded service agreement."""
from sqlalchemy import CheckConstraint, Column, Date, ForeignKey, Index, Integer, Text, TIMESTAMP, func, text
from sqlalchemy.dialects.postgresql import UUID

from database import Base


class NetworkServiceWork(Base):
    __tablename__ = "network_service_work"

    request_id = Column(UUID(as_uuid=True), ForeignKey("network_service_requests.id", ondelete="RESTRICT"), primary_key=True)
    version = Column(Integer, nullable=False)
    status = Column(Text, nullable=False)
    confirmed_starts_at = Column(TIMESTAMP(timezone=True))
    confirmed_duration_minutes = Column(Integer)
    proposed_starts_at = Column(TIMESTAMP(timezone=True))
    proposed_duration_minutes = Column(Integer)
    proposed_by = Column(Text)
    completion_note = Column(Text)
    completed_on = Column(Date)
    completion_review_note = Column(Text)
    completion_update_id = Column(UUID(as_uuid=True), ForeignKey("network_project_updates.id", ondelete="RESTRICT"), unique=True)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("version>=1 AND status IN ('unscheduled','proposed','confirmed','cancelled','completion_requested','completed')",
                        name="ck_service_work_state"),
        CheckConstraint("(confirmed_starts_at IS NULL)=(confirmed_duration_minutes IS NULL) "
                        "AND (confirmed_duration_minutes IS NULL OR confirmed_duration_minutes BETWEEN 15 AND 480) "
                        "AND (status NOT IN ('confirmed','completion_requested','completed') OR confirmed_starts_at IS NOT NULL) "
                        "AND (status NOT IN ('unscheduled','cancelled') OR confirmed_starts_at IS NULL)",
                        name="ck_service_work_confirmed"),
        CheckConstraint("(status='proposed')=(proposed_starts_at IS NOT NULL) "
                        "AND (proposed_starts_at IS NULL)=(proposed_duration_minutes IS NULL) "
                        "AND (proposed_starts_at IS NULL)=(proposed_by IS NULL) "
                        "AND (proposed_duration_minutes IS NULL OR proposed_duration_minutes BETWEEN 15 AND 480) "
                        "AND (proposed_by IS NULL OR proposed_by IN ('owner','provider'))",
                        name="ck_service_work_proposal"),
        CheckConstraint("(completion_note IS NULL)=(completed_on IS NULL) "
                        "AND (completion_note IS NULL OR char_length(completion_note) BETWEEN 1 AND 2000) "
                        "AND (completion_review_note IS NULL OR char_length(completion_review_note) BETWEEN 1 AND 2000) "
                        "AND (status NOT IN ('completion_requested','completed') OR completion_note IS NOT NULL) "
                        "AND (status='completed')=(completion_update_id IS NOT NULL)",
                        name="ck_service_work_completion"),
        Index("ix_service_work_confirmed_start", "confirmed_starts_at", postgresql_where=text("confirmed_starts_at IS NOT NULL")),
    )


class NetworkServiceWorkAction(Base):
    __tablename__ = "network_service_work_actions"

    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    submission_id = Column(UUID(as_uuid=True), primary_key=True)
    request_id = Column(UUID(as_uuid=True), ForeignKey("network_service_work.request_id", ondelete="RESTRICT"), nullable=False)
    operation = Column(Text, nullable=False)
    creation_sha256 = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("creation_sha256 ~ '^[0-9a-f]{64}$'", name="ck_service_work_action_hash"),
        CheckConstraint("operation IN ('propose_visit','accept_visit','decline_visit','cancel_visit',"
                        "'request_completion','request_changes','approve_completion')", name="ck_service_work_action_operation"),
        Index("ix_service_work_action_request", "request_id", "created_at"),
    )
