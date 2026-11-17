"""Private referrals and receipts for changes to their state."""

from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, Text, TIMESTAMP, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID

from database import Base


class NetworkServiceReferral(Base):
    __tablename__ = "network_service_referrals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    request_id = Column(UUID(as_uuid=True), ForeignKey("network_service_requests.id", ondelete="RESTRICT"), nullable=False)
    proposer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    provider_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    provider_alias = Column(Text, nullable=False)
    area_id = Column(Text, nullable=False)
    language = Column(Text)
    relationship = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    offered_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    recipient_id = Column(UUID(as_uuid=True), ForeignKey("network_service_recipients.id", ondelete="RESTRICT"))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("recipient_id", name="uq_service_referral_recipient"),
        CheckConstraint(
            "char_length(provider_alias) BETWEEN 1 AND 120 AND btrim(provider_alias) <> '' "
            "AND char_length(area_id) BETWEEN 1 AND 64 AND btrim(area_id) <> '' "
            "AND (language IS NULL OR (char_length(language) BETWEEN 1 AND 32 AND btrim(language) <> ''))",
            name="ck_service_referral_bounds",
        ),
        CheckConstraint("relationship IN ('worked_together','know_their_work','no_prior_work')",
                        name="ck_service_referral_relationship"),
        CheckConstraint(
            "version >= 1 AND ((status = 'proposed' AND offered_by_id IS NULL) "
            "OR (status IN ('offered','accepted','declined') AND offered_by_id IS NOT NULL) OR status = 'revoked')",
            name="ck_service_referral_offer_state",
        ),
        CheckConstraint("(status = 'accepted') = (recipient_id IS NOT NULL)", name="ck_service_referral_recipient_state"),
        Index("uq_service_referral_active_provider", "request_id", "provider_id", unique=True,
              postgresql_where=text("status IN ('proposed','offered')")),
        Index("ix_service_referral_request", "request_id", "created_at", "id"),
        Index("ix_service_referral_proposer", "proposer_id", "created_at", "id"),
        Index("ix_service_referral_provider", "provider_id", "status", "created_at", "id"),
        Index("ix_service_referral_offerer", "offered_by_id"),
    )


class NetworkServiceReferralEvent(Base):
    __tablename__ = "network_service_referral_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    referral_id = Column(UUID(as_uuid=True), ForeignKey("network_service_referrals.id", ondelete="RESTRICT"), nullable=False)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submission_id = Column(UUID(as_uuid=True), nullable=False)
    operation = Column(Text, nullable=False)
    input_sha256 = Column(Text, nullable=False)
    result_version = Column(Integer, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("actor_id", "submission_id", name="uq_service_referral_event_submission"),
        UniqueConstraint("referral_id", "result_version", name="uq_service_referral_event_version"),
        CheckConstraint(
            "(operation = 'create' AND result_version = 1) "
            "OR (operation IN ('offer','accept','decline','revoke') AND result_version >= 2)",
            name="ck_service_referral_event_operation",
        ),
        CheckConstraint("input_sha256 ~ '^[a-f0-9]{64}$'", name="ck_service_referral_event_hash"),
    )
