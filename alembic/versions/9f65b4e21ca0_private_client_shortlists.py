"""Store deliberate private shortlists and revoke links on assignment changes."""

from alembic import op


revision = "9f65b4e21ca0"
down_revision = "87e9c4a1d620"
branch_labels = None
depends_on = None

DOCUMENTS = "client_shortlists"
ITEMS = "client_shortlist_items"
GRANTS = "client_shortlist_grants"
FUNCTION = "invalidate_client_shortlist_assignment"
TRIGGER = "client_shortlist_assignment_changed"


def upgrade():
    # Bootstrap may already have created the registered tables on an empty DB.
    op.execute(f"""CREATE TABLE IF NOT EXISTS {DOCUMENTS} (
        id UUID PRIMARY KEY,
        owner_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        buyer_id UUID REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        shared_notes TEXT NOT NULL,
        shared_preferences TEXT NOT NULL,
        version INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        archived_at TIMESTAMPTZ,
        CONSTRAINT ck_client_shortlist_content CHECK (
            version >= 1 AND char_length(btrim(title)) BETWEEN 1 AND 100
            AND char_length(shared_notes) <= 2000 AND char_length(shared_preferences) <= 2000)
    )""")
    op.execute(f"""CREATE TABLE IF NOT EXISTS {ITEMS} (
        shortlist_id UUID NOT NULL REFERENCES {DOCUMENTS}(id) ON DELETE CASCADE,
        listing_id UUID NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        PRIMARY KEY (shortlist_id, listing_id),
        CONSTRAINT ck_client_shortlist_item_position CHECK (position BETWEEN 0 AND 19),
        CONSTRAINT uq_client_shortlist_item_position UNIQUE (shortlist_id, position)
    )""")
    op.execute(f"""CREATE TABLE IF NOT EXISTS {GRANTS} (
        id UUID PRIMARY KEY,
        shortlist_id UUID NOT NULL REFERENCES {DOCUMENTS}(id) ON DELETE CASCADE,
        buyer_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        realtor_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        recipient_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        recipient_user_code INTEGER NOT NULL,
        generation INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        revoked_at TIMESTAMPTZ,
        invalidated_at TIMESTAMPTZ,
        CONSTRAINT ck_client_shortlist_grant_scope CHECK (
            buyer_id <> realtor_id AND recipient_id IN (buyer_id, realtor_id)
            AND recipient_user_code > 0 AND generation >= 1),
        CONSTRAINT ck_client_shortlist_grant_interval CHECK (
            expires_at-created_at IN (interval '168 hours',interval '720 hours',interval '2160 hours')),
        CONSTRAINT uq_client_shortlist_grant_generation UNIQUE (shortlist_id, generation)
    )""")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_client_shortlist_owner ON {DOCUMENTS}"
               "(owner_id, updated_at, id) WHERE archived_at IS NULL")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_client_shortlist_buyer ON {DOCUMENTS}(buyer_id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_client_shortlist_item_listing ON {ITEMS}(listing_id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_client_shortlist_grant_recipient ON {GRANTS}(recipient_id, expires_at)")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_client_shortlist_grant_realtor ON {GRANTS}(realtor_id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_client_shortlist_grant_buyer ON {GRANTS}(buyer_id, realtor_id)")

    for table in (DOCUMENTS, ITEMS, GRANTS):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
    op.execute(f"""CREATE FUNCTION {FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
        BEGIN
            EXECUTE format(
                'UPDATE %I.{GRANTS} SET invalidated_at = statement_timestamp()
                 WHERE buyer_id = $1 AND realtor_id = $2 AND revoked_at IS NULL
                 AND invalidated_at IS NULL', TG_TABLE_SCHEMA)
                USING OLD.id, OLD.assigned_realtor_id;
            RETURN NEW;
        END;
        $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION}() FROM PUBLIC")
    for role in ("anon", "authenticated", "service_role"):
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                REVOKE ALL ON TABLE {DOCUMENTS}, {ITEMS}, {GRANTS} FROM {role};
                REVOKE ALL ON FUNCTION {FUNCTION}() FROM {role};
            END IF;
        END $$""")
    op.execute(f"""CREATE TRIGGER {TRIGGER} AFTER UPDATE OF assigned_realtor_id ON users
        FOR EACH ROW WHEN (OLD.assigned_realtor_id IS DISTINCT FROM NEW.assigned_realtor_id)
        EXECUTE FUNCTION {FUNCTION}()""")


def downgrade():
    op.execute(f"DROP TRIGGER {TRIGGER} ON users")
    op.execute(f"DROP FUNCTION {FUNCTION}()")
    op.execute(f"DROP TABLE {GRANTS}, {ITEMS}, {DOCUMENTS}")
