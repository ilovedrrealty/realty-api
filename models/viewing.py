from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, Text, TIMESTAMP, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID

from database import Base


class ViewingItinerary(Base):
    __tablename__ = "viewing_itineraries"

    id = Column(UUID(as_uuid=True), primary_key=True)
    buyer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    request_id = Column(UUID(as_uuid=True), nullable=False)
    request_sha256 = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("buyer_id", "request_id", name="uq_viewing_itinerary_request"),
        CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 100 AND request_sha256 ~ '^[0-9a-f]{64}$'",
                        name="ck_viewing_itinerary_content"),
        Index("ix_viewing_itinerary_buyer", "buyer_id", "created_at", "id"),
    )


class PropertyViewing(Base):
    __tablename__ = "property_viewings"

    id = Column(UUID(as_uuid=True), primary_key=True)
    itinerary_id = Column(UUID(as_uuid=True), ForeignKey("viewing_itineraries.id", ondelete="CASCADE"), nullable=False)
    listing_id = Column(UUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), nullable=False)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    listing_title = Column(Text, nullable=False)
    listing_location = Column(Text, nullable=False)
    agent_name = Column(Text, nullable=False)
    attendee_name = Column(Text, nullable=False)
    note = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    mode = Column(Text, nullable=False)
    starts_at = Column(TIMESTAMP(timezone=True), nullable=False)
    version = Column(Integer, nullable=False)
    meeting_details = Column(Text)
    meeting_url = Column(Text)
    availability_checked_at = Column(TIMESTAMP(timezone=True))

    __table_args__ = (
        UniqueConstraint("itinerary_id", "listing_id", name="uq_property_viewing_itinerary_listing"),
        CheckConstraint("version >= 1 AND status IN ('requested','proposed','confirmed','declined','cancelled','unavailable') "
                        "AND mode IN ('in_person','virtual') AND char_length(note) <= 1000",
                        name="ck_property_viewing_status"),
        CheckConstraint(
            "(status IN ('proposed','confirmed') AND availability_checked_at IS NOT NULL "
            "AND (meeting_details IS NULL OR char_length(meeting_details) BETWEEN 1 AND 1000) "
            "AND ((mode = 'virtual' AND meeting_url IS NOT NULL AND char_length(meeting_url) BETWEEN 1 AND 600) "
            "OR (mode = 'in_person' AND meeting_url IS NULL AND meeting_details IS NOT NULL))) OR "
            "(status NOT IN ('proposed','confirmed') AND availability_checked_at IS NULL "
            "AND meeting_details IS NULL AND meeting_url IS NULL)", name="ck_property_viewing_meeting",
        ),
        Index("ix_property_viewing_agent", "agent_id", "starts_at", "id"),
        Index("ix_property_viewing_listing", "listing_id"),
        Index("ix_property_viewing_confirmed_start", "starts_at", postgresql_where=text("status='confirmed'")),
    )
