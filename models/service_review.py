"""One private review of a completed service award."""
from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, Text, TIMESTAMP, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from database import Base


class NetworkServiceReview(Base):
    __tablename__ = "network_service_reviews"

    id = Column(UUID(as_uuid=True), primary_key=True)
    request_id = Column(UUID(as_uuid=True), ForeignKey("network_service_work.request_id", ondelete="RESTRICT"), nullable=False)
    reviewer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    provider_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submission_id = Column(UUID(as_uuid=True), nullable=False)
    stars = Column(Integer, nullable=False)
    comment = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("request_id", name="uq_service_review_request"),
        UniqueConstraint("reviewer_id", "submission_id", name="uq_service_review_submission"),
        CheckConstraint("reviewer_id <> provider_id AND stars BETWEEN 1 AND 5", name="ck_service_review_stars"),
        CheckConstraint("comment IS NULL OR (char_length(comment) BETWEEN 1 AND 2000 "
                        "AND comment = btrim(comment, ' ' || chr(9) || chr(10) || chr(13)) "
                        "AND translate(comment, chr(9) || chr(10) || chr(13), '') COLLATE \"C\" !~ '[[:cntrl:]]')",
                        name="ck_service_review_comment"),
    )
