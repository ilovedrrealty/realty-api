"""Extend the existing private delivery queue for future verified signups only."""
from alembic import op

revision = "e2f4a8b0c612"
down_revision = "d1e3f7a9b521"
branch_labels = None
depends_on = None


def upgrade():
    # No account/lead scan, job backfill, provider call or feature activation.
    # Conditional columns/constraints also support bootstrap create_all.
    op.execute("ALTER TABLE lead_delivery_jobs ADD COLUMN IF NOT EXISTS user_id UUID")
    op.execute("ALTER TABLE lead_delivery_jobs ALTER COLUMN lead_id DROP NOT NULL")
    op.execute("""DO $$ BEGIN
        IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='lead_delivery_jobs'::regclass AND conname='fk_delivery_signup_user') THEN
            ALTER TABLE lead_delivery_jobs ADD CONSTRAINT fk_delivery_signup_user FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE RESTRICT;
        END IF;
        IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='lead_delivery_jobs'::regclass AND conname='uq_signup_delivery_step') THEN
            ALTER TABLE lead_delivery_jobs ADD CONSTRAINT uq_signup_delivery_step UNIQUE(user_id,step);
        END IF;
        IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='lead_delivery_jobs'::regclass AND conname='ck_delivery_exactly_one_event') THEN
            ALTER TABLE lead_delivery_jobs ADD CONSTRAINT ck_delivery_exactly_one_event CHECK((lead_id IS NULL)<>(user_id IS NULL));
        END IF;
    END $$""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_delivery_signup ON lead_delivery_jobs(user_id,created_at)")


def downgrade():
    # Retain private operational outcomes and preserve rollback compatibility.
    pass
