"""Store newsletter consent separately from users and existing leads."""

from alembic import op

revision = "f92c8a74d31e"
down_revision = "a4e72d1c718b"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS newsletter_subscriptions (
        id UUID PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        language TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        consent_at TIMESTAMPTZ NOT NULL,
        confirmation_nonce UUID,
        confirmation_expires_at TIMESTAMPTZ,
        last_attempt_at TIMESTAMPTZ,
        provider_accepted_at TIMESTAMPTZ,
        subscribed_at TIMESTAMPTZ,
        unsubscribed_at TIMESTAMPTZ,
        CONSTRAINT ck_newsletter_locale CHECK (language IN ('en','es')),
        CONSTRAINT ck_newsletter_status CHECK (status IN ('pending','subscribed','unsubscribed'))
    )""")
    op.execute("ALTER TABLE newsletter_subscriptions ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON TABLE newsletter_subscriptions FROM PUBLIC")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
            REVOKE ALL ON TABLE newsletter_subscriptions FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
            REVOKE ALL ON TABLE newsletter_subscriptions FROM authenticated;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN
            REVOKE ALL ON TABLE newsletter_subscriptions FROM service_role;
        END IF;
    END $$""")


def downgrade():
    # Retain consent and opt-out records across an application rollback.
    pass
