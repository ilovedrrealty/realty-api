"""Add final private delivery tables directly after the deployed trade directory.

The donor's two delivery revisions belonged to an unreleased property-network
chain. This independent revision creates their final schema from the live head;
it does not replay either donor revision or scan historical leads or accounts.
"""
from alembic import op


revision = "d721f0b6a84c"
down_revision = "7aa51b40e2bd"
branch_labels = None
depends_on = None


def upgrade():
    # create_all may already have created these exact model tables on a fresh
    # database. The live upgrade creates both empty tables in their final shape.
    op.execute("""CREATE TABLE IF NOT EXISTS lead_delivery_jobs (
        id UUID PRIMARY KEY,
        lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
        step TEXT NOT NULL,
        provider TEXT NOT NULL,
        depends_on TEXT,
        payload JSONB NOT NULL,
        payload_sha256 TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempt_count INTEGER NOT NULL DEFAULT 0,
        safe_to_retry BOOLEAN NOT NULL DEFAULT false,
        outcome_uncertain BOOLEAN NOT NULL DEFAULT false,
        next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        first_attempt_at TIMESTAMPTZ,
        last_attempt_at TIMESTAMPTZ,
        lease_token UUID,
        lease_until TIMESTAMPTZ,
        accepted_at TIMESTAMPTZ,
        provider_id TEXT,
        error_code TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        user_id UUID CONSTRAINT fk_delivery_signup_user REFERENCES users(id) ON DELETE RESTRICT,
        CONSTRAINT uq_lead_delivery_step UNIQUE (lead_id, step),
        CONSTRAINT uq_signup_delivery_step UNIQUE (user_id, step),
        CONSTRAINT ck_delivery_exactly_one_event CHECK ((lead_id IS NULL) <> (user_id IS NULL)),
        CONSTRAINT ck_lead_delivery_status CHECK (status IN ('pending','processing','retry_wait','accepted','failed','needs_review','skipped')),
        CONSTRAINT ck_lead_delivery_attempts CHECK (attempt_count >= 0 AND attempt_count <= 4)
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_lead_delivery_due ON lead_delivery_jobs (status, next_attempt_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_lead_delivery_lead ON lead_delivery_jobs (lead_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_delivery_signup ON lead_delivery_jobs (user_id, created_at)")
    op.execute("""CREATE TABLE IF NOT EXISTS lead_delivery_worker_state (
        id INTEGER PRIMARY KEY,
        last_poll_at TIMESTAMPTZ NOT NULL,
        last_completed_at TIMESTAMPTZ,
        last_batch_count INTEGER NOT NULL DEFAULT 0,
        error_code TEXT,
        CONSTRAINT ck_lead_delivery_worker_singleton CHECK (id = 1)
    )""")
    # The backend uses its established server authorization. No direct browser
    # Data API policy is added for immutable contact-bearing delivery payloads.
    for table in ("lead_delivery_jobs", "lead_delivery_worker_state"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
                REVOKE ALL ON {table} FROM anon;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
                REVOKE ALL ON {table} FROM authenticated;
            END IF;
        END $$""")


def downgrade():
    # A code rollback must not erase private accepted-event outcomes. Retain
    # these tables and use a reviewed forward migration for schema corrections.
    pass
