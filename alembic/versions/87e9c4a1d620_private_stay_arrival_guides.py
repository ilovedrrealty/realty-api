"""Store private per-rental arrival guides and revoke them on custody changes."""

from alembic import op


revision = "87e9c4a1d620"
down_revision = "f4b27d6e913c"
branch_labels = None
depends_on = None

TABLE = "listing_arrival_guides"
FUNCTION = "invalidate_listing_arrival_guide"
TRIGGER = "listing_arrival_guide_owner_changed"


def upgrade():
    # Fresh installations already create registered models before running Alembic.
    op.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
        listing_id UUID PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
        owner_id UUID REFERENCES users(id),
        version BIGINT NOT NULL,
        content JSONB,
        CONSTRAINT ck_listing_arrival_guide_version CHECK (version >= 1),
        CONSTRAINT ck_listing_arrival_guide_content CHECK (
            content IS NULL OR (owner_id IS NOT NULL AND jsonb_typeof(content) = 'object'))
    )""")
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON TABLE {TABLE} FROM PUBLIC")

    # The transfer creates or advances a tombstone in the listing's own
    # schema, preserving the version even if the former owner later returns.
    op.execute(f"""CREATE FUNCTION {FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
        BEGIN
            EXECUTE format(
                'INSERT INTO %I.{TABLE} AS guide (listing_id, owner_id, version)
                 VALUES ($1, $2, 1)
                 ON CONFLICT (listing_id) DO UPDATE SET owner_id = EXCLUDED.owner_id,
                     version = guide.version + 1, content = NULL', TG_TABLE_SCHEMA)
                USING NEW.id, NEW.owner_id;
            RETURN NEW;
        END;
        $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION}() FROM PUBLIC")
    for role in ("anon", "authenticated", "service_role"):
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                REVOKE ALL ON TABLE {TABLE} FROM {role};
                REVOKE ALL ON FUNCTION {FUNCTION}() FROM {role};
            END IF;
        END $$""")
    op.execute(f"""CREATE TRIGGER {TRIGGER} AFTER UPDATE OF owner_id ON listings
        FOR EACH ROW WHEN (OLD.owner_id IS DISTINCT FROM NEW.owner_id)
        EXECUTE FUNCTION {FUNCTION}()""")


def downgrade():
    op.execute(f"DROP TRIGGER {TRIGGER} ON listings")
    op.execute(f"DROP FUNCTION {FUNCTION}()")
    op.execute(f"DROP TABLE {TABLE}")
