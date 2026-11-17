"""Private buyer transaction rooms and their explicitly managed participation."""

from sqlalchemy import CheckConstraint, Column, Date, ForeignKey, Index, Integer, Text, TIMESTAMP, text
from sqlalchemy.dialects.postgresql import UUID

from database import Base


class TransactionRoom(Base):
    __tablename__ = "transaction_rooms"

    id = Column(UUID(as_uuid=True), primary_key=True)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="RESTRICT"), nullable=False, unique=True)
    listing_id = Column(UUID(as_uuid=True), ForeignKey("listings.id", ondelete="RESTRICT"), nullable=False)
    buyer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    title = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("version >= 1 AND status IN ('open','closed','retired') "
                        "AND char_length(btrim(title)) BETWEEN 1 AND 120", name="ck_transaction_room_state"),
        Index("ix_transaction_room_buyer", "buyer_id"),
        Index("ix_transaction_room_listing", "listing_id"),
    )


class TransactionRoomGrant(Base):
    __tablename__ = "transaction_room_grants"

    id = Column(UUID(as_uuid=True), primary_key=True)
    room_id = Column(UUID(as_uuid=True), ForeignKey("transaction_rooms.id", ondelete="CASCADE"), nullable=False)
    recipient_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    state = Column(Text, nullable=False)
    invited_at = Column(TIMESTAMP(timezone=True), nullable=False)
    accepted_at = Column(TIMESTAMP(timezone=True))
    revoked_at = Column(TIMESTAMP(timezone=True))

    __table_args__ = (
        CheckConstraint("(state = 'pending' AND accepted_at IS NULL AND revoked_at IS NULL) "
                        "OR (state = 'active' AND accepted_at IS NOT NULL AND revoked_at IS NULL) "
                        "OR (state = 'revoked' AND revoked_at IS NOT NULL)", name="ck_transaction_grant_state"),
        Index("uq_transaction_current_grant", "room_id", "recipient_id", unique=True,
              postgresql_where=text("state IN ('pending','active')")),
        Index("ix_transaction_grant_recipient", "recipient_id", "state", "invited_at"),
        Index("ix_transaction_grant_room", "room_id"),
    )


class TransactionTask(Base):
    __tablename__ = "transaction_room_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True)
    room_id = Column(UUID(as_uuid=True), ForeignKey("transaction_rooms.id", ondelete="CASCADE"), nullable=False)
    title = Column(Text, nullable=False)
    assignee_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    due_on = Column(Date)
    status = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("version >= 1 AND status IN ('open','in_progress','done','cancelled') "
                        "AND char_length(btrim(title)) BETWEEN 1 AND 160", name="ck_transaction_task_state"),
        Index("ix_transaction_task_room", "room_id"),
        Index("ix_transaction_task_assignee", "assignee_user_id"),
    )


class TransactionMilestone(Base):
    __tablename__ = "transaction_room_milestones"

    id = Column(UUID(as_uuid=True), primary_key=True)
    room_id = Column(UUID(as_uuid=True), ForeignKey("transaction_rooms.id", ondelete="CASCADE"), nullable=False)
    title = Column(Text, nullable=False)
    target_on = Column(Date)
    actual_on = Column(Date)
    version = Column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("version >= 1 AND char_length(btrim(title)) BETWEEN 1 AND 160", name="ck_transaction_milestone_state"),
        Index("ix_transaction_milestone_room", "room_id"),
    )


class TransactionRoomEvent(Base):
    __tablename__ = "transaction_room_events"

    room_id = Column(UUID(as_uuid=True), ForeignKey("transaction_rooms.id", ondelete="CASCADE"), primary_key=True)
    sequence = Column(Integer, primary_key=True)
    action = Column(Text, nullable=False)
    actor_role = Column(Text, nullable=False)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    target_id = Column(UUID(as_uuid=True))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("sequence >= 1 AND action ~ '^[a-z][a-z_]{1,60}$' "
                        "AND actor_role IN ('admin','realtor','buyer','professional','system')",
                        name="ck_transaction_event"),
        Index("ix_transaction_event_actor", "actor_id"),
    )
