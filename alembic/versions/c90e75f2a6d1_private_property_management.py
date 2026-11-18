"""Private property operations and append-only revision evidence.

Revision ID: c90e75f2a6d1
Revises: b12d41e2c9f0
"""
from alembic import op

revision = "c90e75f2a6d1"
down_revision = "b12d41e2c9f0"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS property_management_records (
        id UUID PRIMARY KEY,
        listing_id UUID NOT NULL REFERENCES listings(id) ON DELETE RESTRICT,
        kind TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, data JSONB NOT NULL,
        due_date DATE, currency TEXT, amount NUMERIC(14,2), version INTEGER NOT NULL DEFAULT 1,
        approval_status TEXT NOT NULL DEFAULT 'not_required', approval_limit NUMERIC(14,2),
        approved_by UUID REFERENCES users(id) ON DELETE SET NULL, approved_at TIMESTAMPTZ,
        origin_record_id UUID REFERENCES property_management_records(id) ON DELETE RESTRICT,
        recurrence_anchor_day INTEGER,
        created_by UUID REFERENCES users(id) ON DELETE SET NULL,
        updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_management_record_version CHECK (version >= 1),
        CONSTRAINT ck_management_record_kind CHECK (kind IN ('lease','maintenance','inspection','ledger','task'))
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_management_listing_kind_due ON property_management_records (listing_id,kind,due_date)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_management_recurring_origin ON property_management_records (origin_record_id) WHERE kind = 'task' AND origin_record_id IS NOT NULL")
    op.execute("""CREATE TABLE IF NOT EXISTS property_management_revisions (
        id UUID PRIMARY KEY,
        record_id UUID NOT NULL REFERENCES property_management_records(id) ON DELETE RESTRICT,
        version INTEGER NOT NULL, action TEXT NOT NULL, snapshot JSONB NOT NULL,
        actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_management_revision_version UNIQUE (record_id,version)
    )""")
    for table in ("property_management_records", "property_management_revisions"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"""DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON {table} FROM anon; END IF;
            IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN REVOKE ALL ON {table} FROM authenticated; END IF;
        END $$""")


def downgrade():
    # Financial, tenancy and approval history survives a source rollback.
    pass
