from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, Text, TIMESTAMP, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID

from database import Base


class ClientShortlist(Base):
    __tablename__ = "client_shortlists"

    id = Column(UUID(as_uuid=True), primary_key=True)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    buyer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    title = Column(Text, nullable=False)
    shared_notes = Column(Text, nullable=False)
    shared_preferences = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False)
    archived_at = Column(TIMESTAMP(timezone=True))

    __table_args__ = (
        CheckConstraint("version >= 1 AND char_length(btrim(title)) BETWEEN 1 AND 100 "
                        "AND char_length(shared_notes) <= 2000 AND char_length(shared_preferences) <= 2000",
                        name="ck_client_shortlist_content"),
        Index("ix_client_shortlist_owner", "owner_id", "updated_at", "id",
              postgresql_where=text("archived_at IS NULL")),
        Index("ix_client_shortlist_buyer", "buyer_id"),
    )


class ClientShortlistItem(Base):
    __tablename__ = "client_shortlist_items"

    shortlist_id = Column(UUID(as_uuid=True), ForeignKey("client_shortlists.id", ondelete="CASCADE"), primary_key=True)
    listing_id = Column(UUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True)
    position = Column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("position BETWEEN 0 AND 19", name="ck_client_shortlist_item_position"),
        UniqueConstraint("shortlist_id", "position", name="uq_client_shortlist_item_position"),
        Index("ix_client_shortlist_item_listing", "listing_id"),
    )


class ClientShortlistGrant(Base):
    __tablename__ = "client_shortlist_grants"

    id = Column(UUID(as_uuid=True), primary_key=True)
    shortlist_id = Column(UUID(as_uuid=True), ForeignKey("client_shortlists.id", ondelete="CASCADE"), nullable=False)
    buyer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    realtor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    recipient_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    recipient_user_code = Column(Integer, nullable=False)
    generation = Column(Integer, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False)
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    revoked_at = Column(TIMESTAMP(timezone=True))
    invalidated_at = Column(TIMESTAMP(timezone=True))

    __table_args__ = (
        CheckConstraint("buyer_id <> realtor_id AND recipient_id IN (buyer_id, realtor_id) "
                        "AND recipient_user_code > 0 AND generation >= 1", name="ck_client_shortlist_grant_scope"),
        CheckConstraint("expires_at-created_at IN (interval '168 hours',interval '720 hours',interval '2160 hours')",
                        name="ck_client_shortlist_grant_interval"),
        UniqueConstraint("shortlist_id", "generation", name="uq_client_shortlist_grant_generation"),
        Index("ix_client_shortlist_grant_recipient", "recipient_id", "expires_at"),
        Index("ix_client_shortlist_grant_realtor", "realtor_id"),
        Index("ix_client_shortlist_grant_buyer", "buyer_id", "realtor_id"),
    )
