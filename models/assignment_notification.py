"""Private new-assignment events and one-attempt queued notice outcomes."""
from uuid import uuid4
from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Index, Integer, Text, TIMESTAMP, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from database import Base

NOTICE_KINDS = ("lead_assigned_agent", "owner_upgrade_customer", "owner_assigned_agent")
NOTICE_STATUSES = ("pending", "processing", "accepted", "failed", "needs_review", "skipped")


class AssignmentEvent(Base):
    __tablename__ = "assignment_events"
    __table_args__ = (
        UniqueConstraint("actor_id", "request_id", name="uq_assignment_actor_request"),
        CheckConstraint("(source_kind='lead' AND lead_id IS NOT NULL AND user_id IS NULL) OR "
                        "(source_kind='account' AND user_id IS NOT NULL AND lead_id IS NULL)", name="ck_assignment_source"),
        CheckConstraint("(action='assigned' AND agent_id IS NOT NULL) OR "
                        "(action='cleared' AND agent_id IS NULL AND source_kind='account')", name="ck_assignment_action"),
        CheckConstraint("owner_upgraded_id IS NULL OR source_kind='lead'", name="ck_assignment_owner_upgrade"),
        CheckConstraint("cycle >= 1", name="ck_assignment_cycle"),
        Index("ix_assignment_lead_history", "lead_id", "created_at", "id"),
        Index("ix_assignment_user_history", "user_id", "created_at", "id"),
        Index("ix_assignment_lead_cycle", "lead_id", "cycle"),
        Index("ix_assignment_user_cycle", "user_id", "cycle"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_kind = Column(Text, nullable=False)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="RESTRICT"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    request_id = Column(UUID(as_uuid=True), nullable=False)
    request_sha256 = Column(Text, nullable=False)
    previous_agent_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    agent_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    owner_upgraded_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    cycle = Column(Integer, nullable=False)
    action = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


class AssignmentEmailJob(Base):
    __tablename__ = "assignment_email_jobs"
    __table_args__ = (
        UniqueConstraint("event_id", "kind", name="uq_assignment_notice"),
        CheckConstraint("kind IN ('lead_assigned_agent','owner_upgrade_customer','owner_assigned_agent')", name="ck_assignment_notice_kind"),
        CheckConstraint("status IN ('pending','processing','accepted','failed','needs_review','skipped')", name="ck_assignment_notice_status"),
        CheckConstraint("attempt_count IN (0,1)", name="ck_assignment_notice_attempts"),
        CheckConstraint("(attempt_count=0 AND attempted_at IS NULL) OR (attempt_count=1 AND attempted_at IS NOT NULL)", name="ck_assignment_notice_attempt_time"),
        CheckConstraint("(status='accepted' AND accepted_at IS NOT NULL AND attempt_count=1 AND NOT outcome_uncertain) OR "
                        "(status<>'accepted' AND accepted_at IS NULL)", name="ck_assignment_notice_acceptance"),
        CheckConstraint("(status='needs_review' AND outcome_uncertain AND attempt_count=1) OR "
                        "(status<>'needs_review' AND NOT outcome_uncertain)", name="ck_assignment_notice_uncertain"),
        CheckConstraint("status<>'pending' OR attempt_count=0", name="ck_assignment_notice_pending"),
        CheckConstraint("status<>'processing' OR (attempt_count=1 AND lease_token IS NOT NULL AND lease_until IS NOT NULL)", name="ck_assignment_notice_processing"),
        Index("ix_assignment_notice_pending", "status", "created_at", "id"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("assignment_events.id", ondelete="RESTRICT"), nullable=False)
    kind = Column(Text, nullable=False)
    recipient_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    payload = Column(JSONB, nullable=False)
    payload_sha256 = Column(Text, nullable=False)
    status = Column(Text, nullable=False, server_default="pending")
    attempt_count = Column(Integer, nullable=False, server_default="0")
    attempted_at = Column(TIMESTAMP(timezone=True))
    accepted_at = Column(TIMESTAMP(timezone=True))
    outcome_uncertain = Column(Boolean, nullable=False, server_default="false")
    lease_token = Column(UUID(as_uuid=True))
    lease_until = Column(TIMESTAMP(timezone=True))
    provider_id = Column(Text)
    error_code = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
