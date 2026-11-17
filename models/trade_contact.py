"""Private trade contacts and their independent source observations."""

from sqlalchemy import CHAR, DateTime, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import mapped_column

from database import Base


class TradeContact(Base):
    __tablename__ = "trade_contacts"
    __table_args__ = (Index("ix_trade_contacts_category_province_name", "category", "province", "name", "id"),)

    id = mapped_column(UUID(as_uuid=True), primary_key=True)
    identity_key = mapped_column(CHAR(64), nullable=False, unique=True)
    name = mapped_column(Text, nullable=False)
    category = mapped_column(Text, nullable=False)
    province = mapped_column(Text)
    locality = mapped_column(Text)
    details = mapped_column(JSONB, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class TradeContactSource(Base):
    __tablename__ = "trade_contact_sources"
    __table_args__ = (UniqueConstraint("contact_id", "source_key", name="uq_trade_contact_source"),)

    id = mapped_column(UUID(as_uuid=True), primary_key=True)
    contact_id = mapped_column(UUID(as_uuid=True), ForeignKey("trade_contacts.id", ondelete="CASCADE"), nullable=False)
    source_key = mapped_column(CHAR(64), nullable=False)
    evidence = mapped_column(JSONB, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
