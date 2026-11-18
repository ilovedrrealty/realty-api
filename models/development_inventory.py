"""Private, append-only development inventory revisions and publication state."""

from sqlalchemy import CheckConstraint, Column, ForeignKey, ForeignKeyConstraint, Index, Integer, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database import Base


class DevelopmentInventory(Base):
    __tablename__ = "development_inventories"

    listing_id = Column(UUID(as_uuid=True), ForeignKey("listings.id", ondelete="RESTRICT"), primary_key=True)
    version = Column(Integer, nullable=False)
    draft_revision = Column(Integer, nullable=False)
    reviewed_revision = Column(Integer)
    published_revision = Column(Integer)
    published_at = Column(TIMESTAMP(timezone=True))

    __table_args__ = (
        CheckConstraint("draft_revision >= 1 AND version >= draft_revision "
                        "AND (reviewed_revision IS NULL OR reviewed_revision = draft_revision) "
                        "AND (published_revision IS NULL OR published_revision BETWEEN 1 AND draft_revision) "
                        "AND (published_at IS NULL) = (published_revision IS NULL)", name="ck_development_state"),
        ForeignKeyConstraint(("listing_id", "draft_revision"),
                             ("development_inventory_revisions.listing_id", "development_inventory_revisions.revision"),
                             name="fk_development_draft", use_alter=True, deferrable=True, initially="DEFERRED"),
        ForeignKeyConstraint(("listing_id", "reviewed_revision"),
                             ("development_inventory_revisions.listing_id", "development_inventory_revisions.revision"),
                             name="fk_development_reviewed", use_alter=True, deferrable=True, initially="DEFERRED"),
        ForeignKeyConstraint(("listing_id", "published_revision"),
                             ("development_inventory_revisions.listing_id", "development_inventory_revisions.revision"),
                             name="fk_development_published", use_alter=True, deferrable=True, initially="DEFERRED"),
    )


class DevelopmentInventoryRevision(Base):
    __tablename__ = "development_inventory_revisions"

    listing_id = Column(UUID(as_uuid=True), ForeignKey("development_inventories.listing_id"), primary_key=True)
    revision = Column(Integer, primary_key=True)
    snapshot = Column(JSONB, nullable=False)
    saved_at = Column(TIMESTAMP(timezone=True), nullable=False)
    saved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    __table_args__ = (
        CheckConstraint("revision >= 1 AND jsonb_typeof(snapshot) = 'object' "
                        "AND snapshot ?& ARRAY['project', 'evidence'] "
                        "AND jsonb_typeof(snapshot->'project') = 'object' "
                        "AND jsonb_typeof(snapshot->'evidence') = 'object'", name="ck_development_snapshot"),
        Index("ix_development_saved_by", "saved_by"),
    )


class DevelopmentInventoryEvent(Base):
    __tablename__ = "development_inventory_events"

    listing_id = Column(UUID(as_uuid=True), primary_key=True)
    version = Column(Integer, primary_key=True)
    revision = Column(Integer, nullable=False)
    action = Column(Text, nullable=False)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("version >= revision AND revision >= 1 "
                        "AND action IN ('draft','review','publish','withdraw')", name="ck_development_event"),
        ForeignKeyConstraint(("listing_id", "revision"),
                             ("development_inventory_revisions.listing_id", "development_inventory_revisions.revision"),
                             name="fk_development_event_revision"),
        Index("ix_development_actor", "actor_id"),
    )
