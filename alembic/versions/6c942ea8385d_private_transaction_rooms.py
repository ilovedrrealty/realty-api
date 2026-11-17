"""Private transaction rooms, membership, tasks, milestones and audit history."""

from alembic import op


revision = "6c942ea8385d"
down_revision = "5a4e7f921bc0"
branch_labels = None
depends_on = None

TABLES = ("transaction_rooms", "transaction_room_grants", "transaction_room_tasks",
          "transaction_room_milestones", "transaction_room_events")


def upgrade():
    # Fresh bootstrap can create the identical ORM tables before this migration.
    op.execute("""CREATE TABLE IF NOT EXISTS transaction_rooms (
        id UUID PRIMARY KEY,
        lead_id UUID NOT NULL UNIQUE REFERENCES leads(id) ON DELETE RESTRICT,
        listing_id UUID NOT NULL REFERENCES listings(id) ON DELETE RESTRICT,
        buyer_id UUID REFERENCES users(id) ON DELETE SET NULL,
        title TEXT NOT NULL,
        status TEXT NOT NULL,
        version INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT ck_transaction_room_state CHECK (
            version >= 1 AND status IN ('open','closed','retired') AND char_length(btrim(title)) BETWEEN 1 AND 120)
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS transaction_room_grants (
        id UUID PRIMARY KEY,
        room_id UUID NOT NULL REFERENCES transaction_rooms(id) ON DELETE CASCADE,
        recipient_id UUID REFERENCES users(id) ON DELETE SET NULL,
        state TEXT NOT NULL,
        invited_at TIMESTAMPTZ NOT NULL,
        accepted_at TIMESTAMPTZ,
        revoked_at TIMESTAMPTZ,
        CONSTRAINT ck_transaction_grant_state CHECK (
            (state = 'pending' AND accepted_at IS NULL AND revoked_at IS NULL)
            OR (state = 'active' AND accepted_at IS NOT NULL AND revoked_at IS NULL)
            OR (state = 'revoked' AND revoked_at IS NOT NULL))
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS transaction_room_tasks (
        id UUID PRIMARY KEY,
        room_id UUID NOT NULL REFERENCES transaction_rooms(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        assignee_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
        due_on DATE,
        status TEXT NOT NULL,
        version INTEGER NOT NULL,
        CONSTRAINT ck_transaction_task_state CHECK (
            version >= 1 AND status IN ('open','in_progress','done','cancelled')
            AND char_length(btrim(title)) BETWEEN 1 AND 160)
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS transaction_room_milestones (
        id UUID PRIMARY KEY,
        room_id UUID NOT NULL REFERENCES transaction_rooms(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        target_on DATE,
        actual_on DATE,
        version INTEGER NOT NULL,
        CONSTRAINT ck_transaction_milestone_state CHECK (
            version >= 1 AND char_length(btrim(title)) BETWEEN 1 AND 160)
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS transaction_room_events (
        room_id UUID NOT NULL REFERENCES transaction_rooms(id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL,
        action TEXT NOT NULL,
        actor_role TEXT NOT NULL,
        actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
        target_id UUID,
        created_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (room_id, sequence),
        CONSTRAINT ck_transaction_event CHECK (sequence >= 1 AND action ~ '^[a-z][a-z_]{1,60}$'
            AND actor_role IN ('admin','realtor','buyer','professional','system'))
    )""")
    for name, table, columns in (
        ("ix_transaction_room_buyer", "transaction_rooms", "buyer_id"),
        ("ix_transaction_room_listing", "transaction_rooms", "listing_id"),
        ("ix_transaction_grant_recipient", "transaction_room_grants", "recipient_id, state, invited_at"),
        ("ix_transaction_grant_room", "transaction_room_grants", "room_id"),
        ("ix_transaction_task_room", "transaction_room_tasks", "room_id"),
        ("ix_transaction_task_assignee", "transaction_room_tasks", "assignee_user_id"),
        ("ix_transaction_milestone_room", "transaction_room_milestones", "room_id"),
        ("ix_transaction_event_actor", "transaction_room_events", "actor_id"),
    ):
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})")
    op.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_transaction_current_grant
        ON transaction_room_grants (room_id, recipient_id) WHERE state IN ('pending','active')""")

    # Reassignment revokes any old or newly assigned Realtor's grant so it
    # cannot later restore their access as a professional. A buyer/listing
    # rebind retires the whole room and can never reopen an earlier case.
    op.execute("""CREATE FUNCTION transaction_room_lead_changed() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
        DECLARE
            room_id uuid;
            room_version integer;
            retire boolean := OLD.from_user_id IS DISTINCT FROM NEW.from_user_id
                OR OLD.property_id IS DISTINCT FROM NEW.property_id OR OLD.type IS DISTINCT FROM NEW.type;
        BEGIN
            EXECUTE format(
                'UPDATE %I.transaction_rooms
                    SET status = CASE WHEN $2 THEN ''retired'' ELSE status END, version = version + 1
                  WHERE lead_id = $1 AND status <> ''retired'' RETURNING id, version', TG_TABLE_SCHEMA)
                INTO room_id, room_version USING NEW.id, retire;
            IF room_id IS NULL THEN
                RETURN NEW;
            END IF;
            EXECUTE format(
                'UPDATE %I.transaction_room_grants SET state = ''revoked'', revoked_at = transaction_timestamp()
                  WHERE room_id = $1 AND state IN (''pending'', ''active'')
                    AND ($2 OR recipient_id IN ($3, $4))', TG_TABLE_SCHEMA)
                USING room_id, retire, OLD.assigned_realtor_id, NEW.assigned_realtor_id;
            IF NOT retire AND OLD.assigned_realtor_id IS NOT NULL THEN
                EXECUTE format(
                    'UPDATE %I.transaction_room_tasks SET assignee_user_id = NULL, version = version + 1
                      WHERE room_id = $1 AND assignee_user_id = $2', TG_TABLE_SCHEMA)
                    USING room_id, OLD.assigned_realtor_id;
            END IF;
            EXECUTE format(
                'INSERT INTO %I.transaction_room_events (room_id,sequence,action,actor_role,created_at)
                    VALUES ($1,$2,$3,''system'',transaction_timestamp())', TG_TABLE_SCHEMA)
                USING room_id, room_version, CASE WHEN retire THEN 'room_retired' ELSE 'assignment_changed' END;
            RETURN NEW;
        END;
        $$""")
    op.execute("""CREATE TRIGGER transaction_room_lead_changed
        AFTER UPDATE OF from_user_id, property_id, assigned_realtor_id, type ON leads
        FOR EACH ROW WHEN (
            OLD.from_user_id IS DISTINCT FROM NEW.from_user_id
            OR OLD.property_id IS DISTINCT FROM NEW.property_id
            OR OLD.assigned_realtor_id IS DISTINCT FROM NEW.assigned_realtor_id
            OR OLD.type IS DISTINCT FROM NEW.type)
        EXECUTE FUNCTION transaction_room_lead_changed()""")
    op.execute("""CREATE FUNCTION transaction_room_events_immutable() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
        BEGIN
            -- A user deletion may sever the internal actor reference.
            IF TG_OP = 'UPDATE' THEN
                IF OLD.actor_id IS NOT NULL AND NEW.actor_id IS NULL AND pg_trigger_depth() > 1
                   AND (to_jsonb(NEW) - 'actor_id') = (to_jsonb(OLD) - 'actor_id') THEN
                    RETURN NEW;
                END IF;
            END IF;
            RAISE EXCEPTION 'Transaction room history is append only' USING ERRCODE = '55000';
        END;
        $$""")
    op.execute("""CREATE TRIGGER transaction_room_events_immutable
        BEFORE UPDATE OR DELETE ON transaction_room_events
        FOR EACH ROW EXECUTE FUNCTION transaction_room_events_immutable()""")

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
    functions = "transaction_room_lead_changed(), transaction_room_events_immutable()"
    op.execute(f"REVOKE ALL ON FUNCTION {functions} FROM PUBLIC")
    for role in ("anon", "authenticated", "service_role"):
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                REVOKE ALL ON TABLE {', '.join(TABLES)} FROM {role};
                REVOKE ALL ON FUNCTION {functions} FROM {role};
            END IF;
        END $$""")


def downgrade():
    op.execute("DROP TRIGGER transaction_room_events_immutable ON transaction_room_events")
    op.execute("DROP TRIGGER transaction_room_lead_changed ON leads")
    op.execute("DROP FUNCTION transaction_room_events_immutable(), transaction_room_lead_changed()")
    op.execute(f"DROP TABLE {', '.join(reversed(TABLES))}")
