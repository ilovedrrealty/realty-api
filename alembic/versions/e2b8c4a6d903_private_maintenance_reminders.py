"""Store owner-authored in-app reminders without any delivery worker."""
from alembic import op

revision = "e2b8c4a6d903"
down_revision = "f71c5a9d243e"
branch_labels = None
depends_on = None

PREFERENCES = "network_maintenance_preferences"
SCHEDULES = "network_maintenance_schedules"
OCCURRENCES = "network_maintenance_occurrences"
ACTIONS = "network_maintenance_actions"
TABLES = (PREFERENCES, SCHEDULES, OCCURRENCES, ACTIONS)


def upgrade():
    op.execute(f"""CREATE TABLE IF NOT EXISTS {PREFERENCES} (
        id UUID PRIMARY KEY,
        actor_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        property_id UUID REFERENCES network_properties(id) ON DELETE RESTRICT,
        listing_id UUID REFERENCES listings(id) ON DELETE RESTRICT,
        enabled BOOLEAN NOT NULL DEFAULT false,
        lead_days INTEGER NOT NULL DEFAULT 7,
        version INTEGER NOT NULL DEFAULT 0,
        CONSTRAINT uq_maintenance_preference_registry UNIQUE (actor_id, property_id),
        CONSTRAINT uq_maintenance_preference_listing UNIQUE (actor_id, listing_id),
        CONSTRAINT ck_maintenance_preference_one_source CHECK ((property_id IS NULL) <> (listing_id IS NULL)),
        CONSTRAINT ck_maintenance_preference_bounds CHECK (lead_days BETWEEN 0 AND 30 AND version >= 0
            AND (version > 0 OR (NOT enabled AND lead_days = 7)))
    )""")
    op.execute(f"""CREATE TABLE IF NOT EXISTS {SCHEDULES} (
        id UUID PRIMARY KEY,
        preference_id UUID NOT NULL REFERENCES {PREFERENCES}(id) ON DELETE RESTRICT,
        title TEXT NOT NULL,
        repeat TEXT NOT NULL,
        anchor_on DATE NOT NULL,
        status TEXT NOT NULL,
        version INTEGER NOT NULL,
        CONSTRAINT ck_maintenance_schedule_bounds CHECK (char_length(title) BETWEEN 1 AND 180 AND version >= 1),
        CONSTRAINT ck_maintenance_schedule_repeat CHECK (repeat IN ('none','weekly','monthly','quarterly','yearly')),
        CONSTRAINT ck_maintenance_schedule_status CHECK (status IN ('active','paused','completed','stopped'))
    )""")
    op.execute(f"""CREATE TABLE IF NOT EXISTS {OCCURRENCES} (
        schedule_id UUID NOT NULL REFERENCES {SCHEDULES}(id) ON DELETE RESTRICT,
        due_on DATE NOT NULL,
        status TEXT NOT NULL,
        snoozed_until DATE,
        closed_on DATE,
        PRIMARY KEY (schedule_id, due_on),
        CONSTRAINT ck_maintenance_occurrence_state CHECK (status IN ('open','completed','stopped')
            AND (status = 'open') = (closed_on IS NULL) AND (status = 'open' OR snoozed_until IS NULL))
    )""")
    op.execute(f"""CREATE TABLE IF NOT EXISTS {ACTIONS} (
        actor_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        submission_id UUID NOT NULL,
        preference_id UUID NOT NULL REFERENCES {PREFERENCES}(id) ON DELETE RESTRICT,
        schedule_id UUID REFERENCES {SCHEDULES}(id) ON DELETE RESTRICT,
        operation TEXT NOT NULL,
        creation_sha256 TEXT NOT NULL,
        PRIMARY KEY (actor_id, submission_id),
        CONSTRAINT ck_maintenance_action_scope CHECK (operation IN ('preferences','create','complete','snooze','pause','resume','stop')
            AND (operation = 'preferences') = (schedule_id IS NULL) AND creation_sha256 ~ '^[0-9a-f]{{64}}$')
    )""")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_maintenance_schedule_preference ON {SCHEDULES}(preference_id, id)")
    op.execute(f"""CREATE UNIQUE INDEX IF NOT EXISTS uq_maintenance_occurrence_one_open
        ON {OCCURRENCES}(schedule_id) WHERE status = 'open'""")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        for role in ("anon", "authenticated", "service_role"):
            op.execute(f"""DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
                    REVOKE ALL ON {table} FROM {role};
                END IF;
            END $$""")


def downgrade():
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
