"""Private property collaboration and separately opted-in member walls."""
import uuid
from sqlalchemy import Column, Text, Integer, Boolean, Date, TIMESTAMP, ForeignKey, CheckConstraint, UniqueConstraint, Index, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from database import Base


class NetworkProject(Base):
    __tablename__ = "network_projects"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id = Column(UUID(as_uuid=True), ForeignKey("listings.id", ondelete="RESTRICT"), nullable=False)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    title = Column(Text, nullable=False)
    summary = Column(Text, nullable=False, default="")
    property_class = Column(Text, nullable=False)
    service_category_ids = Column(JSONB, nullable=False)
    status = Column(Text, nullable=False, default="open")
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (CheckConstraint("version >= 1", name="ck_network_project_version"),
                     CheckConstraint("status IN ('open','in_progress','completed','archived')", name="ck_network_project_status"),
                     Index("ix_network_project_listing", "listing_id", "created_at"))


class NetworkProjectMember(Base):
    __tablename__ = "network_project_members"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("network_projects.id", ondelete="RESTRICT"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    role = Column(Text, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_network_project_member"),
                     CheckConstraint("role IN ('manager','professional','viewer')", name="ck_network_member_role"),
                     Index("ix_network_member_user", "user_id", "active"))


class NetworkMilestone(Base):
    __tablename__ = "network_milestones"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("network_projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    title = Column(Text, nullable=False)
    due_date = Column(Date)
    status = Column(Text, nullable=False, default="open")
    version = Column(Integer, nullable=False, default=1)
    __table_args__ = (CheckConstraint("version >= 1", name="ck_network_milestone_version"),
                     CheckConstraint("status IN ('open','in_progress','completed','cancelled')", name="ck_network_milestone_status"))


class NetworkProjectUpdate(Base):
    __tablename__ = "network_project_updates"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("network_projects.id", ondelete="RESTRICT"), nullable=False)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submission_id = Column(UUID(as_uuid=True), nullable=False)
    kind = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    occurred_on = Column(Date, nullable=False)
    correction_of = Column(UUID(as_uuid=True), ForeignKey("network_project_updates.id", ondelete="RESTRICT"))
    media_ids = Column(JSONB, nullable=False, default=list)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("author_id", "submission_id", name="uq_network_update_submission"),
                     CheckConstraint("kind IN ('progress','correction','milestone')", name="ck_network_update_kind"),
                     Index("ix_network_update_project_date", "project_id", "created_at", "id"))


class NetworkProjectComment(Base):
    __tablename__ = "network_project_comments"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    update_id = Column(UUID(as_uuid=True), ForeignKey("network_project_updates.id", ondelete="RESTRICT"), nullable=False, index=True)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submission_id = Column(UUID(as_uuid=True), nullable=False)
    body = Column(Text, nullable=False)
    media_id = Column(UUID(as_uuid=True), ForeignKey("network_media.id", ondelete="RESTRICT"))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("author_id", "submission_id", name="uq_network_project_comment_submission"),)


class SocialNetworkProfile(Base):
    __tablename__ = "social_network_profiles"
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    display_name = Column(Text, nullable=False)
    bio = Column(Text, nullable=False, default="")
    status_text = Column(Text, nullable=False, default="")
    visibility = Column(Text, nullable=False, default="private")
    wall_policy = Column(Text, nullable=False, default="own_only")
    comments_enabled = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=1)
    __table_args__ = (CheckConstraint("visibility IN ('private','members')", name="ck_network_profile_visibility"),
                     CheckConstraint("wall_policy IN ('own_only','approval_required')", name="ck_network_wall_policy"),
                     CheckConstraint("version >= 1", name="ck_network_profile_version"))


class NetworkPost(Base):
    __tablename__ = "network_posts"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_user_id = Column(UUID(as_uuid=True), ForeignKey("social_network_profiles.user_id", ondelete="RESTRICT"), nullable=False)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submission_id = Column(UUID(as_uuid=True), nullable=False)
    body = Column(Text, nullable=False)
    media_ids = Column(JSONB, nullable=False, default=list)
    status = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("author_id", "submission_id", name="uq_network_post_submission"),
                     CheckConstraint("status IN ('pending','visible','hidden')", name="ck_network_post_status"),
                     Index("ix_network_post_wall_date", "profile_user_id", "created_at", "id"))


class NetworkPostComment(Base):
    __tablename__ = "network_post_comments"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("network_posts.id", ondelete="RESTRICT"), nullable=False, index=True)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submission_id = Column(UUID(as_uuid=True), nullable=False)
    body = Column(Text, nullable=False)
    media_id = Column(UUID(as_uuid=True), ForeignKey("network_media.id", ondelete="RESTRICT"))
    hidden = Column(Boolean, nullable=False, default=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("author_id", "submission_id", name="uq_network_post_comment_submission"),)


class NetworkBlock(Base):
    __tablename__ = "network_blocks"
    blocker_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    blocked_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    __table_args__ = (CheckConstraint("blocker_id <> blocked_id", name="ck_network_no_self_block"),)


class NetworkMedia(Base):
    __tablename__ = "network_media"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    uploader_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("network_projects.id", ondelete="RESTRICT"), index=True)
    update_id = Column(UUID(as_uuid=True), ForeignKey("network_project_updates.id", ondelete="RESTRICT"))
    post_id = Column(UUID(as_uuid=True), ForeignKey("network_posts.id", ondelete="RESTRICT"))
    object_key = Column(Text, nullable=False, unique=True)
    sha256 = Column(Text, nullable=False)
    byte_count = Column(Integer, nullable=False)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (CheckConstraint("NOT (update_id IS NOT NULL AND post_id IS NOT NULL)", name="ck_network_media_one_binding"),
                     CheckConstraint("byte_count > 0 AND byte_count <= 5242880", name="ck_network_media_size"),
                     CheckConstraint("width > 0 AND height > 0", name="ck_network_media_dimensions"))


class NetworkAudit(Base):
    __tablename__ = "network_audit"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    target_kind = Column(Text, nullable=False)
    target_id = Column(UUID(as_uuid=True), nullable=False)
    action = Column(Text, nullable=False)
    data = Column(JSONB, nullable=False, default=dict)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
