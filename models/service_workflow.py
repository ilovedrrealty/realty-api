"""Private member service agreements; separate from imported professional drafts."""
import uuid
from sqlalchemy import Column, Text, Boolean, Integer, Date, Numeric, TIMESTAMP, ForeignKey, CheckConstraint, UniqueConstraint, Index, func, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from database import Base


class NetworkServicePreference(Base):
    __tablename__ = "network_service_preferences"
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    service_opt_in = Column(Boolean, nullable=False, server_default=text("false"))
    member_alias = Column(Text, nullable=False)
    category_ids = Column(JSONB, nullable=False)
    property_class_ids = Column(JSONB, nullable=False)
    version = Column(Integer, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_service_preference_version"),
        CheckConstraint("char_length(member_alias) <= 120 AND jsonb_typeof(category_ids)='array' AND jsonb_array_length(category_ids)<=12 AND jsonb_typeof(property_class_ids)='array' AND jsonb_array_length(property_class_ids)<=10", name="ck_service_preference_bounds"),
        CheckConstraint("NOT service_opt_in OR (char_length(member_alias)>=1 AND jsonb_array_length(category_ids)>=1 AND jsonb_array_length(property_class_ids)>=1)", name="ck_service_preference_consent"),
        Index("ix_service_preference_optin", "service_opt_in", "user_id"))


class NetworkServiceRequest(Base):
    __tablename__ = "network_service_requests"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    property_id = Column(UUID(as_uuid=True), ForeignKey("network_properties.id", ondelete="RESTRICT"))
    listing_id = Column(UUID(as_uuid=True), ForeignKey("listings.id", ondelete="RESTRICT"))
    custodian_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    requester_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    source_listing_type = Column(Text)
    category_id = Column(Text, nullable=False)
    property_class = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    brief = Column(Text, nullable=False)
    area_label = Column(Text, nullable=False)
    requester_alias = Column(Text, nullable=False)
    desired_start_on = Column(Date)
    recipient_user_codes = Column(JSONB, nullable=False)
    scope_sha256 = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("network_projects.id", ondelete="RESTRICT"), unique=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("(property_id IS NULL) <> (listing_id IS NULL)", name="ck_service_request_source"),
        CheckConstraint("version>=1 AND status IN ('open','awarded','cancelled')", name="ck_service_request_state"),
        CheckConstraint("(status='awarded') = (project_id IS NOT NULL)", name="ck_service_request_project"),
        CheckConstraint("char_length(title) BETWEEN 1 AND 180 AND char_length(brief) BETWEEN 1 AND 4000 AND char_length(area_label)<=120 AND char_length(requester_alias) BETWEEN 1 AND 120 AND scope_sha256 ~ '^[0-9a-f]{64}$'", name="ck_service_request_bounds"),
        CheckConstraint("jsonb_typeof(recipient_user_codes)='array' AND jsonb_array_length(recipient_user_codes) BETWEEN 1 AND 5", name="ck_service_request_recipients"),
        Index("ix_service_request_property", "property_id", "created_at", "id"),
        Index("ix_service_request_listing", "listing_id", "created_at", "id"),
        Index("ix_service_request_custodian", "custodian_id", "created_at", "id"),
        Index("ix_service_request_requester", "requester_id", "created_at", "id"))


class NetworkServiceRecipient(Base):
    __tablename__ = "network_service_recipients"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id = Column(UUID(as_uuid=True), ForeignKey("network_service_requests.id", ondelete="RESTRICT"), nullable=False)
    provider_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    user_code = Column(Integer, nullable=False)
    member_alias = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    current_proposal_id = Column(UUID(as_uuid=True), ForeignKey("network_service_proposals.id", ondelete="RESTRICT", use_alter=True, name="fk_service_recipient_current"))
    __table_args__ = (
        UniqueConstraint("request_id", "provider_id", name="uq_service_recipient_member"),
        CheckConstraint("user_code>0 AND char_length(member_alias) BETWEEN 1 AND 120 AND version>=1", name="ck_service_recipient_bounds"),
        CheckConstraint("status IN ('pending','accepted','declined','revoked','closed','awarded')", name="ck_service_recipient_state"),
        Index("ix_service_recipient_inbox", "provider_id", "status", "request_id"),
        Index("ix_service_recipient_request", "request_id", "id"),
        Index("ix_service_recipient_current", "current_proposal_id"),
        Index("uq_service_recipient_winner", "request_id", unique=True, postgresql_where=text("status='awarded'")))


class NetworkServiceProposal(Base):
    __tablename__ = "network_service_proposals"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id = Column(UUID(as_uuid=True), ForeignKey("network_service_requests.id", ondelete="RESTRICT"), nullable=False)
    recipient_id = Column(UUID(as_uuid=True), ForeignKey("network_service_recipients.id", ondelete="RESTRICT"), nullable=False)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    parent_proposal_id = Column(UUID(as_uuid=True), ForeignKey("network_service_proposals.id", ondelete="RESTRICT"))
    revision_no = Column(Integer, nullable=False)
    kind = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(Text, nullable=False)
    pricing_basis = Column(Text, nullable=False)
    scope_text = Column(Text, nullable=False)
    exclusions = Column(Text, nullable=False)
    estimated_days = Column(Integer)
    valid_until = Column(Date, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("recipient_id", "revision_no", name="uq_service_proposal_revision"),
        CheckConstraint("revision_no>=1 AND version>=1 AND kind IN ('provider_quote','owner_counter') AND status IN ('submitted','accepted','declined','withdrawn','superseded','closed','awarded')", name="ck_service_proposal_state"),
        CheckConstraint("amount>0 AND amount<=9999999999.99 AND currency IN ('USD','DOP') AND pricing_basis IN ('fixed','estimate')", name="ck_service_proposal_price"),
        CheckConstraint("char_length(scope_text) BETWEEN 1 AND 4000 AND char_length(exclusions)<=2000 AND (estimated_days IS NULL OR estimated_days BETWEEN 1 AND 365)", name="ck_service_proposal_bounds"),
        Index("ix_service_proposal_request", "request_id", "id"),
        Index("ix_service_proposal_author", "author_id"),
        Index("ix_service_proposal_parent", "parent_proposal_id"))


class NetworkServiceAction(Base):
    __tablename__ = "network_service_actions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submission_id = Column(UUID(as_uuid=True), nullable=False)
    operation = Column(Text, nullable=False)
    target_id = Column(UUID(as_uuid=True), nullable=False)
    request_id = Column(UUID(as_uuid=True), ForeignKey("network_service_requests.id", ondelete="RESTRICT"))
    recipient_id = Column(UUID(as_uuid=True), ForeignKey("network_service_recipients.id", ondelete="RESTRICT"))
    proposal_id = Column(UUID(as_uuid=True), ForeignKey("network_service_proposals.id", ondelete="RESTRICT"))
    creation_sha256 = Column(Text, nullable=False)
    result_versions = Column(JSONB, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("actor_id", "submission_id", name="uq_service_action_submission"),
        CheckConstraint("creation_sha256 ~ '^[0-9a-f]{64}$' AND jsonb_typeof(result_versions)='object'", name="ck_service_action_hash"),
        CheckConstraint("operation IN ('preference_save','request_create','recipient_accept','recipient_decline','recipient_revoke','provider_quote','owner_counter','counter_accept','counter_decline','proposal_withdraw','request_cancel','request_award')", name="ck_service_action_operation"),
        Index("ix_service_action_request", "request_id", "created_at", "id"),
        Index("ix_service_action_recipient", "recipient_id", "created_at", "id"),
        Index("ix_service_action_proposal", "proposal_id"))
