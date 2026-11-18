"""Keep original seller valuation details private and attached to their lead."""

from alembic import op


revision = "064b4d131f47"
down_revision = "fad71e03c6b9"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS seller_valuation_intakes (
        lead_id UUID PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
        subject JSONB NOT NULL,
        CONSTRAINT ck_seller_valuation_intake_subject CHECK (
            jsonb_typeof(subject) = 'object' AND subject ?& ARRAY['property_type', 'location'])
    )""")
    op.execute("ALTER TABLE seller_valuation_intakes ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON TABLE seller_valuation_intakes FROM PUBLIC")
    for role in ("anon", "authenticated", "service_role"):
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                REVOKE ALL ON TABLE seller_valuation_intakes FROM {role};
            END IF;
        END $$""")


def downgrade():
    op.execute("DROP TABLE seller_valuation_intakes")
