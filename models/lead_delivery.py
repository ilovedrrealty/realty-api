"""Private operational records for newly accepted lead notifications.

Payloads are immutable snapshots and may contain contact data. They must never
be returned by public or Admin status endpoints, or included in log messages.
"""
import uuid
from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Index, Integer, Text, TIMESTAMP, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from database import Base


DELIVERY_STATUSES = ("pending", "processing", "retry_wait", "accepted", "failed", "needs_review", "skipped")


class LeadDeliveryJob(Base):
    __tablename__ = "lead_delivery_jobs"
    __table_args__ = (
        UniqueConstraint("lead_id", "step", name="uq_lead_delivery_step"),
        CheckConstraint("status IN ('pending','processing','retry_wait','accepted','failed','needs_review','skipped')", name="ck_lead_delivery_status"),
        CheckConstraint("attempt_count >= 0 AND attempt_count <= 4", name="ck_lead_delivery_attempts"),
        Index("ix_lead_delivery_due", "status", "next_attempt_at"),
        Index("ix_lead_delivery_lead", "lead_id", "created_at"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    step = Column(Text, nullable=False)
    provider = Column(Text, nullable=False)
    depends_on = Column(Text)
    payload = Column(JSONB, nullable=False)
    payload_sha256 = Column(Text, nullable=False)
    status = Column(Text, nullable=False, server_default="pending")
    attempt_count = Column(Integer, nullable=False, server_default="0")
    safe_to_retry = Column(Boolean, nullable=False, server_default="false")
    outcome_uncertain = Column(Boolean, nullable=False, server_default="false")
    next_attempt_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    first_attempt_at = Column(TIMESTAMP(timezone=True))
    last_attempt_at = Column(TIMESTAMP(timezone=True))
    lease_token = Column(UUID(as_uuid=True))
    lease_until = Column(TIMESTAMP(timezone=True))
    accepted_at = Column(TIMESTAMP(timezone=True))
    provider_id = Column(Text)
    error_code = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


class LeadDeliveryWorkerState(Base):
    __tablename__ = "lead_delivery_worker_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_lead_delivery_worker_singleton"),)
    id = Column(Integer, primary_key=True)
    last_poll_at = Column(TIMESTAMP(timezone=True), nullable=False)
    last_completed_at = Column(TIMESTAMP(timezone=True))
    last_batch_count = Column(Integer, nullable=False, server_default="0")
    error_code = Column(Text)
