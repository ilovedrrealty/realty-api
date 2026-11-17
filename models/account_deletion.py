"""Persist account-deletion requests and the history of their review."""

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column

from database import Base


class AccountDeletionRequest(Base):
    __tablename__ = "account_deletion_requests"
    __table_args__ = (
        CheckConstraint("status IN ('pending_review','reviewed','retention_hold','cancelled')",
                        name="ck_account_deletion_status"),
        CheckConstraint("version >= 1", name="ck_account_deletion_version"),
        Index("uq_account_deletion_active_user", "user_id", unique=True,
              postgresql_where=text("status <> 'cancelled'")),
        Index("ix_account_deletion_status_requested", "status", "requested_at", "id"),
    )

    id = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    requested_at = mapped_column(DateTime(timezone=True), nullable=False)
    status = mapped_column(Text, nullable=False)
    version = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    reviewed_at = mapped_column(DateTime(timezone=True))
    reviewed_by_id = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    review_reason = mapped_column(Text)
    cancelled_at = mapped_column(DateTime(timezone=True))


class AccountDeletionEvent(Base):
    __tablename__ = "account_deletion_events"

    id = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id = mapped_column(UUID(as_uuid=True), ForeignKey("account_deletion_requests.id", ondelete="RESTRICT"),
                               nullable=False, index=True)
    actor_id = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    action = mapped_column(Text, nullable=False)
    version = mapped_column(Integer, nullable=False)
    reason = mapped_column(Text)
    occurred_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
