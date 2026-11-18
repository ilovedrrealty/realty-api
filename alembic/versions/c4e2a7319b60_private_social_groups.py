"""Separate member groups; no existing grants, content or provider settings change."""
from alembic import op

revision = "c4e2a7319b60"
down_revision = "b19d8e4c720a"
branch_labels = None
depends_on = None

TABLES = ("network_groups", "network_group_memberships", "network_group_invite_links", "network_group_posts",
          "network_group_comments", "network_group_media", "network_group_actions")


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS network_groups (
        id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        owner_alias TEXT NOT NULL, submission_id UUID NOT NULL, creation_sha256 TEXT NOT NULL, name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '', purpose TEXT NOT NULL DEFAULT 'community', area_label TEXT NOT NULL DEFAULT '',
        visibility TEXT NOT NULL DEFAULT 'private', join_policy TEXT NOT NULL DEFAULT 'approval_required',
        post_policy TEXT NOT NULL DEFAULT 'members', comments_enabled BOOLEAN NOT NULL DEFAULT true,
        image_media_id UUID, status TEXT NOT NULL DEFAULT 'active', version INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_group_submission UNIQUE(owner_id,submission_id),
        CONSTRAINT ck_group_bounds CHECK(version>=1 AND char_length(name) BETWEEN 1 AND 120 AND char_length(owner_alias) BETWEEN 1 AND 100 AND char_length(description)<=2000 AND char_length(area_label)<=160),
        CONSTRAINT ck_group_creation_hash CHECK(creation_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_group_purpose CHECK(purpose IN ('community','professional','local_area','property_project')),
        CONSTRAINT ck_group_state CHECK(visibility IN ('private','network') AND join_policy IN ('approval_required','open') AND post_policy IN ('members','moderated') AND status IN ('active','archived'))
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS network_group_memberships (
        id UUID PRIMARY KEY, group_id UUID NOT NULL REFERENCES network_groups(id) ON DELETE RESTRICT,
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT, role TEXT NOT NULL DEFAULT 'member',
        state TEXT NOT NULL, member_alias TEXT, invited_by_id UUID REFERENCES users(id) ON DELETE RESTRICT,
        decided_by_id UUID REFERENCES users(id) ON DELETE RESTRICT, invite_expires_at TIMESTAMPTZ,
        version INTEGER NOT NULL DEFAULT 1, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_group_member UNIQUE(group_id,user_id),
        CONSTRAINT ck_group_member_state CHECK(version>=1 AND role IN ('admin','moderator','member') AND state IN ('requested','invited','active','declined','cancelled','left','removed','blocked')),
        CONSTRAINT ck_group_member_fields CHECK((member_alias IS NULL OR char_length(member_alias) BETWEEN 1 AND 100) AND (state<>'active' OR member_alias IS NOT NULL) AND (state<>'invited' OR invite_expires_at IS NOT NULL))
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS network_group_invite_links (
        id UUID PRIMARY KEY, group_id UUID NOT NULL REFERENCES network_groups(id) ON DELETE RESTRICT,
        created_by_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT, token_sha256 TEXT NOT NULL UNIQUE,
        expires_at TIMESTAMPTZ NOT NULL, revoked_at TIMESTAMPTZ, max_uses INTEGER NOT NULL,
        use_count INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_group_link_bounds CHECK(token_sha256 ~ '^[0-9a-f]{64}$' AND version>=1 AND max_uses BETWEEN 1 AND 1000 AND use_count BETWEEN 0 AND max_uses AND expires_at>created_at)
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS network_group_posts (
        id UUID PRIMARY KEY, group_id UUID NOT NULL REFERENCES network_groups(id) ON DELETE RESTRICT,
        author_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT, submission_id UUID NOT NULL,
        body TEXT NOT NULL, media_ids JSONB NOT NULL DEFAULT '[]', created_audience TEXT NOT NULL, status TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_group_post_submission UNIQUE(author_id,submission_id),
        CONSTRAINT ck_group_post_bounds CHECK(version>=1 AND char_length(body)<=4000 AND jsonb_typeof(media_ids)='array' AND jsonb_array_length(media_ids)<=6 AND (char_length(body)>0 OR jsonb_array_length(media_ids)>0)),
        CONSTRAINT ck_group_post_state CHECK(created_audience IN ('private','network') AND status IN ('pending','visible','hidden'))
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS network_group_comments (
        id UUID PRIMARY KEY, post_id UUID NOT NULL REFERENCES network_group_posts(id) ON DELETE RESTRICT,
        author_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT, submission_id UUID NOT NULL, body TEXT NOT NULL,
        media_id UUID, hidden BOOLEAN NOT NULL DEFAULT false, version INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_group_comment_submission UNIQUE(author_id,submission_id),
        CONSTRAINT ck_group_comment_bounds CHECK(version>=1 AND char_length(body) BETWEEN 1 AND 2000)
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS network_group_media (
        id UUID PRIMARY KEY, group_id UUID NOT NULL REFERENCES network_groups(id) ON DELETE RESTRICT,
        uploader_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT, kind TEXT NOT NULL,
        post_id UUID REFERENCES network_group_posts(id) ON DELETE RESTRICT, submission_id UUID NOT NULL,
        object_key TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL, byte_count INTEGER NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), CONSTRAINT uq_group_media_submission UNIQUE(uploader_id,submission_id),
        CONSTRAINT ck_group_media_bounds CHECK(kind IN ('group_image','post') AND (kind='post' OR post_id IS NULL) AND sha256 ~ '^[0-9a-f]{64}$' AND byte_count BETWEEN 1 AND 5242880 AND width>0 AND height>0)
    )""")
    for table, name, column in (("network_groups", "fk_group_image", "image_media_id"),
                                ("network_group_comments", "fk_group_comment_media", "media_id")):
        op.execute(f"""DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_constraint WHERE conrelid='{table}'::regclass AND conname='{name}') THEN
            ALTER TABLE {table} ADD CONSTRAINT {name} FOREIGN KEY({column}) REFERENCES network_group_media(id) ON DELETE RESTRICT;
        END IF; END $$""")
    op.execute("""CREATE TABLE IF NOT EXISTS network_group_actions (
        id UUID PRIMARY KEY, group_id UUID NOT NULL REFERENCES network_groups(id) ON DELETE RESTRICT,
        actor_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT, submission_id UUID NOT NULL,
        operation TEXT NOT NULL, target_kind TEXT NOT NULL, target_id UUID NOT NULL, creation_sha256 TEXT NOT NULL,
        membership_id UUID REFERENCES network_group_memberships(id) ON DELETE RESTRICT,
        link_id UUID REFERENCES network_group_invite_links(id) ON DELETE RESTRICT, result_versions JSONB NOT NULL,
        reason TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_group_action_submission UNIQUE(actor_id,submission_id),
        CONSTRAINT ck_group_action_fields CHECK(creation_sha256 ~ '^[0-9a-f]{64}$' AND target_kind IN ('group','membership','link','post','comment','media') AND char_length(reason)<=500),
        CONSTRAINT ck_group_action_operation CHECK(operation IN ('group_create','group_edit','archive','join','request','cancel','accept_invite','decline_invite','leave','invite','approve_member','decline_member','revoke_invite','set_role','remove','block','unblock','link_create','link_revoke','link_accept','post_create','post_approve','post_hide','comment_create','comment_hide','media_upload'))
    )""")
    for name, table, columns in (
        ("ix_group_owner", "network_groups", "owner_id,status,updated_at,id"),
        ("ix_group_discovery", "network_groups", "visibility,status,name,id"),
        ("ix_group_image", "network_groups", "image_media_id"),
        ("ix_group_member_user", "network_group_memberships", "user_id,state,updated_at,id"),
        ("ix_group_member_queue", "network_group_memberships", "group_id,state,id"),
        ("ix_group_member_inviter", "network_group_memberships", "invited_by_id"),
        ("ix_group_member_decider", "network_group_memberships", "decided_by_id"),
        ("ix_group_link_group", "network_group_invite_links", "group_id,created_at,id"),
        ("ix_group_link_creator", "network_group_invite_links", "created_by_id"),
        ("ix_group_post_feed", "network_group_posts", "group_id,status,created_at,id"),
        ("ix_group_post_author", "network_group_posts", "author_id"),
        ("ix_group_comment_post", "network_group_comments", "post_id,created_at,id"),
        ("ix_group_comment_author", "network_group_comments", "author_id"),
        ("ix_group_comment_media", "network_group_comments", "media_id"),
        ("ix_group_media_context", "network_group_media", "group_id,post_id,id"),
        ("ix_group_media_uploader", "network_group_media", "uploader_id,created_at,id"),
        ("ix_group_action_group", "network_group_actions", "group_id,created_at,id"),
        ("ix_group_action_actor", "network_group_actions", "actor_id,created_at,id"),
        ("ix_group_action_member", "network_group_actions", "membership_id"),
        ("ix_group_action_link", "network_group_actions", "link_id"),
    ):
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({columns})")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_group_link_redemption ON network_group_actions(link_id,actor_id) WHERE operation='link_accept'")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        for role in ("anon", "authenticated", "service_role"):
            op.execute(f"""DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='{role}') THEN
                REVOKE ALL ON {table} FROM {role}; END IF; END $$""")
    op.execute("""CREATE OR REPLACE FUNCTION network_group_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Group records are retained';
        END IF;
        IF TG_TABLE_NAME='network_group_actions' AND TG_OP='UPDATE' THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Group action receipts are immutable';
        ELSIF TG_TABLE_NAME='network_groups' THEN
            IF TG_OP='UPDATE' AND ((to_jsonb(NEW)-'name'-'description'-'purpose'-'area_label'-'visibility'-'join_policy'-'post_policy'-'comments_enabled'-'image_media_id'-'status'-'version'-'updated_at')
                IS DISTINCT FROM (to_jsonb(OLD)-'name'-'description'-'purpose'-'area_label'-'visibility'-'join_policy'-'post_policy'-'comments_enabled'-'image_media_id'-'status'-'version'-'updated_at') OR NEW.version<>OLD.version+1) THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Group identity is immutable and versions must advance';
            END IF;
            IF NEW.image_media_id IS NOT NULL AND NOT EXISTS(SELECT FROM network_group_media m WHERE m.id=NEW.image_media_id AND m.group_id=NEW.id AND m.kind='group_image') THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Group image context mismatch';
            END IF;
        ELSIF TG_TABLE_NAME='network_group_memberships' THEN
            IF EXISTS(SELECT FROM network_groups g WHERE g.id=NEW.group_id AND g.owner_id=NEW.user_id)
                OR (TG_OP='UPDATE' AND (NEW.id<>OLD.id OR NEW.group_id<>OLD.group_id OR NEW.user_id<>OLD.user_id
                    OR NEW.created_at<>OLD.created_at OR NEW.version<>OLD.version+1)) THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Group membership identity or version mismatch';
            END IF;
        ELSIF TG_TABLE_NAME='network_group_invite_links' AND TG_OP='UPDATE' THEN
            IF (to_jsonb(NEW)-'use_count'-'revoked_at'-'version') IS DISTINCT FROM (to_jsonb(OLD)-'use_count'-'revoked_at'-'version')
                OR NEW.use_count<OLD.use_count OR NEW.use_count>OLD.use_count+1
                OR (NEW.revoked_at IS DISTINCT FROM OLD.revoked_at AND (OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL OR NEW.version<>OLD.version+1))
                OR (NEW.revoked_at IS NOT DISTINCT FROM OLD.revoked_at AND NEW.version<>OLD.version) THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Invitation link identity is immutable';
            END IF;
        ELSIF TG_TABLE_NAME='network_group_posts' THEN
            IF TG_OP='INSERT' AND NOT EXISTS(SELECT FROM network_groups g WHERE g.id=NEW.group_id AND g.visibility=NEW.created_audience) THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Post audience must match its creation context';
            ELSIF TG_OP='UPDATE' AND ((to_jsonb(NEW)-'status'-'version') IS DISTINCT FROM (to_jsonb(OLD)-'status'-'version') OR NEW.version<>OLD.version+1) THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Group post content and audience are immutable';
            END IF;
        ELSIF TG_TABLE_NAME='network_group_comments' THEN
            IF TG_OP='UPDATE' AND ((to_jsonb(NEW)-'hidden'-'version') IS DISTINCT FROM (to_jsonb(OLD)-'hidden'-'version') OR NEW.version<>OLD.version+1) THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Group comment content is immutable';
            END IF;
            IF NEW.media_id IS NOT NULL AND NOT EXISTS(SELECT FROM network_group_media m WHERE m.id=NEW.media_id AND m.post_id=NEW.post_id) THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Group photo comment context mismatch';
            END IF;
        ELSIF TG_TABLE_NAME='network_group_media' THEN
            IF TG_OP='UPDATE' AND ((to_jsonb(NEW)-'post_id') IS DISTINCT FROM (to_jsonb(OLD)-'post_id')
                OR OLD.post_id IS NOT NULL OR NEW.post_id IS NULL) THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Group photo identity and binding are immutable';
            END IF;
            IF NEW.post_id IS NOT NULL AND NOT EXISTS(SELECT FROM network_group_posts p WHERE p.id=NEW.post_id AND p.group_id=NEW.group_id AND p.author_id=NEW.uploader_id) THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Group photo post context mismatch';
            END IF;
        END IF;
        RETURN NEW;
    END $$""")
    op.execute("REVOKE ALL ON FUNCTION network_group_guard() FROM PUBLIC")
    for role in ("anon", "authenticated", "service_role"):
        op.execute(f"""DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='{role}') THEN
            REVOKE ALL ON FUNCTION network_group_guard() FROM {role}; END IF; END $$""")
    for table in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS network_group_retention ON {table}")
        op.execute(f"CREATE TRIGGER network_group_retention BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION network_group_guard()")


def downgrade():
    op.execute("LOCK TABLE " + ",".join(TABLES) + " IN ACCESS EXCLUSIVE MODE")
    for table in TABLES:
        op.execute(f"""DO $$ BEGIN IF EXISTS(SELECT FROM {table}) THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Populated group records must be retained'; END IF; END $$""")
    op.execute("ALTER TABLE network_groups DROP CONSTRAINT fk_group_image")
    op.execute("ALTER TABLE network_group_comments DROP CONSTRAINT fk_group_comment_media")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION network_group_guard()")
