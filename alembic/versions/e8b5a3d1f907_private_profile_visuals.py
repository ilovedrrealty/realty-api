"""Deliberately chosen private profile photos; no account-photo backfill or grants."""
from alembic import op

revision = "e8b5a3d1f907"
down_revision = "d8f4c2a91e67"
branch_labels = None
depends_on = None


def upgrade():
    # Append order matches the model so forward/fresh native projections agree.
    op.execute("ALTER TABLE social_network_profiles ADD COLUMN IF NOT EXISTS avatar_media_id UUID REFERENCES network_media(id) ON DELETE RESTRICT")
    op.execute("ALTER TABLE social_network_profiles ADD COLUMN IF NOT EXISTS cover_media_id UUID REFERENCES network_media(id) ON DELETE RESTRICT")
    op.execute("ALTER TABLE network_media ADD COLUMN IF NOT EXISTS profile_user_id UUID REFERENCES users(id) ON DELETE RESTRICT")
    op.execute("ALTER TABLE network_media ADD COLUMN IF NOT EXISTS profile_kind TEXT")
    for table, name, definition in (
        ("social_network_profiles", "ck_network_profile_visuals_distinct",
         "avatar_media_id IS NULL OR cover_media_id IS NULL OR avatar_media_id <> cover_media_id"),
        ("network_media", "ck_network_media_profile_scope",
         "(profile_user_id IS NULL AND profile_kind IS NULL) OR "
         "(profile_user_id IS NOT NULL AND profile_kind IS NOT NULL AND "
         "profile_user_id = uploader_id AND profile_kind IN ('avatar','cover') AND "
         "project_id IS NULL AND update_id IS NULL AND post_id IS NULL)"),
    ):
        op.execute(f"""DO $$ BEGIN
            IF NOT EXISTS(SELECT FROM pg_constraint WHERE conrelid='{table}'::regclass AND conname='{name}') THEN
                ALTER TABLE {table} ADD CONSTRAINT {name} CHECK({definition});
            END IF;
        END $$""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_network_media_profile_user ON network_media(profile_user_id,profile_kind)")
    # Existing network_guard_content compares every identity field except its
    # original post/update attachment slots, so these two new fields are immutable.
    op.execute("""CREATE OR REPLACE FUNCTION network_guard_profile_visuals() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.avatar_media_id IS NOT NULL AND NOT EXISTS(
            SELECT FROM network_media m WHERE m.id=NEW.avatar_media_id AND m.uploader_id=NEW.user_id
                AND m.profile_user_id=NEW.user_id AND m.profile_kind='avatar'
                AND m.project_id IS NULL AND m.update_id IS NULL AND m.post_id IS NULL
        ) THEN RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Profile avatar context mismatch'; END IF;
        IF NEW.cover_media_id IS NOT NULL AND NOT EXISTS(
            SELECT FROM network_media m WHERE m.id=NEW.cover_media_id AND m.uploader_id=NEW.user_id
                AND m.profile_user_id=NEW.user_id AND m.profile_kind='cover'
                AND m.project_id IS NULL AND m.update_id IS NULL AND m.post_id IS NULL
        ) THEN RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Profile cover context mismatch'; END IF;
        RETURN NEW;
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION network_guard_profile_visuals() FROM PUBLIC")
    for role in ("anon", "authenticated", "service_role"):
        op.execute(f"DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='{role}') THEN REVOKE ALL ON FUNCTION network_guard_profile_visuals() FROM {role}; END IF; END $$")
    op.execute("DROP TRIGGER IF EXISTS network_profile_visual_guard ON social_network_profiles")
    op.execute("CREATE TRIGGER network_profile_visual_guard BEFORE INSERT OR UPDATE ON social_network_profiles FOR EACH ROW EXECUTE FUNCTION network_guard_profile_visuals()")


def downgrade():
    # Code rollback keeps the new columns/objects. An empty-only schema downgrade
    # is explicit, and must never discard selected or unselected private photos.
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT FROM social_network_profiles WHERE avatar_media_id IS NOT NULL OR cover_media_id IS NOT NULL)
            OR EXISTS(SELECT FROM network_media WHERE profile_user_id IS NOT NULL OR profile_kind IS NOT NULL) THEN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Private profile visuals require retained schema';
        END IF;
    END $$""")
    op.execute("DROP TRIGGER IF EXISTS network_profile_visual_guard ON social_network_profiles")
    op.execute("DROP FUNCTION IF EXISTS network_guard_profile_visuals()")
    op.execute("ALTER TABLE social_network_profiles DROP CONSTRAINT IF EXISTS ck_network_profile_visuals_distinct")
    op.execute("ALTER TABLE social_network_profiles DROP COLUMN IF EXISTS cover_media_id, DROP COLUMN IF EXISTS avatar_media_id")
    op.execute("DROP INDEX IF EXISTS ix_network_media_profile_user")
    op.execute("ALTER TABLE network_media DROP CONSTRAINT IF EXISTS ck_network_media_profile_scope")
    op.execute("ALTER TABLE network_media DROP COLUMN IF EXISTS profile_kind, DROP COLUMN IF EXISTS profile_user_id")
