"""Store bilingual drafts and reviewed published revisions independently."""

from alembic import op


revision = "b35e60f12da4"
down_revision = "a8f31b7c6d52"
branch_labels = None
depends_on = None

PAGES = "editorial_pages"
REVISIONS = "editorial_revisions"
EVENTS = "editorial_events"
IMMUTABLE = "keep_editorial_history"


def upgrade():
    # Registered models can already exist after the app's empty DB bootstrap.
    op.execute(f"""CREATE TABLE IF NOT EXISTS {PAGES} (
        id UUID PRIMARY KEY,
        slug TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL,
        version INTEGER NOT NULL,
        draft_revision INTEGER NOT NULL,
        reviewed_revision INTEGER,
        published_revision INTEGER,
        published_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT ck_editorial_page_identity CHECK (
            slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND char_length(slug) <= 80
            AND kind IN ('article','guide','neighborhood')),
        CONSTRAINT ck_editorial_page_state CHECK (
            draft_revision >= 1 AND version >= draft_revision
            AND (reviewed_revision IS NULL OR reviewed_revision = draft_revision)
            AND (published_revision IS NULL OR published_revision BETWEEN 1 AND draft_revision)
            AND (published_at IS NULL) = (published_revision IS NULL))
    )""")
    op.execute(f"""CREATE TABLE IF NOT EXISTS {REVISIONS} (
        page_id UUID NOT NULL REFERENCES {PAGES}(id),
        revision INTEGER NOT NULL,
        content JSONB NOT NULL,
        sources JSONB NOT NULL,
        review_note TEXT NOT NULL,
        saved_at TIMESTAMPTZ NOT NULL,
        saved_by UUID REFERENCES users(id) ON DELETE SET NULL,
        restored_from INTEGER,
        PRIMARY KEY (page_id, revision),
        CONSTRAINT fk_editorial_restored_from
            FOREIGN KEY (page_id, restored_from) REFERENCES {REVISIONS}(page_id, revision),
        CONSTRAINT ck_editorial_revision_content CHECK (
            revision >= 1 AND jsonb_typeof(content) = 'object' AND jsonb_typeof(sources) = 'array'
            AND jsonb_array_length(sources) <= 8 AND char_length(review_note) <= 2000
            AND (restored_from IS NULL OR restored_from < revision))
    )""")
    op.execute(f"""CREATE TABLE IF NOT EXISTS {EVENTS} (
        page_id UUID NOT NULL,
        version INTEGER NOT NULL,
        revision INTEGER NOT NULL,
        action TEXT NOT NULL,
        actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (page_id, version),
        CONSTRAINT fk_editorial_event_revision
            FOREIGN KEY (page_id, revision) REFERENCES {REVISIONS}(page_id, revision),
        CONSTRAINT ck_editorial_event_action CHECK (version >= revision AND revision >= 1
            AND action IN ('create','draft','review','publish','unpublish','restore'))
    )""")
    for pointer in ("draft", "reviewed", "published"):
        name = f"fk_editorial_{pointer}"
        op.execute(f"""DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_constraint WHERE conrelid = '{PAGES}'::regclass AND conname = '{name}') THEN
                ALTER TABLE {PAGES} ADD CONSTRAINT {name}
                    FOREIGN KEY (id, {pointer}_revision) REFERENCES {REVISIONS}(page_id, revision)
                    DEFERRABLE INITIALLY DEFERRED;
            END IF;
        END $$""")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_editorial_page_updated ON {PAGES}(updated_at, id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_editorial_page_published ON {PAGES}(published_at, slug) "
               "WHERE published_revision IS NOT NULL")

    op.execute(f"""CREATE FUNCTION {IMMUTABLE}() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF (to_jsonb(OLD) ->> TG_ARGV[0]) IS NOT NULL AND (to_jsonb(NEW) ->> TG_ARGV[0]) IS NULL
                    AND (to_jsonb(NEW) - TG_ARGV[0]) = (to_jsonb(OLD) - TG_ARGV[0]) THEN
                    RETURN NEW;
                END IF;
            END IF;
            RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'Editorial history is append-only';
        END;
        $$""")
    # Clearing an actor FK may remove attribution, never the recorded history.
    for table, field in ((REVISIONS, "saved_by"), (EVENTS, "actor_id")):
        op.execute(f"""CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION {IMMUTABLE}('{field}')""")

    for table in (PAGES, REVISIONS, EVENTS):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON FUNCTION {IMMUTABLE}() FROM PUBLIC")
    for role in ("anon", "authenticated", "service_role"):
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                REVOKE ALL ON TABLE {PAGES}, {REVISIONS}, {EVENTS} FROM {role};
                REVOKE ALL ON FUNCTION {IMMUTABLE}() FROM {role};
            END IF;
        END $$""")


def downgrade():
    op.execute(f"DROP TRIGGER {EVENTS}_append_only ON {EVENTS}")
    op.execute(f"DROP TRIGGER {REVISIONS}_append_only ON {REVISIONS}")
    op.execute(f"DROP FUNCTION {IMMUTABLE}()")
    for pointer in ("draft", "reviewed", "published"):
        op.execute(f"ALTER TABLE {PAGES} DROP CONSTRAINT fk_editorial_{pointer}")
    op.execute(f"DROP TABLE {EVENTS}, {REVISIONS}, {PAGES}")
