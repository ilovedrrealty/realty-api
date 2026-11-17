"""Private documents and immutable versions in an existing transaction room."""

from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, ForeignKeyConstraint, Index, Integer, Text, TIMESTAMP, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from database import Base


class TransactionDocument(Base):
    __tablename__ = "transaction_documents"

    room_id = Column(UUID(as_uuid=True), ForeignKey("transaction_rooms.id", ondelete="RESTRICT"), primary_key=True)
    id = Column(UUID(as_uuid=True), primary_key=True)
    title = Column(Text, nullable=False)
    creator_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 160", name="ck_transaction_document_title"),
        Index("ix_transaction_document_creator", creator_id),
    )


class TransactionDocumentVersion(Base):
    __tablename__ = "transaction_document_versions"

    room_id = Column(UUID(as_uuid=True), primary_key=True)
    document_id = Column(UUID(as_uuid=True), primary_key=True)
    id = Column(UUID(as_uuid=True), primary_key=True)
    number = Column(Integer, nullable=False)
    request_id = Column(UUID(as_uuid=True), nullable=False)
    uploader_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    file_name = Column(Text, nullable=False)
    content_type = Column(Text, nullable=False)
    byte_count = Column(Integer, nullable=False)
    sha256 = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    uploaded_at = Column(TIMESTAMP(timezone=True))
    reviewed_at = Column(TIMESTAMP(timezone=True))
    reviewer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    share_with_buyer = Column(Boolean, nullable=False, server_default="false")
    professional_grant_id = Column(UUID(as_uuid=True), ForeignKey("transaction_room_grants.id", ondelete="SET NULL"))

    __table_args__ = (
        ForeignKeyConstraint(["room_id", "document_id"], ["transaction_documents.room_id", "transaction_documents.id"],
                             name="fk_transaction_document_version_parent", ondelete="RESTRICT"),
        UniqueConstraint("room_id", "document_id", "number", name="uq_transaction_document_version_number"),
        UniqueConstraint("room_id", "uploader_id", "request_id", name="uq_transaction_document_request"),
        CheckConstraint("number >= 1 AND byte_count BETWEEN 1 AND 6291456 "
                        "AND char_length(btrim(file_name)) BETWEEN 1 AND 140 "
                        "AND content_type IN ('application/pdf','image/jpeg','image/png') "
                        "AND sha256 ~ '^[a-f0-9]{64}$'", name="ck_transaction_document_version_file"),
        CheckConstraint(
            "(status = 'reserved' AND uploaded_at IS NULL AND reviewed_at IS NULL) "
            "OR (status = 'submitted' AND uploaded_at IS NOT NULL AND reviewed_at IS NULL) "
            "OR (status IN ('approved','rejected') AND uploaded_at IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_transaction_document_version_state",
        ),
        CheckConstraint("status = 'approved' OR (NOT share_with_buyer AND professional_grant_id IS NULL)",
                        name="ck_transaction_document_version_audience"),
        Index("ix_transaction_document_version_uploader", uploader_id),
        Index("ix_transaction_document_version_reviewer", reviewer_id),
        Index("ix_transaction_document_version_grant", professional_grant_id, room_id),
    )
