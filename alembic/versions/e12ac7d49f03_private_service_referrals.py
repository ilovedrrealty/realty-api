"""Store private service referrals and their operation receipts."""

from alembic import op


revision = "e12ac7d49f03"
down_revision = "de248ab5c9f3"
branch_labels = None
depends_on = None

TABLES = ("network_service_referrals", "network_service_referral_events")


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS network_service_referrals (
        id UUID PRIMARY KEY,
        request_id UUID NOT NULL REFERENCES network_service_requests(id) ON DELETE RESTRICT,
        proposer_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        provider_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        provider_alias TEXT NOT NULL,
        area_id TEXT NOT NULL,
        language TEXT,
        relationship TEXT NOT NULL,
        status TEXT NOT NULL,
        version INTEGER NOT NULL,
        offered_by_id UUID REFERENCES users(id) ON DELETE RESTRICT,
        recipient_id UUID REFERENCES network_service_recipients(id) ON DELETE RESTRICT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_service_referral_recipient UNIQUE (recipient_id),
        CONSTRAINT ck_service_referral_bounds CHECK (
            char_length(provider_alias) BETWEEN 1 AND 120 AND btrim(provider_alias) <> ''
            AND char_length(area_id) BETWEEN 1 AND 64 AND btrim(area_id) <> ''
            AND (language IS NULL OR (char_length(language) BETWEEN 1 AND 32 AND btrim(language) <> ''))
        ),
        CONSTRAINT ck_service_referral_relationship CHECK (
            relationship IN ('worked_together','know_their_work','no_prior_work')
        ),
        CONSTRAINT ck_service_referral_offer_state CHECK (
            version >= 1 AND ((status = 'proposed' AND offered_by_id IS NULL)
            OR (status IN ('offered','accepted','declined') AND offered_by_id IS NOT NULL) OR status = 'revoked')
        ),
        CONSTRAINT ck_service_referral_recipient_state CHECK ((status = 'accepted') = (recipient_id IS NOT NULL))
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS network_service_referral_events (
        id UUID PRIMARY KEY,
        referral_id UUID NOT NULL REFERENCES network_service_referrals(id) ON DELETE RESTRICT,
        actor_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        submission_id UUID NOT NULL,
        operation TEXT NOT NULL,
        input_sha256 TEXT NOT NULL,
        result_version INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_service_referral_event_submission UNIQUE (actor_id, submission_id),
        CONSTRAINT uq_service_referral_event_version UNIQUE (referral_id, result_version),
        CONSTRAINT ck_service_referral_event_operation CHECK (
            (operation = 'create' AND result_version = 1)
            OR (operation IN ('offer','accept','decline','revoke') AND result_version >= 2)
        ),
        CONSTRAINT ck_service_referral_event_hash CHECK (input_sha256 ~ '^[a-f0-9]{64}$')
    )""")
    op.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_service_referral_active_provider
        ON network_service_referrals (request_id, provider_id) WHERE status IN ('proposed','offered')""")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_service_referral_request
        ON network_service_referrals (request_id, created_at, id)""")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_service_referral_proposer
        ON network_service_referrals (proposer_id, created_at, id)""")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_service_referral_provider
        ON network_service_referrals (provider_id, status, created_at, id)""")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_service_referral_offerer
        ON network_service_referrals (offered_by_id)""")
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
