"""Private location evidence. Listings retain only the approved public projection."""
import uuid
from sqlalchemy import Column, Text, Numeric, Integer, TIMESTAMP, ForeignKey, CheckConstraint, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from database import Base


class ListingLocationReview(Base):
    __tablename__ = "listing_location_reviews"
    listing_id = Column(UUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True)
    version = Column(Integer, nullable=False, server_default="1")
    review_status = Column(Text, nullable=False, server_default="unreviewed")
    public_precision = Column(Text, nullable=False, server_default="exact")
    latitude = Column(Numeric(10, 7))
    longitude = Column(Numeric(10, 7))
    maps_url = Column(Text)
    source_type = Column(Text)
    source_reference = Column(Text)
    review_note = Column(Text)
    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at = Column(TIMESTAMP(timezone=True))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_location_review_version"),
        CheckConstraint("review_status IN ('unreviewed','needs_review','verified')", name="ck_location_review_status"),
        CheckConstraint("public_precision IN ('exact','area','hidden')", name="ck_location_public_precision"),
        CheckConstraint("(latitude IS NULL AND longitude IS NULL) OR (latitude IS NOT NULL AND longitude IS NOT NULL AND latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)", name="ck_location_review_coordinates"),
    )


class ListingLocationReviewRevision(Base):
    __tablename__ = "listing_location_review_revisions"
    listing_id = Column(UUID(as_uuid=True), ForeignKey("listing_location_reviews.listing_id", ondelete="CASCADE"), primary_key=True)
    version = Column(Integer, primary_key=True)
    action = Column(Text, nullable=False)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    snapshot = Column(JSONB, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (CheckConstraint("version >= 1", name="ck_location_revision_version"),)
