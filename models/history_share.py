"""Private selected property history and actor-owned command receipts."""
from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, Text, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database import Base


class NetworkHistoryShare(Base):
    __tablename__ = "network_history_shares"

    id = Column(UUID(as_uuid=True), primary_key=True)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    recipient_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    property_id = Column(UUID(as_uuid=True), ForeignKey("network_properties.id", ondelete="RESTRICT"))
    listing_id = Column(UUID(as_uuid=True), ForeignKey("listings.id", ondelete="RESTRICT"))
    anchor_project_id = Column(UUID(as_uuid=True), ForeignKey("network_projects.id", ondelete="RESTRICT"), nullable=False)
    member_id = Column(UUID(as_uuid=True), ForeignKey("network_project_members.id", ondelete="RESTRICT"), nullable=False)
    member_version = Column(Integer, nullable=False)
    label = Column(Text, nullable=False)
    items = Column(JSONB, nullable=False)
    status = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False)
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    invalidated_at = Column(TIMESTAMP(timezone=True))

    __table_args__ = (
        CheckConstraint("(property_id IS NULL) <> (listing_id IS NULL) AND owner_id <> recipient_id",
                        name="ck_history_share_scope"),
        CheckConstraint("char_length(label) BETWEEN 1 AND 120 AND jsonb_typeof(items)='array' AND "
                        "jsonb_array_length(items) BETWEEN 1 AND 25 AND member_version >= 1",
                        name="ck_history_share_content"),
        CheckConstraint("expires_at-created_at IN (interval '24 hours',interval '168 hours',interval '336 hours',interval '720 hours')",
                        name="ck_history_share_expiry"),
        CheckConstraint("(status='pending' AND version=1) OR (status IN ('accepted','declined') AND version=2) "
                        "OR (status='revoked' AND version IN (2,3))", name="ck_history_share_state"),
        Index("ix_history_share_owner", "owner_id", "created_at", "id"),
        Index("ix_history_share_recipient", "recipient_id", "created_at", "id"),
        Index("ix_history_share_registry", "property_id"),
        Index("ix_history_share_listing", "listing_id"),
        Index("ix_history_share_anchor", "anchor_project_id"),
    )


class NetworkHistoryShareAction(Base):
    __tablename__ = "network_history_share_actions"

    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    submission_id = Column(UUID(as_uuid=True), primary_key=True)
    share_id = Column(UUID(as_uuid=True), ForeignKey("network_history_shares.id", ondelete="RESTRICT"), nullable=False)
    operation = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    creation_sha256 = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("creation_sha256 ~ '^[0-9a-f]{64}$'", name="ck_history_share_action_digest"),
        CheckConstraint("(operation='create' AND status='pending' AND version=1) OR "
                        "(operation='accept' AND status='accepted' AND version=2) OR "
                        "(operation='decline' AND status='declined' AND version=2) OR "
                        "(operation='revoke' AND status='revoked' AND version IN (2,3))", name="ck_history_share_action_state"),
    )
