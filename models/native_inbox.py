"""Private native conversations; external-channel entries are manual records only."""
from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Column, ForeignKey, ForeignKeyConstraint, Index,
                        Integer, Text, TIMESTAMP, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import UUID

from database import Base


class InboxPreference(Base):
    __tablename__ = "inbox_preferences"
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    visibility = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    __table_args__ = (CheckConstraint("visibility IN ('private','members') AND version>=1", name="ck_inbox_preference"),
                     Index("ix_inbox_preference_discovery", "visibility", "user_id"))


class InboxBlock(Base):
    __tablename__ = "inbox_blocks"
    blocker_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    blocked_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (CheckConstraint("blocker_id<>blocked_id", name="ck_inbox_no_self_block"),
                     Index("ix_inbox_block_reverse", "blocked_id", "blocker_id"))


class InboxConversation(Base):
    __tablename__ = "inbox_conversations"
    id = Column(UUID(as_uuid=True), primary_key=True)
    user_low_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    user_high_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    brokerage_subject_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    policy_version = Column(Text, nullable=False)
    last_sequence = Column(BigInteger, nullable=False, server_default=text("0"))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    last_entry_at = Column(TIMESTAMP(timezone=True))
    __table_args__ = (
        UniqueConstraint("user_low_id", "user_high_id", name="uq_inbox_direct_pair"),
        CheckConstraint("user_low_id<user_high_id AND created_by_id IN (user_low_id,user_high_id) "
                        "AND (brokerage_subject_id IS NULL OR brokerage_subject_id IN (user_low_id,user_high_id)) "
                        "AND policy_version='native-inbox-v1' AND last_sequence BETWEEN 0 AND 9007199254740991 AND ((last_sequence=0)=(last_entry_at IS NULL))", name="ck_inbox_conversation"),
        Index("ix_inbox_conversation_low", "user_low_id", "last_entry_at", "id"),
        Index("ix_inbox_conversation_high", "user_high_id", "last_entry_at", "id"),
        Index("ix_inbox_conversation_creator", "created_by_id"),
        Index("ix_inbox_conversation_subject", "brokerage_subject_id"))


COMMAND_ACTIONS = "'conversation_opened','entry_created','read_marked','visibility_saved','block_saved','review_opened'"


class InboxCommand(Base):
    __tablename__ = "inbox_commands"
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    submission_id = Column(UUID(as_uuid=True), primary_key=True)
    action = Column(Text, nullable=False)
    payload_sha256 = Column(Text, nullable=False)
    target_id = Column(UUID(as_uuid=True), nullable=False)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("inbox_conversations.id", ondelete="RESTRICT"))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (ForeignKeyConstraint(["actor_id", "submission_id"], ["inbox_audit.actor_id", "inbox_audit.submission_id"],
                         deferrable=True, initially="DEFERRED", use_alter=True, name="fk_inbox_command_audit"),
                     CheckConstraint(f"action IN ({COMMAND_ACTIONS}) AND payload_sha256 ~ '^[0-9a-f]{{64}}$'", name="ck_inbox_command"),
                     Index("ix_inbox_command_conversation", "conversation_id", "created_at"))


class InboxEntry(Base):
    __tablename__ = "inbox_entries"
    id = Column(UUID(as_uuid=True), primary_key=True)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("inbox_conversations.id", ondelete="RESTRICT"), nullable=False)
    sequence = Column(BigInteger, nullable=False)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submission_id = Column(UUID(as_uuid=True), nullable=False)
    author_role = Column(Text, nullable=False)
    kind = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    occurred_at = Column(TIMESTAMP(timezone=True))
    reported_direction = Column(Text)
    call_outcome = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        ForeignKeyConstraint(["author_id", "submission_id"], ["inbox_commands.actor_id", "inbox_commands.submission_id"],
                             ondelete="RESTRICT", name="fk_inbox_entry_command"),
        UniqueConstraint("conversation_id", "sequence", name="uq_inbox_entry_sequence"),
        UniqueConstraint("author_id", "submission_id", name="uq_inbox_entry_submission"),
        CheckConstraint("sequence BETWEEN 1 AND 9007199254740991 AND author_role IN ('buyer','owner','realtor','admin')", name="ck_inbox_entry_actor"),
        CheckConstraint("char_length(body) BETWEEN 1 AND 2000 AND body=btrim(body,' '||chr(9)||chr(10)||chr(13)) "
                        "AND translate(body,chr(9)||chr(10)||chr(13),'') COLLATE \"C\" !~ '[[:cntrl:]]' "
                        "AND body !~ ('['||chr(128)||'-'||chr(159)||']')", name="ck_inbox_entry_body"),
        CheckConstraint("(kind='message' AND occurred_at IS NULL AND reported_direction IS NULL AND call_outcome IS NULL) "
                        "OR (kind='manual_whatsapp' AND occurred_at IS NOT NULL AND reported_direction IS NOT NULL AND reported_direction IN ('incoming','outgoing') AND call_outcome IS NULL) "
                        "OR (kind='manual_call' AND occurred_at IS NOT NULL AND reported_direction IS NOT NULL AND reported_direction IN ('incoming','outgoing') "
                        "AND call_outcome IS NOT NULL AND call_outcome IN ('connected','voicemail','missed','no_answer','declined','unspecified'))", name="ck_inbox_entry_kind"),
        CheckConstraint("occurred_at IS NULL OR (occurred_at>='1970-01-01T00:00:00Z'::timestamptz "
                        "AND occurred_at<=created_at+interval '5 minutes')", name="ck_inbox_entry_time"),
        Index("ix_inbox_entry_author", "author_id", "created_at", "id"))


