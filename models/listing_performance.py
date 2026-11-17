from sqlalchemy import BigInteger, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import UUID

from database import Base


class ListingDailyView(Base):
    __tablename__ = "listing_daily_views"

    listing_id = Column(UUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True)
    view_date = Column(Date, primary_key=True)
    view_count = Column(BigInteger, nullable=False)

    __table_args__ = (CheckConstraint("view_count > 0", name="ck_listing_daily_view_count"),)


class ListingViewCoverage(Base):
    __tablename__ = "listing_view_coverage"

    id = Column(SmallInteger, primary_key=True, autoincrement=False)
    started_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (CheckConstraint("id = 1", name="ck_listing_view_coverage_singleton"),)


class ListingPerformanceEntry(Base):
    __tablename__ = "listing_performance_entries"

    listing_id = Column(UUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True)
    id = Column(UUID(as_uuid=True), primary_key=True)
    kind = Column(Text, nullable=False)
    occurred_on = Column(Date, nullable=False)
    text = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    __table_args__ = (
        CheckConstraint("kind IN ('completed_showing', 'owner_feedback', 'marketing_update')",
                        name="ck_listing_performance_entry_kind"),
        CheckConstraint(
            "(kind = 'completed_showing' AND text IS NULL) OR "
            "(kind <> 'completed_showing' AND text IS NOT NULL AND char_length(btrim(text)) BETWEEN 1 AND 1000)",
            name="ck_listing_performance_entry_text",
        ),
        Index("ix_listing_performance_entries_date", listing_id, occurred_on.desc(), id),
    )
