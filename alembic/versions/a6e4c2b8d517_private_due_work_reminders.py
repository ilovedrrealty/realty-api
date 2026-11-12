"""Keep current project reminder preferences and suppression separate from work."""
from alembic import op

revision = "a6e4c2b8d517"
down_revision = "e2b8c4a6d903"
branch_labels = None
depends_on = None

PREFERENCES = "network_due_work_preferences"
DISPOSITIONS = "network_due_work_dispositions"
ACTIONS = "network_due_work_actions"
TABLES = (PREFERENCES, DISPOSITIONS, ACTIONS)
MEMBER = "network_project_members"
MILESTONE = "network_milestones"
WORK = "network_service_work"


def _constraint(table, name, expression):
    op.execute(f"""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='{name}' AND conrelid='{table}'::regclass) THEN
            ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expression});
        END IF;
    END $$""")


def upgrade_work_generation():
    # The immutable legacy work record is untouched. A previously confirmed
    # slot has the reserved generation 1 until a new proposal is accepted.
    op.execute(f"ALTER TABLE {WORK} ADD COLUMN IF NOT EXISTS confirmed_reminder_version INTEGER")
    _constraint(WORK, "ck_service_work_reminder_occurrence", "confirmed_reminder_version IS NULL OR "
                "(confirmed_starts_at IS NOT NULL AND confirmed_reminder_version BETWEEN 2 AND version)")


def upgrade():
    op.execute(f"ALTER TABLE {MEMBER} ADD COLUMN IF NOT EXISTS reminder_grant_version INTEGER NOT NULL DEFAULT 1")
    _constraint(MEMBER, "ck_network_member_reminder_grant", "reminder_grant_version >= 1")
    op.execute(f"ALTER TABLE {MILESTONE} ADD COLUMN IF NOT EXISTS reminder_occurrence_version INTEGER NOT NULL DEFAULT 1")
    _constraint(MILESTONE, "ck_network_milestone_reminder_occurrence", "reminder_occurrence_version BETWEEN 1 AND version")
    upgrade_work_generation()
    op.execute(f"""CREATE TABLE IF NOT EXISTS {PREFERENCES} (
        id UUID PRIMARY KEY,
        actor_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        project_id UUID NOT NULL REFERENCES network_projects(id) ON DELETE RESTRICT,
        grant_version INTEGER NOT NULL,
        milestones_enabled BOOLEAN NOT NULL,
        visits_enabled BOOLEAN NOT NULL,
        lead_days INTEGER NOT NULL,
        version INTEGER NOT NULL,
        CONSTRAINT uq_due_work_preference_grant UNIQUE (actor_id, project_id, grant_version),
        CONSTRAINT ck_due_work_preference_bounds CHECK (grant_version >= 0 AND lead_days BETWEEN 0 AND 30 AND version >= 1)
    )""")
    op.execute(f"""CREATE TABLE IF NOT EXISTS {DISPOSITIONS} (
        preference_id UUID NOT NULL REFERENCES {PREFERENCES}(id) ON DELETE RESTRICT,
        kind TEXT NOT NULL,
        item_id UUID NOT NULL,
        occurrence_version INTEGER NOT NULL,
        state TEXT NOT NULL,
        snoozed_until DATE,
        version INTEGER NOT NULL,
        PRIMARY KEY (preference_id, kind, item_id, occurrence_version),
        CONSTRAINT ck_due_work_item_bounds CHECK (kind IN ('milestone','visit') AND occurrence_version >= 1 AND version >= 1),
        CONSTRAINT ck_due_work_item_state CHECK (state IN ('active','snoozed','dismissed','stopped')
            AND (state='snoozed') = (snoozed_until IS NOT NULL))
    )""")
    op.execute(f"""CREATE TABLE IF NOT EXISTS {ACTIONS} (
        actor_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        submission_id UUID NOT NULL,
        preference_id UUID NOT NULL REFERENCES {PREFERENCES}(id) ON DELETE RESTRICT,
        kind TEXT,
        item_id UUID,
        occurrence_version INTEGER,
        operation TEXT NOT NULL,
        creation_sha256 TEXT NOT NULL,
        PRIMARY KEY (actor_id, submission_id),
        CONSTRAINT ck_due_work_action_scope CHECK (
            (operation='preferences' AND kind IS NULL AND item_id IS NULL AND occurrence_version IS NULL)
            OR (operation IN ('snooze','dismiss','restore','stop') AND kind IN ('milestone','visit')
                AND kind IS NOT NULL AND item_id IS NOT NULL AND occurrence_version IS NOT NULL AND occurrence_version >= 1)),
        CONSTRAINT ck_due_work_action_hash CHECK (creation_sha256 ~ '^[0-9a-f]{{64}}$')
    )""")
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
    op.execute(f"ALTER TABLE {WORK} DROP COLUMN confirmed_reminder_version")
    op.execute(f"ALTER TABLE {MILESTONE} DROP COLUMN reminder_occurrence_version")
    op.execute(f"ALTER TABLE {MEMBER} DROP COLUMN reminder_grant_version")
