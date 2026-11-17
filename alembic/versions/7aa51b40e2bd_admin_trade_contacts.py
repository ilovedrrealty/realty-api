"""Add a private Dominican trade directory and its source records."""

from alembic import op

revision = "7aa51b40e2bd"
down_revision = "52b2edc810ad"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS trade_contacts (
        id UUID PRIMARY KEY,
        identity_key CHAR(64) NOT NULL UNIQUE,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        province TEXT,
        locality TEXT,
        details JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS trade_contact_sources (
        id UUID PRIMARY KEY,
        contact_id UUID NOT NULL REFERENCES trade_contacts(id) ON DELETE CASCADE,
        source_key CHAR(64) NOT NULL,
        evidence JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_trade_contact_source UNIQUE (contact_id, source_key)
    )""")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_trade_contacts_category_province_name
        ON trade_contacts (category, province, name, id)""")
    op.execute("ALTER TABLE trade_contacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE trade_contact_sources ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON TABLE trade_contacts, trade_contact_sources FROM PUBLIC")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
            REVOKE ALL ON TABLE trade_contacts, trade_contact_sources FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
            REVOKE ALL ON TABLE trade_contacts, trade_contact_sources FROM authenticated;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN
            REVOKE ALL ON TABLE trade_contacts, trade_contact_sources FROM service_role;
        END IF;
    END $$""")


def downgrade():
    # Application rollback keeps privately imported directory records.
    pass
