from sqlalchemy import CheckConstraint, Column, ForeignKey
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