class InboxReadState(Base):
    __tablename__ = "inbox_read_states"
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("inbox_conversations.id", ondelete="RESTRICT"), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    last_read_sequence = Column(BigInteger, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (CheckConstraint("last_read_sequence BETWEEN 0 AND 9007199254740991", name="ck_inbox_read_sequence"),
                     Index("ix_inbox_read_user", "user_id", "conversation_id"))


class InboxAudit(Base):
    __tablename__ = "inbox_audit"
    id = Column(UUID(as_uuid=True), primary_key=True)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submission_id = Column(UUID(as_uuid=True), nullable=False)
    actor_role = Column(Text, nullable=False)
    policy_version = Column(Text, nullable=False)
    action = Column(Text, nullable=False)
    target_id = Column(UUID(as_uuid=True), nullable=False)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("inbox_conversations.id", ondelete="RESTRICT"))
    source_kind = Column(Text)
    source_id = Column(UUID(as_uuid=True))
    recipient_id = Column(UUID(as_uuid=True))
    reason = Column(Text)
    expires_at = Column(TIMESTAMP(timezone=True))
    result_flag = Column(Boolean)
    result_visibility = Column(Text)
    result_sequence = Column(BigInteger)
    review_id = Column(UUID(as_uuid=True), ForeignKey("inbox_audit.id", ondelete="RESTRICT"))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        ForeignKeyConstraint(["actor_id", "submission_id"], ["inbox_commands.actor_id", "inbox_commands.submission_id"],
                             ondelete="RESTRICT", name="fk_inbox_audit_command"),
        UniqueConstraint("actor_id", "submission_id", name="uq_inbox_audit_submission"),
        CheckConstraint(f"action IN ({COMMAND_ACTIONS}) AND actor_role IN ('buyer','owner','realtor','admin') AND policy_version='native-inbox-v1'", name="ck_inbox_audit_actor"),
        CheckConstraint("(action='review_opened' AND actor_role='admin' AND source_kind IS NOT NULL AND source_kind IN ('native','service_award','service_negotiation') "
                        "AND source_id IS NOT NULL AND reason IS NOT NULL AND reason IN ('oversight','support') AND expires_at IS NOT NULL AND expires_at>created_at "
                        "AND ((source_kind='service_negotiation')=(recipient_id IS NOT NULL))) "
                        "OR (action<>'review_opened' AND source_kind IS NULL AND source_id IS NULL AND recipient_id IS NULL "
                        "AND reason IS NULL AND expires_at IS NULL)", name="ck_inbox_audit_review"),
        CheckConstraint("((action='block_saved')=(result_flag IS NOT NULL)) "
                        "AND ((action='visibility_saved')=(result_visibility IS NOT NULL)) "
                        "AND (result_visibility IS NULL OR result_visibility IN ('private','members')) "
                        "AND ((action IN ('entry_created','read_marked'))=(result_sequence IS NOT NULL)) "
                        "AND (result_sequence IS NULL OR result_sequence BETWEEN 0 AND 9007199254740991)", name="ck_inbox_audit_result"),
        CheckConstraint("review_id IS NULL OR (action='entry_created' AND actor_role='admin')", name="ck_inbox_audit_parent_review"),
        Index("ix_inbox_audit_actor", "actor_id", "created_at", "id"),
        Index("ix_inbox_audit_conversation", "conversation_id", "created_at", "id"),
        Index("ix_inbox_audit_review", "review_id"),
        Index("ix_inbox_audit_source", "source_kind", "source_id", "recipient_id"))
