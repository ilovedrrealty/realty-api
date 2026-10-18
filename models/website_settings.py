from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database import Base


class WebsiteSettings(Base):
    __tablename__ = "website_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_website_settings_singleton"),
        CheckConstraint("version >= 0", name="ck_website_settings_version"),
    )

    id = Column(Integer, primary_key=True, default=1)
    version = Column(Integer, nullable=False, server_default="0")
    data = Column(JSONB, nullable=False, server_default="{}")
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class WebsiteSettingsRevision(Base):
    __tablename__ = "website_settings_revisions"
    __table_args__ = (CheckConstraint("version >= 0", name="ck_website_settings_revision_version"),)

    version = Column(Integer, primary_key=True)
    snapshot = Column(JSONB, nullable=False)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
