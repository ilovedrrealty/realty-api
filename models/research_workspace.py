import uuid
from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, Text, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from database import Base


class ResearchWorkspace(Base):
    __tablename__ = "research_workspaces"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, server_default="1")
    data = Column(JSONB, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_research_workspace_version"),
        Index("ix_research_workspace_user_updated", "user_id", "updated_at"),
    )
