"""Version and publish only source-checked development inventories."""

from alembic import op


revision = "fad71e03c6b9"
down_revision = "b35e60f12da4"
branch_labels = None
depends_on = None

PROJECTS = "development_inventories"
REVISIONS = "development_inventory_revisions"
EVENTS = "development_inventory_events"
IMMUTABLE = "keep_development_history"


def upgrade():
    # Empty application bootstrap may already have registered ORM tables.
    op.execute(f"""CREATE TABLE IF NOT EXISTS {PROJECTS} (
        listing_id UUID PRIMARY KEY REFERENCES listings(id) ON DELETE RESTRICT,
        version INTEGER NOT NULL,
        draft_revision INTEGER NOT NULL,
        reviewed_revision INTEGER,
        published_revision INTEGER,
        published_at TIMESTAMPTZ,
        CONSTRAINT ck_development_state CHECK (
            draft_revision >= 1 AND version >= draft_revision
            AND (reviewed_revision IS NULL OR reviewed_revision = draft_revision)
            AND (published_revision IS NULL OR published_revision BETWEEN 1 AND draft_revision)
            AND (published_at IS NULL) = (published_revision IS NULL))
    )""")
    op.execute(f"""CREATE TABLE IF NOT EXISTS {REVISIONS} (
        listing_id UUID REFERENCES {PROJECTS}(listing_id) NOT NULL,
        revision INTEGER NOT NULL,
        snapshot JSONB NOT NULL,
        saved_at TIMESTAMPTZ NOT NULL,
        saved_by UUID REFERENCES users(id) ON DELETE SET NULL,
        PRIMARY KEY (listing_id, revision),
        CONSTRAINT ck_development_snapshot CHECK (
            revision >= 1 AND jsonb_typeof(snapshot) = 'object'
            AND snapshot ?& ARRAY['project', 'evidence']
            AND jsonb_typeof(snapshot->'project') = 'object'
            AND jsonb_typeof(snapshot->'evidence') = 'object')
    )""")
    op.execute(f"""CREATE TABLE IF NOT EXISTS {EVENTS} (
        listing_id UUID NOT NULL,
        version INTEGER NOT NULL,
        revision INTEGER NOT NULL,
        action TEXT NOT NULL,
        actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (listing_id, version),
        CONSTRAINT fk_development_event_revision
            FOREIGN KEY (listing_id, revision) REFERENCES {REVISIONS}(listing_id, revision),
        CONSTRAINT ck_development_event CHECK (version >= revision AND revision >= 1
            AND action IN ('draft','review','publish','withdraw'))
    )""")
    for pointer in ("draft", "reviewed", "published"):
        name = f"fk_development_{pointer}"
        op.execute(f"""DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_constraint WHERE conrelid = '{PROJECTS}'::regclass AND conname = '{name}') THEN
                ALTER TABLE {PROJECTS} ADD CONSTRAINT {name} FOREIGN KEY (listing_id, {pointer}_revision)
                    REFERENCES {REVISIONS}(listing_id, revision) DEFERRABLE INITIALLY DEFERRED;
            END IF;
        END $$""")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_development_saved_by ON {REVISIONS}(saved_by)")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_development_actor ON {EVENTS}(actor_id)")

    op.execute(f"""CREATE FUNCTION {IMMUTABLE}() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF (to_jsonb(OLD) ->> TG_ARGV[0]) IS NOT NULL AND (to_jsonb(NEW) ->> TG_ARGV[0]) IS NULL
                    AND (to_jsonb(NEW) - TG_ARGV[0]) = (to_jsonb(OLD) - TG_ARGV[0]) THEN
                    RETURN NEW;
                END IF;
            END IF;
            RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'Development history is append-only';
        END;
        $$""")
    for table, field in ((REVISIONS, "saved_by"), (EVENTS, "actor_id")):
        op.execute(f"""CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION {IMMUTABLE}('{field}')""")

    for table in (PROJECTS, REVISIONS, EVENTS):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON FUNCTION {IMMUTABLE}() FROM PUBLIC")
    for role in ("anon", "authenticated", "service_role"):
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                REVOKE ALL ON TABLE {PROJECTS}, {REVISIONS}, {EVENTS} FROM {role};
                REVOKE ALL ON FUNCTION {IMMUTABLE}() FROM {role};
            END IF;
        END $$""")


def downgrade():
    for table in (EVENTS, REVISIONS):
        op.execute(f"DROP TRIGGER {table}_append_only ON {table}")
    op.execute(f"DROP FUNCTION {IMMUTABLE}()")
    for pointer in ("draft", "reviewed", "published"):
        op.execute(f"ALTER TABLE {PROJECTS} DROP CONSTRAINT fk_development_{pointer}")
    op.execute(f"DROP TABLE {EVENTS}, {REVISIONS}, {PROJECTS}")
