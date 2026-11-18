"""Private operational records; public listings never contain tenant/ledger data."""
import uuid
from sqlalchemy import Column, Text, Integer, Numeric, Date, TIMESTAMP, ForeignKey, Index, UniqueConstraint, CheckConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from database import Base


class PropertyManagementRecord(Base):
    __tablename__ = "property_management_records"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id = Column(UUID(as_uuid=True), ForeignKey("listings.id", ondelete="RESTRICT"), nullable=False)
    kind = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    data = Column(JSONB, nullable=False)
    due_date = Column(Date)
    currency = Column(Text)
    amount = Column(Numeric(14, 2))
    version = Column(Integer, nullable=False, default=1, server_default="1")
    approval_status = Column(Text, nullable=False, default="not_required", server_default="not_required")
    approval_limit = Column(Numeric(14, 2))
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    approved_at = Column(TIMESTAMP(timezone=True))
    origin_record_id = Column(UUID(as_uuid=True), ForeignKey("property_management_records.id", ondelete="RESTRICT"))
    recurrence_anchor_day = Column(Integer)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_management_record_version"),
        CheckConstraint("kind IN ('lease','maintenance','inspection','ledger','task')", name="ck_management_record_kind"),
        Index("ix_management_listing_kind_due", "listing_id", "kind", "due_date"),
        Index("uq_management_recurring_origin", "origin_record_id", unique=True,
              postgresql_where=text("kind = 'task' AND origin_record_id IS NOT NULL")),
    )


class PropertyManagementRevision(Base):
    __tablename__ = "property_management_revisions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_id = Column(UUID(as_uuid=True), ForeignKey("property_management_records.id", ondelete="RESTRICT"), nullable=False)
    version = Column(Integer, nullable=False)
    action = Column(Text, nullable=False)
    snapshot = Column(JSONB, nullable=False)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("record_id", "version", name="uq_management_revision_version"),)
