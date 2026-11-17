"""Keep prospective listing views and explicitly shared performance records private."""

from alembic import op


revision = "5a4e7f921bc0"
down_revision = "17d8b2c40aa6"
branch_labels = None
depends_on = None

TABLES = ("listing_daily_views", "listing_view_coverage", "listing_performance_entries")
CHECKS = {
    "listing_daily_views": {"ck_listing_daily_view_count": "view_count > 0"},
    "listing_view_coverage": {"ck_listing_view_coverage_singleton": "id = 1"},
    "listing_performance_entries": {
        "ck_listing_performance_entry_kind": "kind IN ('completed_showing', 'owner_feedback', 'marketing_update')",
        "ck_listing_performance_entry_text": (
            "(kind = 'completed_showing' AND text IS NULL) OR "
            "(kind <> 'completed_showing' AND text IS NOT NULL AND char_length(btrim(text)) BETWEEN 1 AND 1000)"
        ),
    },
}


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS listing_daily_views (
        listing_id UUID NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
        view_date DATE NOT NULL,
        view_count BIGINT NOT NULL,
        PRIMARY KEY (listing_id, view_date)
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS listing_view_coverage (
        id SMALLINT PRIMARY KEY,
        started_at TIMESTAMPTZ NOT NULL
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS listing_performance_entries (
        listing_id UUID NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
        id UUID NOT NULL,
        kind TEXT NOT NULL,
        occurred_on DATE NOT NULL,
        text TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by UUID REFERENCES users(id) ON DELETE SET NULL,
        PRIMARY KEY (listing_id, id)
    )""")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_listing_performance_entries_date
        ON listing_performance_entries (listing_id, occurred_on DESC, id)""")
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
