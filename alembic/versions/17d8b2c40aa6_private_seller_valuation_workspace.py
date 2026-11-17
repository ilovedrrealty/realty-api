"""Add private staff workspace records for existing and new seller leads."""

from alembic import op


revision = "17d8b2c40aa6"
down_revision = "064b4d131f47"
branch_labels = None
depends_on = None

TABLES = ("seller_valuation_workspaces", "seller_valuation_comparables", "seller_valuation_follow_ups")


def upgrade():
    op.execute("""CREATE TABLE seller_valuation_workspaces (
        lead_id UUID PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
        subject JSONB NOT NULL CHECK (jsonb_typeof(subject) = 'object'),
        version INTEGER NOT NULL CHECK (version >= 1),
        evidence_version INTEGER NOT NULL CHECK (evidence_version BETWEEN 1 AND version),
        review JSONB,
        review_evidence_version INTEGER,
        CHECK ((review IS NULL AND review_evidence_version IS NULL) OR
            (review IS NOT NULL AND jsonb_typeof(review) = 'object' AND review_evidence_version IS NOT NULL
             AND review_evidence_version BETWEEN 1 AND evidence_version))
    )""")
    op.execute("""CREATE TABLE seller_valuation_comparables (
        id UUID PRIMARY KEY,
        lead_id UUID NOT NULL REFERENCES seller_valuation_workspaces(lead_id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('asking', 'sold')),
        property_type TEXT NOT NULL CHECK (property_type IN ('villa', 'apartment', 'condo', 'land', 'commercial')),
        location TEXT NOT NULL,
        area_m2 NUMERIC(10, 2) CHECK (area_m2 > 0),
        bedrooms INTEGER CHECK (bedrooms >= 0),
        bathrooms NUMERIC(4, 1) CHECK (bathrooms >= 0),
        price NUMERIC(12, 2) NOT NULL CHECK (price > 0),
        currency TEXT NOT NULL CHECK (currency IN ('USD', 'DOP')),
        captured_at TIMESTAMPTZ NOT NULL,
        listing_id UUID,
        title TEXT,
        closed_on DATE,
        source_label TEXT,
        source_reference TEXT,
        sold_review JSONB,
        CHECK (
          (kind = 'asking' AND listing_id IS NOT NULL AND title IS NOT NULL AND closed_on IS NULL
             AND source_label IS NULL AND source_reference IS NULL AND sold_review IS NULL)
          OR
          (kind = 'sold' AND listing_id IS NULL AND title IS NULL AND closed_on IS NOT NULL
             AND source_label IS NOT NULL AND source_reference IS NOT NULL
             AND (sold_review IS NULL OR jsonb_typeof(sold_review) = 'object'))
        )
    )""")
    op.execute("""CREATE UNIQUE INDEX uq_seller_valuation_asking_listing
        ON seller_valuation_comparables (lead_id, listing_id) WHERE kind = 'asking'""")
    op.execute("""CREATE INDEX ix_seller_valuation_comparables_lead
        ON seller_valuation_comparables (lead_id, captured_at, id)""")
    op.execute("""CREATE TABLE seller_valuation_follow_ups (
        id UUID PRIMARY KEY,
        lead_id UUID NOT NULL REFERENCES seller_valuation_workspaces(lead_id) ON DELETE CASCADE,
        follow_up_on DATE NOT NULL,
        note TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ
    )""")
    op.execute("""CREATE INDEX ix_seller_valuation_follow_ups_lead
        ON seller_valuation_follow_ups (lead_id, follow_up_on, id)""")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
        for role in ("anon", "authenticated", "service_role"):
            op.execute(f"""DO $$ BEGIN
                IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                    REVOKE ALL ON TABLE {table} FROM {role};
                END IF;
            END $$""")


def downgrade():
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
