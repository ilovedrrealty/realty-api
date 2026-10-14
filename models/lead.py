import uuid
from sqlalchemy import Column, Text, TIMESTAMP, func, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from database import Base


class Lead(Base):
    __tablename__ = "leads"

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
