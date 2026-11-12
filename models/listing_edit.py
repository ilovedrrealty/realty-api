import uuid
from sqlalchemy import Column, Text, Enum as SAEnum, TIMESTAMP, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from database import Base

edit_status = SAEnum("pending", "approved", "rejected", name="listing_edit_status", create_type=True)


class ListingEdit(Base):
    __tablename__ = "listing_edits"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id   = Column(UUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, index=True)
    submitted_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    proposed_data = Column(JSONB, nullable=False)
    status       = Column(edit_status, nullable=False, server_default="pending")
    submitted_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    reviewed_by  = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at  = Column(TIMESTAMP(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    room_photo_tour_base_gallery_sha256 = Column(Text, nullable=True)
