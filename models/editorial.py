from sqlalchemy import CheckConstraint, Column, ForeignKey, ForeignKeyConstraint, Index, Integer, Text, TIMESTAMP, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database import Base


class EditorialPage(Base):
    __tablename__ = "editorial_pages"

    id = Column(UUID(as_uuid=True), primary_key=True)
    slug = Column(Text, nullable=False, unique=True)
    kind = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    draft_revision = Column(Integer, nullable=False)
    reviewed_revision = Column(Integer)
    published_revision = Column(Integer)
    published_at = Column(TIMESTAMP(timezone=True))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND char_length(slug) <= 80 "
                        "AND kind IN ('article','guide','neighborhood')", name="ck_editorial_page_identity"),
        CheckConstraint("draft_revision >= 1 AND version >= draft_revision "
                        "AND (reviewed_revision IS NULL OR reviewed_revision = draft_revision) "
                        "AND (published_revision IS NULL OR published_revision BETWEEN 1 AND draft_revision) "
                        "AND (published_at IS NULL) = (published_revision IS NULL)", name="ck_editorial_page_state"),
        ForeignKeyConstraint(("id", "draft_revision"), ("editorial_revisions.page_id", "editorial_revisions.revision"),
                             name="fk_editorial_draft", use_alter=True, deferrable=True, initially="DEFERRED"),
        ForeignKeyConstraint(("id", "reviewed_revision"), ("editorial_revisions.page_id", "editorial_revisions.revision"),
                             name="fk_editorial_reviewed", use_alter=True, deferrable=True, initially="DEFERRED"),
        ForeignKeyConstraint(("id", "published_revision"), ("editorial_revisions.page_id", "editorial_revisions.revision"),
                             name="fk_editorial_published", use_alter=True, deferrable=True, initially="DEFERRED"),
        Index("ix_editorial_page_updated", "updated_at", "id"),
        Index("ix_editorial_page_published", "published_at", "slug",
              postgresql_where=text("published_revision IS NOT NULL")),
    )


class EditorialRevision(Base):
    __tablename__ = "editorial_revisions"

    page_id = Column(UUID(as_uuid=True), ForeignKey("editorial_pages.id"), primary_key=True)
    revision = Column(Integer, primary_key=True)
    content = Column(JSONB, nullable=False)
    sources = Column(JSONB, nullable=False)
    review_note = Column(Text, nullable=False)
    saved_at = Column(TIMESTAMP(timezone=True), nullable=False)
    saved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    restored_from = Column(Integer)

    __table_args__ = (
        CheckConstraint("revision >= 1 AND jsonb_typeof(content) = 'object' AND "
                        "jsonb_typeof(sources) = 'array' AND jsonb_array_length(sources) <= 8 "
                        "AND char_length(review_note) <= 2000 "
                        "AND (restored_from IS NULL OR restored_from < revision)", name="ck_editorial_revision_content"),
        ForeignKeyConstraint(("page_id", "restored_from"), ("editorial_revisions.page_id", "editorial_revisions.revision"),
                             name="fk_editorial_restored_from"),
    )


class EditorialEvent(Base):
    __tablename__ = "editorial_events"

    page_id = Column(UUID(as_uuid=True), primary_key=True)
    version = Column(Integer, primary_key=True)
    revision = Column(Integer, nullable=False)
    action = Column(Text, nullable=False)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("version >= revision AND revision >= 1 "
                        "AND action IN ('create','draft','review','publish','unpublish','restore')",
                        name="ck_editorial_event_action"),
        ForeignKeyConstraint(("page_id", "revision"), ("editorial_revisions.page_id", "editorial_revisions.revision"),
                             name="fk_editorial_event_revision"),
    )
