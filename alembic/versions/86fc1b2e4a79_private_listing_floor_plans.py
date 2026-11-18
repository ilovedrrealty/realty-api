"""Keep staged and current approved floorplan bytes inside application recovery."""

from alembic import op


revision = "86fc1b2e4a79"
down_revision = "21f2fe166874"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS listing_floor_plans (
        id UUID PRIMARY KEY,
        listing_id UUID NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
        stage TEXT NOT NULL,
        content_type TEXT NOT NULL,
        byte_count INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        payload BYTEA NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        staged_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
        approved_at TIMESTAMPTZ,
        approved_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
        CONSTRAINT uq_listing_floor_plan_slot UNIQUE (listing_id, stage),
        CONSTRAINT ck_listing_floor_plan_file CHECK (
            content_type IN ('application/pdf','image/jpeg','image/png')
            AND byte_count BETWEEN 1 AND 6291456 AND octet_length(payload) = byte_count
            AND sha256 ~ '^[0-9a-f]{64}$'
        ),
        CONSTRAINT ck_listing_floor_plan_stage CHECK (
            (stage = 'draft' AND approved_at IS NULL AND approved_by_id IS NULL)
            OR (stage = 'published' AND approved_at IS NOT NULL)
        )
    )""")
    op.execute("ALTER TABLE listing_floor_plans ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON TABLE listing_floor_plans FROM PUBLIC")
    for role in ("anon", "authenticated", "service_role"):
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                REVOKE ALL ON TABLE listing_floor_plans FROM {role};
            END IF;
        END $$""")


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM listing_floor_plans) THEN
            RAISE EXCEPTION 'Preserve floor plan files before downgrading';
        END IF;
    END $$""")
    op.execute("DROP TABLE listing_floor_plans")
