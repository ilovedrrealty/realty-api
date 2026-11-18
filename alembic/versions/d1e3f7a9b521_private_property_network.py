"""Private projects, retained service history and opted-in member walls.

No rows are copied, no profiles are published, and no bucket/provider is created.
"""
from alembic import op

revision = "d1e3f7a9b521"
down_revision = "c90e75f2a6d1"
branch_labels = None
depends_on = None

TABLES = (
    "network_projects", "network_project_members", "network_milestones", "network_project_updates",
    "network_project_comments", "social_network_profiles", "network_posts", "network_post_comments",
    "network_blocks", "network_media", "network_audit",
)


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS network_projects (
        id UUID PRIMARY KEY, listing_id UUID NOT NULL REFERENCES listings(id) ON DELETE RESTRICT,
        owner_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        title TEXT NOT NULL, summary TEXT NOT NULL, property_class TEXT NOT NULL,
        service_category_ids JSONB NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_network_project_version CHECK(version>=1),
        CONSTRAINT ck_network_project_status CHECK(status IN ('open','in_progress','completed','archived'))
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_network_project_listing ON network_projects(listing_id,created_at)")
    op.execute("""CREATE TABLE IF NOT EXISTS network_project_members (
        id UUID PRIMARY KEY, project_id UUID NOT NULL REFERENCES network_projects(id) ON DELETE RESTRICT,
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT, role TEXT NOT NULL, active BOOLEAN NOT NULL,
        CONSTRAINT uq_network_project_member UNIQUE(project_id,user_id),
        CONSTRAINT ck_network_member_role CHECK(role IN ('manager','professional','viewer'))
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_network_member_user ON network_project_members(user_id,active)")
    op.execute("""CREATE TABLE IF NOT EXISTS network_milestones (
        id UUID PRIMARY KEY, project_id UUID NOT NULL REFERENCES network_projects(id) ON DELETE RESTRICT,
        title TEXT NOT NULL, due_date DATE, status TEXT NOT NULL, version INTEGER NOT NULL,
        CONSTRAINT ck_network_milestone_version CHECK(version>=1),
        CONSTRAINT ck_network_milestone_status CHECK(status IN ('open','in_progress','completed','cancelled'))
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_network_milestones_project_id ON network_milestones(project_id)")
    op.execute("""CREATE TABLE IF NOT EXISTS network_project_updates (
        id UUID PRIMARY KEY, project_id UUID NOT NULL REFERENCES network_projects(id) ON DELETE RESTRICT,
        author_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT, submission_id UUID NOT NULL,
        kind TEXT NOT NULL, body TEXT NOT NULL, occurred_on DATE NOT NULL,
        correction_of UUID REFERENCES network_project_updates(id) ON DELETE RESTRICT,
        media_ids JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_network_update_submission UNIQUE(author_id,submission_id),
        CONSTRAINT ck_network_update_kind CHECK(kind IN ('progress','correction','milestone'))
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_network_update_project_date ON network_project_updates(project_id,created_at,id)")
    op.execute("""CREATE TABLE IF NOT EXISTS network_project_comments (
        id UUID PRIMARY KEY, update_id UUID NOT NULL REFERENCES network_project_updates(id) ON DELETE RESTRICT,
        author_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        submission_id UUID NOT NULL, body TEXT NOT NULL, media_id UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_network_project_comment_submission UNIQUE(author_id,submission_id)
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_network_project_comments_update_id ON network_project_comments(update_id)")
    op.execute("""CREATE TABLE IF NOT EXISTS social_network_profiles (
        user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT,
        display_name TEXT NOT NULL, bio TEXT NOT NULL, status_text TEXT NOT NULL,
        visibility TEXT NOT NULL, wall_policy TEXT NOT NULL, comments_enabled BOOLEAN NOT NULL, version INTEGER NOT NULL,
        CONSTRAINT ck_network_profile_visibility CHECK(visibility IN ('private','members')),
        CONSTRAINT ck_network_wall_policy CHECK(wall_policy IN ('own_only','approval_required')),
        CONSTRAINT ck_network_profile_version CHECK(version>=1)
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS network_posts (
        id UUID PRIMARY KEY, profile_user_id UUID NOT NULL REFERENCES social_network_profiles(user_id) ON DELETE RESTRICT,
        author_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT, submission_id UUID NOT NULL,
        body TEXT NOT NULL, media_ids JSONB NOT NULL, status TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_network_post_submission UNIQUE(author_id,submission_id),
        CONSTRAINT ck_network_post_status CHECK(status IN ('pending','visible','hidden'))
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_network_post_wall_date ON network_posts(profile_user_id,created_at,id)")
    op.execute("""CREATE TABLE IF NOT EXISTS network_post_comments (
        id UUID PRIMARY KEY, post_id UUID NOT NULL REFERENCES network_posts(id) ON DELETE RESTRICT,
        author_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT, submission_id UUID NOT NULL,
        body TEXT NOT NULL, media_id UUID, hidden BOOLEAN NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_network_post_comment_submission UNIQUE(author_id,submission_id)
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_network_post_comments_post_id ON network_post_comments(post_id)")
    op.execute("""CREATE TABLE IF NOT EXISTS network_blocks (
        blocker_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        blocked_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        PRIMARY KEY(blocker_id,blocked_id), CONSTRAINT ck_network_no_self_block CHECK(blocker_id<>blocked_id)
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS network_media (
        id UUID PRIMARY KEY, uploader_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        project_id UUID REFERENCES network_projects(id) ON DELETE RESTRICT,
        update_id UUID REFERENCES network_project_updates(id) ON DELETE RESTRICT,
        post_id UUID REFERENCES network_posts(id) ON DELETE RESTRICT,
        object_key TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL, byte_count INTEGER NOT NULL,
        width INTEGER NOT NULL, height INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_network_media_one_binding CHECK(NOT(update_id IS NOT NULL AND post_id IS NOT NULL)),
        CONSTRAINT ck_network_media_size CHECK(byte_count>0 AND byte_count<=5242880),
        CONSTRAINT ck_network_media_dimensions CHECK(width>0 AND height>0)
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_network_media_project_id ON network_media(project_id)")
    for table in ("network_project_comments", "network_post_comments"):
        op.execute(f"""DO $$ BEGIN
            IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='{table}'::regclass AND conname='{table}_media_id_fkey') THEN
                ALTER TABLE {table} ADD CONSTRAINT {table}_media_id_fkey FOREIGN KEY(media_id) REFERENCES network_media(id) ON DELETE RESTRICT;
            END IF;
        END $$""")
    op.execute("""CREATE TABLE IF NOT EXISTS network_audit (
        id UUID PRIMARY KEY, actor_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        target_kind TEXT NOT NULL, target_id UUID NOT NULL, action TEXT NOT NULL, data JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"""DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON {table} FROM anon; END IF;
            IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN REVOKE ALL ON {table} FROM authenticated; END IF;
        END $$""")
    # These normal invoker triggers also protect against accidental application
    # SQL updates/deletes. They are not a claim against a privileged DB operator.
    op.execute("""CREATE OR REPLACE FUNCTION network_reject_history_change() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Network history is append-only';
        END $$""")
    for table in ("network_project_updates", "network_project_comments", "network_audit"):
        op.execute(f"DROP TRIGGER IF EXISTS network_append_only ON {table}")
        op.execute(f"CREATE TRIGGER network_append_only BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION network_reject_history_change()")
    op.execute("""CREATE OR REPLACE FUNCTION network_guard_content() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Network content is retained';
        END IF;
        IF TG_TABLE_NAME='network_posts' AND (to_jsonb(NEW)-'status') IS DISTINCT FROM (to_jsonb(OLD)-'status') THEN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Post content is immutable';
        ELSIF TG_TABLE_NAME='network_post_comments' AND (to_jsonb(NEW)-'hidden') IS DISTINCT FROM (to_jsonb(OLD)-'hidden') THEN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Comment content is immutable';
        ELSIF TG_TABLE_NAME='network_media' THEN
            IF (to_jsonb(NEW)-'post_id'-'update_id') IS DISTINCT FROM (to_jsonb(OLD)-'post_id'-'update_id')
                OR ((OLD.post_id IS NOT NULL OR OLD.update_id IS NOT NULL)
                    AND (NEW.post_id IS DISTINCT FROM OLD.post_id OR NEW.update_id IS DISTINCT FROM OLD.update_id)) THEN
                RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Photo identity and binding are immutable';
            END IF;
            IF NEW.update_id IS NOT NULL AND NOT EXISTS(
                SELECT 1 FROM network_project_updates u WHERE u.id=NEW.update_id AND u.project_id=NEW.project_id AND u.author_id=NEW.uploader_id
            ) THEN RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Photo update context mismatch'; END IF;
            IF NEW.post_id IS NOT NULL AND (NEW.project_id IS NOT NULL OR NOT EXISTS(
                SELECT 1 FROM network_posts p WHERE p.id=NEW.post_id AND p.author_id=NEW.uploader_id
            )) THEN RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Photo wall context mismatch'; END IF;
        END IF;
        RETURN NEW;
        END $$""")
    for table in ("network_posts", "network_post_comments", "network_media"):
        op.execute(f"DROP TRIGGER IF EXISTS network_content_guard ON {table}")
        op.execute(f"CREATE TRIGGER network_content_guard BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION network_guard_content()")


def downgrade():
    # Retain project evidence, comments and media identities across code rollback.
    pass
