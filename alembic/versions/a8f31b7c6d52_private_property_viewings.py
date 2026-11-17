"""Store private viewing requests and retire them on listing assignment changes."""

from alembic import op


revision = "a8f31b7c6d52"
down_revision = "9f65b4e21ca0"
branch_labels = None
depends_on = None

ITINERARIES = "viewing_itineraries"
VIEWINGS = "property_viewings"
FUNCTION = "retire_property_viewings"
TRIGGER = "property_viewing_listing_changed"


def upgrade():
    # Core model bootstrap may already have created the structure on a fresh DB.
    op.execute(f"""CREATE TABLE IF NOT EXISTS {ITINERARIES} (
        id UUID PRIMARY KEY,
        buyer_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        request_id UUID NOT NULL,
        request_sha256 TEXT NOT NULL,
        title TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_viewing_itinerary_request UNIQUE (buyer_id, request_id),
        CONSTRAINT ck_viewing_itinerary_content CHECK (
            char_length(btrim(title)) BETWEEN 1 AND 100 AND request_sha256 ~ '^[0-9a-f]{{64}}$')
    )""")
    op.execute(f"""CREATE TABLE IF NOT EXISTS {VIEWINGS} (
        id UUID PRIMARY KEY,
        itinerary_id UUID NOT NULL REFERENCES {ITINERARIES}(id) ON DELETE CASCADE,
        listing_id UUID NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
        agent_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        listing_title TEXT NOT NULL,
        listing_location TEXT NOT NULL,
        agent_name TEXT NOT NULL,
        attendee_name TEXT NOT NULL,
        note TEXT NOT NULL,
        status TEXT NOT NULL,
        mode TEXT NOT NULL,
        starts_at TIMESTAMPTZ NOT NULL,
        version INTEGER NOT NULL,
        meeting_details TEXT,
        meeting_url TEXT,
        availability_checked_at TIMESTAMPTZ,
        CONSTRAINT uq_property_viewing_itinerary_listing UNIQUE (itinerary_id, listing_id),
        CONSTRAINT ck_property_viewing_status CHECK (
            version >= 1 AND status IN ('requested','proposed','confirmed','declined','cancelled','unavailable')
            AND mode IN ('in_person','virtual') AND char_length(note) <= 1000),
        CONSTRAINT ck_property_viewing_meeting CHECK (
            (status IN ('proposed','confirmed') AND availability_checked_at IS NOT NULL
             AND (meeting_details IS NULL OR char_length(meeting_details) BETWEEN 1 AND 1000)
             AND ((mode = 'virtual' AND meeting_url IS NOT NULL AND char_length(meeting_url) BETWEEN 1 AND 600)
                  OR (mode = 'in_person' AND meeting_url IS NULL AND meeting_details IS NOT NULL))) OR
            (status NOT IN ('proposed','confirmed') AND availability_checked_at IS NULL
             AND meeting_details IS NULL AND meeting_url IS NULL))
    )""")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_viewing_itinerary_buyer ON {ITINERARIES}(buyer_id, created_at, id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_property_viewing_agent ON {VIEWINGS}(agent_id, starts_at, id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_property_viewing_listing ON {VIEWINGS}(listing_id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_property_viewing_confirmed_start ON {VIEWINGS}(starts_at) "
               "WHERE status='confirmed'")

    for table in (ITINERARIES, VIEWINGS):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
    op.execute(f"""CREATE FUNCTION {FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
        BEGIN
            EXECUTE format(
                'UPDATE %I.{VIEWINGS} SET status = ''unavailable'', version = version + 1,
                    meeting_details = NULL, meeting_url = NULL, availability_checked_at = NULL
                 WHERE listing_id = $1 AND status IN (''requested'', ''proposed'', ''confirmed'')', TG_TABLE_SCHEMA)
                USING NEW.id;
            RETURN NEW;
        END;
        $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION}() FROM PUBLIC")
    for role in ("anon", "authenticated", "service_role"):
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                REVOKE ALL ON TABLE {ITINERARIES}, {VIEWINGS} FROM {role};
                REVOKE ALL ON FUNCTION {FUNCTION}() FROM {role};
            END IF;
        END $$""")
    op.execute(f"""CREATE TRIGGER {TRIGGER} AFTER UPDATE OF assigned_realtor_id, status ON listings
        FOR EACH ROW WHEN (OLD.assigned_realtor_id IS DISTINCT FROM NEW.assigned_realtor_id
                           OR (OLD.status = 'active' AND NEW.status <> 'active'))
        EXECUTE FUNCTION {FUNCTION}()""")


def downgrade():
    op.execute(f"DROP TRIGGER {TRIGGER} ON listings")
    op.execute(f"DROP FUNCTION {FUNCTION}()")
    op.execute(f"DROP TABLE {VIEWINGS}, {ITINERARIES}")
