"""Personal saved service providers keyed by stable member identities."""
from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, TIMESTAMP, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from database import Base


class NetworkServiceFavorite(Base):
    __tablename__ = "network_service_favorites"

    id = Column(UUID(as_uuid=True), primary_key=True)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    saved_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("actor_id", "provider_id", name="uq_service_favorite_actor_provider"),
        CheckConstraint("actor_id <> provider_id", name="ck_service_favorite_distinct_members"),
        Index("ix_service_favorite_actor_newest", "actor_id", "saved_at", "id"),
        Index("ix_service_favorite_provider", "provider_id"),
    )
