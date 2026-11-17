"""Preserve account deletion requests, cancellation and private Admin review."""

from alembic import op

revision = "c3a914f0b628"
down_revision = "f92c8a74d31e"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS account_deletion_requests (
        id UUID PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        requested_at TIMESTAMPTZ NOT NULL,
        status TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        reviewed_at TIMESTAMPTZ,
        reviewed_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
        review_reason TEXT,
        cancelled_at TIMESTAMPTZ,
        CONSTRAINT ck_account_deletion_status
            CHECK (status IN ('pending_review','reviewed','retention_hold','cancelled')),
        CONSTRAINT ck_account_deletion_version CHECK (version >= 1)
    )""")
    op.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_account_deletion_active_user
        ON account_deletion_requests(user_id) WHERE status <> 'cancelled'""")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_account_deletion_status_requested
        ON account_deletion_requests(status, requested_at, id)""")
    op.execute("""CREATE TABLE IF NOT EXISTS account_deletion_events (
        id UUID PRIMARY KEY,
        request_id UUID NOT NULL REFERENCES account_deletion_requests(id) ON DELETE RESTRICT,
        actor_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        action TEXT NOT NULL,
        version INTEGER NOT NULL,
        reason TEXT,
        occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_account_deletion_events_request_id
        ON account_deletion_events(request_id)""")
    # Existing request timestamps were already saved without a separate ID.
    # Using that user's UUID once retains them without resetting their date.
    op.execute("""INSERT INTO account_deletion_requests(id,user_id,requested_at,status,version)
        SELECT u.id,u.id,u.deletion_requested_at,'pending_review',1
        FROM users u WHERE u.deletion_requested_at IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM account_deletion_requests r WHERE r.user_id=u.id)
        ON CONFLICT (id) DO NOTHING""")
    op.execute("""INSERT INTO account_deletion_events(id,request_id,actor_id,action,version,occurred_at)
        SELECT r.id,r.id,r.user_id,'legacy_requested',1,r.requested_at
        FROM account_deletion_requests r
        WHERE r.id=r.user_id AND NOT EXISTS (
            SELECT 1 FROM account_deletion_events e WHERE e.request_id=r.id)
        ON CONFLICT (id) DO NOTHING""")
    op.execute("""CREATE OR REPLACE FUNCTION keep_account_deletion_events() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Account deletion review history must be retained';
        END $$""")
    op.execute("""CREATE TRIGGER account_deletion_events_immutable
        BEFORE UPDATE OR DELETE ON account_deletion_events
        FOR EACH ROW EXECUTE FUNCTION keep_account_deletion_events()""")
    for table in ("account_deletion_requests", "account_deletion_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
        for role in ("anon", "authenticated", "service_role"):
            op.execute(f"""DO $$ BEGIN
                IF EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') THEN
                    REVOKE ALL ON TABLE {table} FROM {role};
                END IF;
            END $$""")
    op.execute("REVOKE ALL ON FUNCTION keep_account_deletion_events() FROM PUBLIC")


def downgrade():
    # Retain request and review history during an application rollback.
    pass
