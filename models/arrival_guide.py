from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database import Base


class ListingArrivalGuide(Base):
    __tablename__ = "listing_arrival_guides"

    listing_id = Column(UUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    version = Column(BigInteger, nullable=False)
    content = Column(JSONB(none_as_null=True))

    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_listing_arrival_guide_version"),
        CheckConstraint("content IS NULL OR (owner_id IS NOT NULL AND jsonb_typeof(content) = 'object')",
                        name="ck_listing_arrival_guide_content"),
    )
