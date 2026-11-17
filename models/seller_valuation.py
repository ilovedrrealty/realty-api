from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database import Base


class SellerValuationIntake(Base):
    __tablename__ = "seller_valuation_intakes"

    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True)
    subject = Column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint("jsonb_typeof(subject) = 'object' AND subject ?& ARRAY['property_type', 'location']",
                        name="ck_seller_valuation_intake_subject"),
    )


class SellerValuationWorkspace(Base):
    __tablename__ = "seller_valuation_workspaces"

    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True)
    subject = Column(JSONB, nullable=False)
    version = Column(Integer, nullable=False)
    evidence_version = Column(Integer, nullable=False)
    review = Column(JSONB(none_as_null=True), nullable=True)
    review_evidence_version = Column(Integer)

    __table_args__ = (
        CheckConstraint("jsonb_typeof(subject) = 'object'", name="ck_seller_valuation_subject"),
        CheckConstraint("version >= 1 AND evidence_version BETWEEN 1 AND version", name="ck_seller_valuation_version"),
        CheckConstraint(
            "(review IS NULL AND review_evidence_version IS NULL) OR "
            "(review IS NOT NULL AND jsonb_typeof(review) = 'object' AND review_evidence_version IS NOT NULL "
            "AND review_evidence_version BETWEEN 1 AND evidence_version)",
            name="ck_seller_valuation_review",
        ),
    )


class SellerValuationComparable(Base):
    __tablename__ = "seller_valuation_comparables"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("seller_valuation_workspaces.lead_id", ondelete="CASCADE"), nullable=False)
    kind = Column(Text, nullable=False)
    property_type = Column(Text, nullable=False)
    location = Column(Text, nullable=False)
    area_m2 = Column(Numeric(10, 2))
    bedrooms = Column(Integer)
    bathrooms = Column(Numeric(4, 1))
    price = Column(Numeric(12, 2), nullable=False)
    currency = Column(Text, nullable=False)
    captured_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    listing_id = Column(UUID(as_uuid=True))  # Keep the capture's source ID if inventory is later removed.
    title = Column(Text)
    closed_on = Column(Date)
    source_label = Column(Text)
    source_reference = Column(Text)
    sold_review = Column(JSONB(none_as_null=True))

    __table_args__ = (
        CheckConstraint("kind IN ('asking', 'sold')", name="ck_seller_valuation_comparable_kind"),
        CheckConstraint("property_type IN ('villa', 'apartment', 'condo', 'land', 'commercial')",
                        name="ck_seller_valuation_comparable_type"),
        CheckConstraint("area_m2 > 0 AND bedrooms >= 0 AND bathrooms >= 0 AND price > 0 AND currency IN ('USD', 'DOP')",
                        name="ck_seller_valuation_comparable_amounts"),
        CheckConstraint(
            "(kind = 'asking' AND listing_id IS NOT NULL AND title IS NOT NULL AND closed_on IS NULL "
            "AND source_label IS NULL AND source_reference IS NULL AND sold_review IS NULL) OR "
            "(kind = 'sold' AND listing_id IS NULL AND title IS NULL AND closed_on IS NOT NULL "
            "AND source_label IS NOT NULL AND source_reference IS NOT NULL "
            "AND (sold_review IS NULL OR jsonb_typeof(sold_review) = 'object'))",
            name="ck_seller_valuation_comparable_source",
        ),
        Index("uq_seller_valuation_asking_listing", "lead_id", "listing_id", unique=True,
              postgresql_where=text("kind = 'asking'")),
        Index("ix_seller_valuation_comparables_lead", "lead_id", "captured_at", "id"),
    )


class SellerValuationFollowUp(Base):
    __tablename__ = "seller_valuation_follow_ups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("seller_valuation_workspaces.lead_id", ondelete="CASCADE"), nullable=False)
    follow_up_on = Column(Date, nullable=False)
    note = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True))

    __table_args__ = (Index("ix_seller_valuation_follow_ups_lead", "lead_id", "follow_up_on", "id"),)
