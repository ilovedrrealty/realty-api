"""Explicit private registry custody offers and actor receipts."""
from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, Text, TIMESTAMP, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database import Base


class NetworkPropertyCustodyTransfer(Base):
    __tablename__ = "network_property_custody_transfers"

    id = Column(UUID(as_uuid=True), primary_key=True)
    property_id = Column(UUID(as_uuid=True), ForeignKey("network_properties.id", ondelete="RESTRICT"), nullable=False)
    offered_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    recipient_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    property_version = Column(Integer, nullable=False)
    custody_version = Column(Integer, nullable=False)
    label = Column(Text, nullable=False)
    fields = Column(JSONB, nullable=False)
    prior_notes = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False)
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    finished_at = Column(TIMESTAMP(timezone=True))

    __table_args__ = (
        CheckConstraint("offered_by_id <> recipient_id AND property_version >= 1 AND custody_version >= 1",
                        name="ck_property_custody_actor_scope"),
        CheckConstraint("char_length(label) BETWEEN 1 AND 120 AND jsonb_typeof(fields)='object' AND char_length(prior_notes) <= 4000",
                        name="ck_property_custody_content"),
        CheckConstraint("expires_at-created_at = interval '168 hours' AND (finished_at IS NULL OR finished_at >= created_at)",
                        name="ck_property_custody_times"),
        CheckConstraint("(status='pending' AND version=1 AND finished_at IS NULL) OR "
                        "(status IN ('accepted','declined','cancelled','expired') AND version=2 AND finished_at IS NOT NULL)",
                        name="ck_property_custody_state"),
        Index("uq_property_custody_pending_source", "property_id", unique=True, postgresql_where=text("status='pending'")),
        Index("uq_property_custody_accepted_generation", "property_id", "custody_version", unique=True,
              postgresql_where=text("status='accepted'")),
        Index("ix_property_custody_offerer", "offered_by_id", "created_at", "id"),
        Index("ix_property_custody_recipient", "recipient_id", "created_at", "id"),
    )


class NetworkPropertyCustodyAction(Base):
    __tablename__ = "network_property_custody_actions"

    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    submission_id = Column(UUID(as_uuid=True), primary_key=True)
    transfer_id = Column(UUID(as_uuid=True), ForeignKey("network_property_custody_transfers.id", ondelete="RESTRICT"), nullable=False)
    operation = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    creation_sha256 = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("creation_sha256 ~ '^[0-9a-f]{64}$'", name="ck_property_custody_action_digest"),
        CheckConstraint("(operation='offer' AND status='pending' AND version=1) OR "
                        "(version=2 AND (operation,status) IN (('accept','accepted'),('decline','declined'),('cancel','cancelled')))",
                        name="ck_property_custody_action_state"),
    )
