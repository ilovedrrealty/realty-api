import uuid
from sqlalchemy import Column, Text, TIMESTAMP, func, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from database import Base


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (Index("leads_submission_id_unique", "submission_id", unique=True),)

    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type                = Column(Text, nullable=False)  # property_inquiry | booking | buyer_interest | seller_interest | rental_interest
    name                = Column(Text, nullable=False)
    email               = Column(Text, nullable=True)
    phone               = Column(Text, nullable=True)
    message             = Column(Text, nullable=True)
    property_id         = Column(UUID(as_uuid=True), ForeignKey("listings.id", ondelete="SET NULL"), nullable=True)
    from_user_id        = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assigned_realtor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status              = Column(Text, nullable=False, server_default="new")  # new | assigned | contacted | closed
    created_at          = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    assigned_at         = Column(TIMESTAMP(timezone=True), nullable=True)
    contacted_at        = Column(TIMESTAMP(timezone=True), nullable=True)
    closed_at           = Column(TIMESTAMP(timezone=True), nullable=True)
    ghl_contact_id      = Column(Text, nullable=True)
    ghl_sync_error      = Column(Text, nullable=True)
    ghl_synced_at       = Column(TIMESTAMP(timezone=True), nullable=True)
    # Private operational metadata. Existing and legacy leads remain NULL.
    submission_id       = Column(UUID(as_uuid=True), nullable=True)
    tracking            = Column(JSONB, nullable=True)
