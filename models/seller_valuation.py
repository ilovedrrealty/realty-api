from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, Date, DateTime, ForeignKey, Integer, Numeric, Text
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


class SellerValuationFollowUp(Base):
    __tablename__ = "seller_valuation_follow_ups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("seller_valuation_workspaces.lead_id", ondelete="CASCADE"), nullable=False)
    follow_up_on = Column(Date, nullable=False)
    note = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True))
