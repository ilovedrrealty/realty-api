"""Add private staff workspace records for existing and new seller leads."""

from alembic import op


revision = "17d8b2c40aa6"
down_revision = "064b4d131f47"
branch_labels = None
depends_on = None

TABLES = ("seller_valuation_workspaces", "seller_valuation_comparables", "seller_valuation_follow_ups")
CHECKS = {
    "seller_valuation_workspaces": {
        "ck_seller_valuation_subject": "jsonb_typeof(subject) = 'object'",
        "ck_seller_valuation_version": "version >= 1 AND evidence_version BETWEEN 1 AND version",
        "ck_seller_valuation_review": (
            "(review IS NULL AND review_evidence_version IS NULL) OR "
            "(review IS NOT NULL AND jsonb_typeof(review) = 'object' AND review_evidence_version IS NOT NULL "
            "AND review_evidence_version BETWEEN 1 AND evidence_version)"
        ),
    },
    "seller_valuation_comparables": {
        "ck_seller_valuation_comparable_kind": "kind IN ('asking', 'sold')",
        "ck_seller_valuation_comparable_type": "property_type IN ('villa', 'apartment', 'condo', 'land', 'commercial')",
        "ck_seller_valuation_comparable_amounts": (
            "area_m2 > 0 AND bedrooms >= 0 AND bathrooms >= 0 AND price > 0 AND currency IN ('USD', 'DOP')"
        ),
        "ck_seller_valuation_comparable_source": (
            "(kind = 'asking' AND listing_id IS NOT NULL AND title IS NOT NULL AND closed_on IS NULL "
            "AND source_label IS NULL AND source_reference IS NULL AND sold_review IS NULL) OR "
            "(kind = 'sold' AND listing_id IS NULL AND title IS NULL AND closed_on IS NOT NULL "
            "AND source_label IS NOT NULL AND source_reference IS NOT NULL "
            "AND (sold_review IS NULL OR jsonb_typeof(sold_review) = 'object'))"
        ),
    },
}


def upgrade():
    # A fresh app database can have these ORM tables before Alembic runs.
    op.execute("""CREATE TABLE IF NOT EXISTS seller_valuation_workspaces (
        lead_id UUID PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
        subject JSONB NOT NULL,
        version INTEGER NOT NULL,
        evidence_version INTEGER NOT NULL,
        review JSONB,
        review_evidence_version INTEGER
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS seller_valuation_comparables (
        id UUID PRIMARY KEY,
        lead_id UUID NOT NULL REFERENCES seller_valuation_workspaces(lead_id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        property_type TEXT NOT NULL,
        location TEXT NOT NULL,
        area_m2 NUMERIC(10, 2),
        bedrooms INTEGER,
        bathrooms NUMERIC(4, 1),
        price NUMERIC(12, 2) NOT NULL,
        currency TEXT NOT NULL,
        captured_at TIMESTAMPTZ NOT NULL,
        listing_id UUID,
        title TEXT,
        closed_on DATE,
        source_label TEXT,
        source_reference TEXT,
        sold_review JSONB
    )""")
    op.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_seller_valuation_asking_listing
        ON seller_valuation_comparables (lead_id, listing_id) WHERE kind = 'asking'""")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_seller_valuation_comparables_lead
        ON seller_valuation_comparables (lead_id, captured_at, id)""")
    op.execute("""CREATE TABLE IF NOT EXISTS seller_valuation_follow_ups (
        id UUID PRIMARY KEY,
        lead_id UUID NOT NULL REFERENCES seller_valuation_workspaces(lead_id) ON DELETE CASCADE,
        follow_up_on DATE NOT NULL,
        note TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ
    )""")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_seller_valuation_follow_ups_lead
        ON seller_valuation_follow_ups (lead_id, follow_up_on, id)""")
    for table, checks in CHECKS.items():
        for name, expression in checks.items():
            op.execute(f"""DO $$ BEGIN
                IF NOT EXISTS (SELECT FROM pg_constraint WHERE conrelid = '{table}'::regclass AND conname = '{name}') THEN
                    ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expression});
                END IF;
            END $$""")
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
