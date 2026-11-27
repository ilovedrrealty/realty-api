"""Private future assignment events and queued one-attempt notice history."""
from alembic import op

revision = "f7a9c3e2d114"
down_revision = "e8b5a3d1f907"
branch_labels = None
depends_on = None


def upgrade():
    # No old account/lead scan, backfill, enqueue, heartbeat or provider request.
    op.execute("""CREATE TABLE IF NOT EXISTS assignment_events (
        id UUID PRIMARY KEY,
        source_kind TEXT NOT NULL,
        lead_id UUID REFERENCES leads(id) ON DELETE RESTRICT,
        user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
        actor_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        request_id UUID NOT NULL,
        request_sha256 TEXT NOT NULL,
        previous_agent_id UUID REFERENCES users(id) ON DELETE RESTRICT,
        agent_id UUID REFERENCES users(id) ON DELETE RESTRICT,
        owner_upgraded_id UUID REFERENCES users(id) ON DELETE RESTRICT,
        cycle INTEGER NOT NULL,
        action TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_assignment_actor_request UNIQUE(actor_id,request_id),
        CONSTRAINT ck_assignment_source CHECK(
            (source_kind='lead' AND lead_id IS NOT NULL AND user_id IS NULL) OR
            (source_kind='account' AND user_id IS NOT NULL AND lead_id IS NULL)),
        CONSTRAINT ck_assignment_action CHECK(
            (action='assigned' AND agent_id IS NOT NULL) OR
            (action='cleared' AND agent_id IS NULL AND source_kind='account')),
        CONSTRAINT ck_assignment_owner_upgrade CHECK(owner_upgraded_id IS NULL OR source_kind='lead'),
        CONSTRAINT ck_assignment_cycle CHECK(cycle >= 1)
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_assignment_lead_history ON assignment_events(lead_id,created_at,id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_assignment_user_history ON assignment_events(user_id,created_at,id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_assignment_lead_cycle ON assignment_events(lead_id,cycle)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_assignment_user_cycle ON assignment_events(user_id,cycle)")
    op.execute("""CREATE TABLE IF NOT EXISTS assignment_email_jobs (
        id UUID PRIMARY KEY,
        event_id UUID NOT NULL REFERENCES assignment_events(id) ON DELETE RESTRICT,
        kind TEXT NOT NULL,
        recipient_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        payload JSONB NOT NULL,
        payload_sha256 TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempt_count INTEGER NOT NULL DEFAULT 0,
        attempted_at TIMESTAMPTZ,
        accepted_at TIMESTAMPTZ,
        outcome_uncertain BOOLEAN NOT NULL DEFAULT false,
        lease_token UUID,
        lease_until TIMESTAMPTZ,
        provider_id TEXT,
        error_code TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_assignment_notice UNIQUE(event_id,kind),
        CONSTRAINT ck_assignment_notice_kind CHECK(kind IN ('lead_assigned_agent','owner_upgrade_customer','owner_assigned_agent')),
        CONSTRAINT ck_assignment_notice_status CHECK(status IN ('pending','processing','accepted','failed','needs_review','skipped')),
        CONSTRAINT ck_assignment_notice_attempts CHECK(attempt_count IN (0,1)),
        CONSTRAINT ck_assignment_notice_attempt_time CHECK(
            (attempt_count=0 AND attempted_at IS NULL) OR (attempt_count=1 AND attempted_at IS NOT NULL)),
        CONSTRAINT ck_assignment_notice_acceptance CHECK(
            (status='accepted' AND accepted_at IS NOT NULL AND attempt_count=1 AND NOT outcome_uncertain) OR
            (status<>'accepted' AND accepted_at IS NULL)),
        CONSTRAINT ck_assignment_notice_uncertain CHECK(
            (status='needs_review' AND outcome_uncertain AND attempt_count=1) OR
            (status<>'needs_review' AND NOT outcome_uncertain)),
        CONSTRAINT ck_assignment_notice_pending CHECK(status<>'pending' OR attempt_count=0),
        CONSTRAINT ck_assignment_notice_processing CHECK(
            status<>'processing' OR (attempt_count=1 AND lease_token IS NOT NULL AND lease_until IS NOT NULL))
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_assignment_notice_pending ON assignment_email_jobs(status,created_at,id)")
    op.execute("""CREATE OR REPLACE FUNCTION assignment_guard_event() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        BEGIN RAISE EXCEPTION USING ERRCODE='23000',MESSAGE='Assignment event is immutable'; END $$""")
    op.execute("""CREATE OR REPLACE FUNCTION assignment_guard_notice() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$ DECLARE permitted boolean; BEGIN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION USING ERRCODE='23000',MESSAGE='Assignment notice is retained';
        END IF;
        IF TG_OP='UPDATE' THEN
            IF ROW(NEW.id,NEW.event_id,NEW.kind,NEW.recipient_user_id,NEW.payload,NEW.payload_sha256,NEW.created_at)
                IS DISTINCT FROM ROW(OLD.id,OLD.event_id,OLD.kind,OLD.recipient_user_id,OLD.payload,OLD.payload_sha256,OLD.created_at)
            THEN RAISE EXCEPTION USING ERRCODE='23000',MESSAGE='Assignment notice identity is immutable'; END IF;
            IF OLD.status IN ('accepted','failed','needs_review','skipped') THEN
                RAISE EXCEPTION USING ERRCODE='23000',MESSAGE='Assignment notice outcome is final';
            END IF;
        END IF;
        EXECUTE format('SELECT EXISTS(SELECT FROM %I.assignment_events e WHERE e.id=$1 AND e.source_kind=''lead''
            AND (($2=''lead_assigned_agent'' AND $3=e.agent_id)
                OR ($2=''owner_assigned_agent'' AND e.owner_upgraded_id IS NOT NULL AND $3=e.agent_id)
                OR ($2=''owner_upgrade_customer'' AND $3=e.owner_upgraded_id)))',TG_TABLE_SCHEMA)
            INTO permitted USING NEW.event_id,NEW.kind,NEW.recipient_user_id;
        IF NOT permitted
        THEN RAISE EXCEPTION USING ERRCODE='23000',MESSAGE='Assignment notice context mismatch'; END IF;
        RETURN NEW; END $$""")
    op.execute("DROP TRIGGER IF EXISTS assignment_event_immutable ON assignment_events")
    op.execute("CREATE TRIGGER assignment_event_immutable BEFORE UPDATE OR DELETE ON assignment_events FOR EACH ROW EXECUTE FUNCTION assignment_guard_event()")
    op.execute("DROP TRIGGER IF EXISTS assignment_notice_guard ON assignment_email_jobs")
    op.execute("CREATE TRIGGER assignment_notice_guard BEFORE INSERT OR UPDATE OR DELETE ON assignment_email_jobs FOR EACH ROW EXECUTE FUNCTION assignment_guard_notice()")
    for table in ("assignment_events", "assignment_email_jobs"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        for role in ("anon", "authenticated", "service_role"):
            op.execute(f"DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='{role}') THEN REVOKE ALL ON {table} FROM {role}; END IF; END $$")
    for name in ("assignment_guard_event", "assignment_guard_notice"):
        op.execute(f"REVOKE ALL ON FUNCTION {name}() FROM PUBLIC")
        for role in ("anon", "authenticated", "service_role"):
            op.execute(f"DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='{role}') THEN REVOKE ALL ON FUNCTION {name}() FROM {role}; END IF; END $$")


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT FROM assignment_events) OR EXISTS(SELECT FROM assignment_email_jobs) THEN
            RAISE EXCEPTION USING ERRCODE='23000',MESSAGE='Assignment history requires retained schema';
        END IF; END $$""")
    op.execute("DROP TABLE assignment_email_jobs")
    op.execute("DROP TABLE assignment_events")
    op.execute("DROP FUNCTION assignment_guard_notice()")
    op.execute("DROP FUNCTION assignment_guard_event()")
