"""Messages visible only to the two current participants of an awarded request."""
from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Text, TIMESTAMP, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from database import Base


class NetworkServiceConversationMessage(Base):
    __tablename__ = "network_service_conversation_messages"

    id = Column(UUID(as_uuid=True), primary_key=True)
    request_id = Column(UUID(as_uuid=True), ForeignKey("network_service_requests.id", ondelete="RESTRICT"), nullable=False)
    sender_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submission_id = Column(UUID(as_uuid=True), nullable=False)
    sender_role = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("sender_id", "submission_id", name="uq_service_conversation_sender_submission"),
        CheckConstraint("sender_role IN ('owner','provider')", name="ck_service_conversation_sender_role"),
        CheckConstraint("char_length(body) BETWEEN 1 AND 2000 AND body = btrim(body, ' ' || chr(9) || chr(10) || chr(13)) "
                        "AND translate(body, chr(9) || chr(10) || chr(13), '') COLLATE \"C\" !~ '[[:cntrl:]]'",
                        name="ck_service_conversation_body"),
        Index("ix_service_conversation_request_newest", "request_id", "created_at", "id"),
    )
