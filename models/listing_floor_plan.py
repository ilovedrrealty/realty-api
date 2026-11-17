"""Store exact draft and approved floorplans with the application's recoverable data."""

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, LargeBinary, Text, TIMESTAMP, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import deferred

from database import Base


class ListingFloorPlan(Base):
    __tablename__ = "listing_floor_plans"

    id = Column(UUID(as_uuid=True), primary_key=True)
    listing_id = Column(UUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), nullable=False)
    stage = Column(Text, nullable=False)
    content_type = Column(Text, nullable=False)
    byte_count = Column(Integer, nullable=False)
    sha256 = Column(Text, nullable=False)
    payload = deferred(Column(LargeBinary, nullable=False))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False)
    staged_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    approved_at = Column(TIMESTAMP(timezone=True))
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    __table_args__ = (
        UniqueConstraint("listing_id", "stage", name="uq_listing_floor_plan_slot"),
        CheckConstraint(
            "content_type IN ('application/pdf','image/jpeg','image/png') "
            "AND byte_count BETWEEN 1 AND 6291456 AND octet_length(payload) = byte_count "
            "AND sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_listing_floor_plan_file",
        ),
        CheckConstraint(
            "(stage = 'draft' AND approved_at IS NULL AND approved_by_id IS NULL) "
            "OR (stage = 'published' AND approved_at IS NOT NULL)",
            name="ck_listing_floor_plan_stage",
        ),
    )
