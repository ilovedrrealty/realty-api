import uuid
from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, Text, TIMESTAMP, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from database import Base


class ProfessionalProfile(Base):
    __tablename__ = "professional_profiles"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    version = Column(Integer, nullable=False, server_default="1")
    draft_data = Column(JSONB, nullable=False, server_default="{}")
    published_data = Column(JSONB, nullable=True)
    review_status = Column(Text, nullable=False, server_default="draft")
    listing_status = Column(Text, nullable=False, server_default="hidden")
    review_note = Column(Text, nullable=True)
    submitted_at = Column(TIMESTAMP(timezone=True), nullable=True)
    reviewed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_professional_version"),
        CheckConstraint("review_status IN ('draft','pending','approved','rejected')", name="ck_professional_review_status"),
        CheckConstraint("listing_status IN ('hidden','published','suspended')", name="ck_professional_listing_status"),
    )


class ProfessionalProfileRevision(Base):
    __tablename__ = "professional_profile_revisions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id = Column(UUID(as_uuid=True), ForeignKey("professional_profiles.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    action = Column(Text, nullable=False)
    snapshot = Column(JSONB, nullable=False)
    note = Column(Text, nullable=True)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("profile_id", "version", name="uq_professional_revision_version"),)
