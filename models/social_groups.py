"""Separate social-group records with no property/project grants or bindings."""
import uuid
from sqlalchemy import (Column, Text, Integer, Boolean, TIMESTAMP, ForeignKey, CheckConstraint,
                        UniqueConstraint, Index, func, text)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from database import Base


class NetworkGroup(Base):
    __tablename__ = "network_groups"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    owner_alias = Column(Text, nullable=False)
    submission_id = Column(UUID(as_uuid=True), nullable=False)
    creation_sha256 = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=False, server_default="")
    purpose = Column(Text, nullable=False, server_default="community")
    area_label = Column(Text, nullable=False, server_default="")
    visibility = Column(Text, nullable=False, server_default="private")
    join_policy = Column(Text, nullable=False, server_default="approval_required")
    post_policy = Column(Text, nullable=False, server_default="members")
    comments_enabled = Column(Boolean, nullable=False, server_default="true")
    image_media_id = Column(UUID(as_uuid=True), ForeignKey("network_group_media.id", ondelete="RESTRICT", use_alter=True, name="fk_group_image"))
    status = Column(Text, nullable=False, server_default="active")
    version = Column(Integer, nullable=False, server_default="1")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("owner_id", "submission_id", name="uq_group_submission"),
        CheckConstraint("version>=1 AND char_length(name) BETWEEN 1 AND 120 AND char_length(owner_alias) BETWEEN 1 AND 100 AND char_length(description)<=2000 AND char_length(area_label)<=160", name="ck_group_bounds"),
        CheckConstraint("creation_sha256 ~ '^[0-9a-f]{64}$'", name="ck_group_creation_hash"),
        CheckConstraint("purpose IN ('community','professional','local_area','property_project')", name="ck_group_purpose"),
        CheckConstraint("visibility IN ('private','network') AND join_policy IN ('approval_required','open') AND post_policy IN ('members','moderated') AND status IN ('active','archived')", name="ck_group_state"),
        Index("ix_group_owner", "owner_id", "status", "updated_at", "id"),
        Index("ix_group_discovery", "visibility", "status", "name", "id"),
        Index("ix_group_image", "image_media_id"),
    )


class NetworkGroupMembership(Base):
    __tablename__ = "network_group_memberships"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("network_groups.id", ondelete="RESTRICT"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    role = Column(Text, nullable=False, server_default="member")
    state = Column(Text, nullable=False)
    member_alias = Column(Text)
    invited_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    decided_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    invite_expires_at = Column(TIMESTAMP(timezone=True))
    version = Column(Integer, nullable=False, server_default="1")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_group_member"),
        CheckConstraint("version>=1 AND role IN ('admin','moderator','member') AND state IN ('requested','invited','active','declined','cancelled','left','removed','blocked')", name="ck_group_member_state"),
        CheckConstraint("(member_alias IS NULL OR char_length(member_alias) BETWEEN 1 AND 100) AND (state<>'active' OR member_alias IS NOT NULL) AND (state<>'invited' OR invite_expires_at IS NOT NULL)", name="ck_group_member_fields"),
        Index("ix_group_member_user", "user_id", "state", "updated_at", "id"),
        Index("ix_group_member_queue", "group_id", "state", "id"),
        Index("ix_group_member_inviter", "invited_by_id"), Index("ix_group_member_decider", "decided_by_id"),
    )


class NetworkGroupInviteLink(Base):
    __tablename__ = "network_group_invite_links"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("network_groups.id", ondelete="RESTRICT"), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    token_sha256 = Column(Text, nullable=False, unique=True)
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    revoked_at = Column(TIMESTAMP(timezone=True))
    max_uses = Column(Integer, nullable=False)
    use_count = Column(Integer, nullable=False, server_default="0")
    version = Column(Integer, nullable=False, server_default="1")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("token_sha256 ~ '^[0-9a-f]{64}$' AND version>=1 AND max_uses BETWEEN 1 AND 1000 AND use_count BETWEEN 0 AND max_uses AND expires_at>created_at", name="ck_group_link_bounds"),
        Index("ix_group_link_group", "group_id", "created_at", "id"), Index("ix_group_link_creator", "created_by_id"),
    )


