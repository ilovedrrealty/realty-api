"""Owner-only maintenance preferences, schedules, and retained occurrences."""
from sqlalchemy import Boolean, CheckConstraint, Column, Date, ForeignKey, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID

from database import Base


class NetworkMaintenancePreference(Base):
    __tablename__ = "network_maintenance_preferences"

    id = Column(UUID(as_uuid=True), primary_key=True)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    property_id = Column(UUID(as_uuid=True), ForeignKey("network_properties.id", ondelete="RESTRICT"))
    listing_id = Column(UUID(as_uuid=True), ForeignKey("listings.id", ondelete="RESTRICT"))
    enabled = Column(Boolean, nullable=False, server_default=text("false"))
    lead_days = Column(Integer, nullable=False, server_default="7")
    version = Column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("actor_id", "property_id", name="uq_maintenance_preference_registry"),
        UniqueConstraint("actor_id", "listing_id", name="uq_maintenance_preference_listing"),
        CheckConstraint("(property_id IS NULL) <> (listing_id IS NULL)", name="ck_maintenance_preference_one_source"),
        CheckConstraint("lead_days BETWEEN 0 AND 30 AND version >= 0 AND (version > 0 OR (NOT enabled AND lead_days = 7))",
                        name="ck_maintenance_preference_bounds"),
    )


class NetworkMaintenanceSchedule(Base):
    __tablename__ = "network_maintenance_schedules"

    id = Column(UUID(as_uuid=True), primary_key=True)
    preference_id = Column(UUID(as_uuid=True), ForeignKey("network_maintenance_preferences.id", ondelete="RESTRICT"), nullable=False)
    title = Column(Text, nullable=False)
    repeat = Column(Text, nullable=False)
    anchor_on = Column(Date, nullable=False)
    status = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("char_length(title) BETWEEN 1 AND 180 AND version >= 1", name="ck_maintenance_schedule_bounds"),
        CheckConstraint("repeat IN ('none','weekly','monthly','quarterly','yearly')", name="ck_maintenance_schedule_repeat"),
        CheckConstraint("status IN ('active','paused','completed','stopped')", name="ck_maintenance_schedule_status"),
        Index("ix_maintenance_schedule_preference", "preference_id", "id"),
    )


class NetworkMaintenanceOccurrence(Base):
    __tablename__ = "network_maintenance_occurrences"

    schedule_id = Column(UUID(as_uuid=True), ForeignKey("network_maintenance_schedules.id", ondelete="RESTRICT"), primary_key=True)
    due_on = Column(Date, primary_key=True)
    status = Column(Text, nullable=False)
    snoozed_until = Column(Date)
    closed_on = Column(Date)

    __table_args__ = (
        CheckConstraint("status IN ('open','completed','stopped') AND (status = 'open') = (closed_on IS NULL) "
                        "AND (status = 'open' OR snoozed_until IS NULL)", name="ck_maintenance_occurrence_state"),
        Index("uq_maintenance_occurrence_one_open", "schedule_id", unique=True, postgresql_where=text("status = 'open'")),
    )


class NetworkMaintenanceAction(Base):
    __tablename__ = "network_maintenance_actions"

    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    submission_id = Column(UUID(as_uuid=True), primary_key=True)
    preference_id = Column(UUID(as_uuid=True), ForeignKey("network_maintenance_preferences.id", ondelete="RESTRICT"), nullable=False)
    schedule_id = Column(UUID(as_uuid=True), ForeignKey("network_maintenance_schedules.id", ondelete="RESTRICT"))
    operation = Column(Text, nullable=False)
    creation_sha256 = Column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("operation IN ('preferences','create','complete','snooze','pause','resume','stop') "
                        "AND (operation = 'preferences') = (schedule_id IS NULL) AND creation_sha256 ~ '^[0-9a-f]{64}$'",
                        name="ck_maintenance_action_scope"),
    )
