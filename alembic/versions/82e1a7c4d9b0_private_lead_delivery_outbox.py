"""Durable private delivery records for future accepted leads only.

Revision ID: 82e1a7c4d9b0
Revises: 52b2edc810ad
"""
from alembic import op

revision = "82e1a7c4d9b0"
down_revision = "52b2edc810ad"
branch_labels = None
depends_on = None


def upgrade():
    # No lead scan, backfill, provider call, queued job or heartbeat is created.
    # IF NOT EXISTS also supports bootstrap_db's create_all on empty installs.
    op.execute("""CREATE TABLE IF NOT EXISTS lead_delivery_jobs (
        id UUID PRIMARY KEY,
        lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
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
        CONSTRAINT uq_lead_delivery_step UNIQUE (lead_id, step),
        CONSTRAINT ck_lead_delivery_status CHECK (status IN ('pending','processing','retry_wait','accepted','failed','needs_review','skipped')),
        CONSTRAINT ck_lead_delivery_attempts CHECK (attempt_count >= 0 AND attempt_count <= 4)
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_lead_delivery_due ON lead_delivery_jobs (status, next_attempt_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_lead_delivery_lead ON lead_delivery_jobs (lead_id, created_at)")
    op.execute("""CREATE TABLE IF NOT EXISTS lead_delivery_worker_state (
        id INTEGER PRIMARY KEY,
        last_poll_at TIMESTAMPTZ NOT NULL,
        last_completed_at TIMESTAMPTZ,
        last_batch_count INTEGER NOT NULL DEFAULT 0,
        error_code TEXT,
        CONSTRAINT ck_lead_delivery_worker_singleton CHECK (id = 1)
    )""")
    # Custom server JWT authorization is used. These private tables are not
    # exposed to Supabase's anonymous/authenticated Data API clients.
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
    # Preserve accepted-event records during a code rollback; use a forward fix.
    pass
