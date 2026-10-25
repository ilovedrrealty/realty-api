"""Private versioned property research workspaces.

Revision ID: 52b2edc810ad
Revises: 49d50da0e05b
"""
from alembic import op

revision = "52b2edc810ad"
down_revision = "49d50da0e05b"
branch_labels = None
depends_on = None


def upgrade():
    # bootstrap_db creates current model tables before replaying Alembic on an
    # empty installation. Both paths must retain existing saved reviews.
    op.execute("""CREATE TABLE IF NOT EXISTS research_workspaces (
        id UUID PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        data JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_research_workspace_version CHECK (version >= 1)
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_research_workspace_user_updated ON research_workspaces (user_id, updated_at)")
    # This application uses custom JWT authentication in its server API, not
    # Supabase Auth. No direct Data API grants/policies are added for clients.
    op.execute("ALTER TABLE research_workspaces ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON research_workspaces FROM PUBLIC")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
            REVOKE ALL ON research_workspaces FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
            REVOKE ALL ON research_workspaces FROM authenticated;
        END IF;
    END $$""")


def downgrade():
    # Retain private notes during an application rollback; use a forward fix.
    pass
