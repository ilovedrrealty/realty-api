"""Private listing location evidence and version history.

Revision ID: b12d41e2c9f0
Revises: 82e1a7c4d9b0
"""
from alembic import op

revision = "b12d41e2c9f0"
down_revision = "82e1a7c4d9b0"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS listing_location_reviews (
        listing_id UUID PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
        version INTEGER NOT NULL DEFAULT 1,
        review_status TEXT NOT NULL DEFAULT 'unreviewed',
        public_precision TEXT NOT NULL DEFAULT 'exact',
        latitude NUMERIC(10,7), longitude NUMERIC(10,7), maps_url TEXT,
        source_type TEXT, source_reference TEXT, review_note TEXT,
        reviewed_by UUID REFERENCES users(id) ON DELETE SET NULL,
        reviewed_at TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_location_review_version CHECK (version>=1),
        CONSTRAINT ck_location_review_status CHECK (review_status IN ('unreviewed','needs_review','verified')),
        CONSTRAINT ck_location_public_precision CHECK (public_precision IN ('exact','area','hidden')),
        CONSTRAINT ck_location_review_coordinates CHECK ((latitude IS NULL AND longitude IS NULL) OR
            (latitude IS NOT NULL AND longitude IS NOT NULL AND latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180))
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_location_review_status ON listing_location_reviews (review_status, listing_id)")
    op.execute("""CREATE TABLE IF NOT EXISTS listing_location_review_revisions (
        listing_id UUID NOT NULL REFERENCES listing_location_reviews(listing_id) ON DELETE CASCADE,
        version INTEGER NOT NULL, action TEXT NOT NULL,
        actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
        snapshot JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(listing_id,version), CONSTRAINT ck_location_revision_version CHECK(version>=1)
    )""")
    for table in ("listing_location_reviews", "listing_location_review_revisions"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON {table} FROM anon; END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN REVOKE ALL ON {table} FROM authenticated; END IF;
        END $$""")
    # Existing coordinates, listings and professional profiles are not backfilled
    # or modified. Legacy points remain explicitly unreviewed until a real review.


def downgrade():
    # Preserve property evidence/history during application rollback.
    pass
