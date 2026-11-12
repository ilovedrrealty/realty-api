"""Store personal service favorites behind the application authorization boundary."""
from alembic import op

revision = "f71c5a9d243e"
down_revision = "a61d9e4b7f20"
branch_labels = None
depends_on = None

TABLE = "network_service_favorites"


def upgrade():
    op.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
        id UUID PRIMARY KEY,
        actor_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        provider_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        saved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_service_favorite_actor_provider UNIQUE (actor_id, provider_id),
        CONSTRAINT ck_service_favorite_distinct_members CHECK (actor_id <> provider_id)
    )""")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_service_favorite_actor_newest ON {TABLE}(actor_id, saved_at, id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_service_favorite_provider ON {TABLE}(provider_id)")
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON {TABLE} FROM PUBLIC")
    for role in ("anon", "authenticated", "service_role"):
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
                REVOKE ALL ON {TABLE} FROM {role};
            END IF;
        END $$""")


def downgrade():
    op.execute(f"DROP TABLE {TABLE}")
