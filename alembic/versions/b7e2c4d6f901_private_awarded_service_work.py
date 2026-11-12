"""Retain visit agreements and approved private project completion."""
from alembic import op

revision = "b7e2c4d6f901"
down_revision = "a4b6c0d2e834"
branch_labels = None
depends_on = None

TABLES = ("network_service_work", "network_service_work_actions")
GUARD = "network_guard_awarded_service_work"


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS network_service_work (
        request_id UUID PRIMARY KEY REFERENCES network_service_requests(id) ON DELETE RESTRICT,
        version INTEGER NOT NULL, status TEXT NOT NULL,
        confirmed_starts_at TIMESTAMPTZ, confirmed_duration_minutes INTEGER,
        proposed_starts_at TIMESTAMPTZ, proposed_duration_minutes INTEGER, proposed_by TEXT,
        completion_note TEXT, completed_on DATE, completion_review_note TEXT,
        completion_update_id UUID UNIQUE REFERENCES network_project_updates(id) ON DELETE RESTRICT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_service_work_state
            CHECK(version>=1 AND status IN ('unscheduled','proposed','confirmed','cancelled','completion_requested','completed')),
        CONSTRAINT ck_service_work_confirmed
            CHECK((confirmed_starts_at IS NULL)=(confirmed_duration_minutes IS NULL)
              AND (confirmed_duration_minutes IS NULL OR confirmed_duration_minutes BETWEEN 15 AND 480)
              AND (status NOT IN ('confirmed','completion_requested','completed') OR confirmed_starts_at IS NOT NULL)
              AND (status NOT IN ('unscheduled','cancelled') OR confirmed_starts_at IS NULL)),
        CONSTRAINT ck_service_work_proposal
            CHECK((status='proposed')=(proposed_starts_at IS NOT NULL)
              AND (proposed_starts_at IS NULL)=(proposed_duration_minutes IS NULL)
              AND (proposed_starts_at IS NULL)=(proposed_by IS NULL)
              AND (proposed_duration_minutes IS NULL OR proposed_duration_minutes BETWEEN 15 AND 480)
              AND (proposed_by IS NULL OR proposed_by IN ('owner','provider'))),
        CONSTRAINT ck_service_work_completion
            CHECK((completion_note IS NULL)=(completed_on IS NULL)
              AND (completion_note IS NULL OR char_length(completion_note) BETWEEN 1 AND 2000)
              AND (completion_review_note IS NULL OR char_length(completion_review_note) BETWEEN 1 AND 2000)
              AND (status NOT IN ('completion_requested','completed') OR completion_note IS NOT NULL)
              AND (status='completed')=(completion_update_id IS NOT NULL))
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS network_service_work_actions (
        actor_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        submission_id UUID NOT NULL,
        request_id UUID NOT NULL REFERENCES network_service_work(request_id) ON DELETE RESTRICT,
        operation TEXT NOT NULL, creation_sha256 TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (actor_id,submission_id),
        CONSTRAINT ck_service_work_action_hash CHECK(creation_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_service_work_action_operation
            CHECK(operation IN ('propose_visit','accept_visit','decline_visit','cancel_visit',
                                'request_completion','request_changes','approve_completion'))
    )""")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_service_work_confirmed_start
                   ON network_service_work(confirmed_starts_at) WHERE confirmed_starts_at IS NOT NULL""")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_service_work_action_request
                   ON network_service_work_actions(request_id,created_at)""")
    op.execute(f"""CREATE OR REPLACE FUNCTION {GUARD}() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER AS $$ BEGIN
        IF TG_OP='DELETE' OR TG_TABLE_NAME='network_service_work_actions' THEN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Service work history must be retained';
        END IF;
        IF TG_OP='UPDATE' AND
            (NEW.request_id<>OLD.request_id OR NEW.version<>OLD.version+1 OR OLD.status='completed') THEN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Invalid service work version or identity';
        END IF;
        IF TG_OP='INSERT' AND (NEW.version<>1 OR NEW.status<>'proposed') THEN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Service work begins with a visit proposal';
        END IF;
        IF NOT EXISTS(SELECT 1 FROM network_service_requests r
            JOIN network_service_recipients winner ON winner.request_id=r.id AND winner.status='awarded'
            WHERE r.id=NEW.request_id AND r.status='awarded' AND r.project_id IS NOT NULL) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Service work requires an awarded project';
        END IF;
        IF NEW.completion_update_id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM network_service_requests r
            JOIN network_projects p ON p.id=r.project_id
            JOIN network_project_updates u ON u.project_id=p.id
            WHERE r.id=NEW.request_id AND u.id=NEW.completion_update_id
              AND u.author_id=p.owner_id AND u.kind='progress' AND u.correction_of IS NULL
              AND u.body=NEW.completion_note AND u.occurred_on=NEW.completed_on AND u.media_ids='[]'::jsonb) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Completion must link to its approved owner progress entry';
        END IF;
        RETURN NEW;
        END $$""")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"DROP TRIGGER IF EXISTS retain_service_work ON {table}")
        events = "INSERT OR UPDATE OR DELETE" if table == TABLES[0] else "UPDATE OR DELETE"
        op.execute(f"CREATE TRIGGER retain_service_work BEFORE {events} ON {table} FOR EACH ROW EXECUTE FUNCTION {GUARD}()")
    op.execute(f"REVOKE ALL ON FUNCTION {GUARD}() FROM PUBLIC")
    for role in ("anon", "authenticated"):
        op.execute(f"""DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
                REVOKE ALL ON network_service_work, network_service_work_actions FROM {role};
                REVOKE ALL ON FUNCTION {GUARD}() FROM {role};
            END IF;
        END $$""")


def downgrade():
    op.execute("LOCK TABLE network_service_work, network_service_work_actions IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM network_service_work) OR EXISTS(SELECT 1 FROM network_service_work_actions) THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Service work history must be retained';
        END IF;
    END $$""")
    op.execute("DROP TABLE network_service_work_actions, network_service_work")
    op.execute(f"DROP FUNCTION {GUARD}()")