class NetworkGroupPost(Base):
    __tablename__ = "network_group_posts"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("network_groups.id", ondelete="RESTRICT"), nullable=False)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submission_id = Column(UUID(as_uuid=True), nullable=False)
    body = Column(Text, nullable=False)
    media_ids = Column(JSONB, nullable=False, server_default="[]")
    created_audience = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, server_default="1")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("author_id", "submission_id", name="uq_group_post_submission"),
        CheckConstraint("version>=1 AND char_length(body)<=4000 AND jsonb_typeof(media_ids)='array' AND jsonb_array_length(media_ids)<=6 AND (char_length(body)>0 OR jsonb_array_length(media_ids)>0)", name="ck_group_post_bounds"),
        CheckConstraint("created_audience IN ('private','network') AND status IN ('pending','visible','hidden')", name="ck_group_post_state"),
        Index("ix_group_post_feed", "group_id", "status", "created_at", "id"), Index("ix_group_post_author", "author_id"),
    )


class NetworkGroupComment(Base):
    __tablename__ = "network_group_comments"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("network_group_posts.id", ondelete="RESTRICT"), nullable=False)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submission_id = Column(UUID(as_uuid=True), nullable=False)
    body = Column(Text, nullable=False)
    media_id = Column(UUID(as_uuid=True), ForeignKey("network_group_media.id", ondelete="RESTRICT", use_alter=True, name="fk_group_comment_media"))
    hidden = Column(Boolean, nullable=False, server_default="false")
    version = Column(Integer, nullable=False, server_default="1")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("author_id", "submission_id", name="uq_group_comment_submission"),
        CheckConstraint("version>=1 AND char_length(body) BETWEEN 1 AND 2000", name="ck_group_comment_bounds"),
        Index("ix_group_comment_post", "post_id", "created_at", "id"), Index("ix_group_comment_author", "author_id"),
        Index("ix_group_comment_media", "media_id"),
    )


class NetworkGroupMedia(Base):
    __tablename__ = "network_group_media"
    id = Column(UUID(as_uuid=True), primary_key=True)
    group_id = Column(UUID(as_uuid=True), ForeignKey("network_groups.id", ondelete="RESTRICT"), nullable=False)
    uploader_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    kind = Column(Text, nullable=False)
    post_id = Column(UUID(as_uuid=True), ForeignKey("network_group_posts.id", ondelete="RESTRICT"))
    submission_id = Column(UUID(as_uuid=True), nullable=False)
    object_key = Column(Text, nullable=False, unique=True)
    sha256 = Column(Text, nullable=False)
    byte_count = Column(Integer, nullable=False)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("uploader_id", "submission_id", name="uq_group_media_submission"),
        CheckConstraint("kind IN ('group_image','post') AND (kind='post' OR post_id IS NULL) AND sha256 ~ '^[0-9a-f]{64}$' AND byte_count BETWEEN 1 AND 5242880 AND width>0 AND height>0", name="ck_group_media_bounds"),
        Index("ix_group_media_context", "group_id", "post_id", "id"), Index("ix_group_media_uploader", "uploader_id", "created_at", "id"),
    )


class NetworkGroupAction(Base):
    __tablename__ = "network_group_actions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("network_groups.id", ondelete="RESTRICT"), nullable=False)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submission_id = Column(UUID(as_uuid=True), nullable=False)
    operation = Column(Text, nullable=False)
    target_kind = Column(Text, nullable=False)
    target_id = Column(UUID(as_uuid=True), nullable=False)
    creation_sha256 = Column(Text, nullable=False)
    membership_id = Column(UUID(as_uuid=True), ForeignKey("network_group_memberships.id", ondelete="RESTRICT"))
    link_id = Column(UUID(as_uuid=True), ForeignKey("network_group_invite_links.id", ondelete="RESTRICT"))
    result_versions = Column(JSONB, nullable=False)
    reason = Column(Text, nullable=False, server_default="")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("actor_id", "submission_id", name="uq_group_action_submission"),
        CheckConstraint("creation_sha256 ~ '^[0-9a-f]{64}$' AND target_kind IN ('group','membership','link','post','comment','media') AND char_length(reason)<=500", name="ck_group_action_fields"),
        CheckConstraint("operation IN ('group_create','group_edit','archive','join','request','cancel','accept_invite','decline_invite','leave','invite','approve_member','decline_member','revoke_invite','set_role','remove','block','unblock','link_create','link_revoke','link_accept','post_create','post_approve','post_hide','comment_create','comment_hide','media_upload')", name="ck_group_action_operation"),
        Index("ix_group_action_group", "group_id", "created_at", "id"), Index("ix_group_action_actor", "actor_id", "created_at", "id"),
        Index("ix_group_action_member", "membership_id"), Index("ix_group_action_link", "link_id"),
        Index("uq_group_link_redemption", "link_id", "actor_id", unique=True, postgresql_where=text("operation='link_accept'")),
    )
