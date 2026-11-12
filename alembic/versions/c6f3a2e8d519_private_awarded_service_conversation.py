"""Private append-only messages for current awarded service participants."""
from alembic import op

revision = "c6f3a2e8d519"
down_revision = "b7e2c4d6f901"
branch_labels = None
depends_on = None

TABLE = "network_service_conversation_messages"
GUARD = "network_guard_service_conversation_message"


def upgrade():
    op.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
        id UUID PRIMARY KEY,
        request_id UUID NOT NULL REFERENCES network_service_requests(id) ON DELETE RESTRICT,
        sender_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        submission_id UUID NOT NULL,
        sender_role TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_service_conversation_sender_submission UNIQUE (sender_id, submission_id),
        CONSTRAINT ck_service_conversation_sender_role CHECK (sender_role IN ('owner','provider')),
        CONSTRAINT ck_service_conversation_body CHECK (
            char_length(body) BETWEEN 1 AND 2000 AND body = btrim(body, ' ' || chr(9) || chr(10) || chr(13))
            AND translate(body, chr(9) || chr(10) || chr(13), '') COLLATE "C" !~ '[[:cntrl:]]'
        )
    )""")
    op.execute(f"""CREATE INDEX IF NOT EXISTS ix_service_conversation_request_newest
        ON {TABLE}(request_id, created_at, id)""")
    op.execute(f"""CREATE OR REPLACE FUNCTION {GUARD}() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER AS $$ BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Service conversation messages must be retained';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM network_service_requests r
            JOIN network_projects p ON p.id=r.project_id
            JOIN network_service_recipients winner ON winner.request_id=r.id AND winner.status='awarded'
            JOIN users owner_account ON owner_account.id=r.custodian_id
            JOIN users provider_account ON provider_account.id=winner.provider_id
            JOIN network_project_members membership ON membership.project_id=p.id AND membership.user_id=winner.provider_id
            LEFT JOIN network_properties registry ON registry.id=r.property_id
            LEFT JOIN listings listing ON listing.id=r.listing_id
            WHERE r.id=NEW.request_id AND r.status='awarded' AND p.status<>'archived'
              AND p.owner_id=r.custodian_id AND p.property_id IS NOT DISTINCT FROM r.property_id
              AND p.listing_id IS NOT DISTINCT FROM r.listing_id
              AND membership.active IS TRUE AND membership.role='professional'
              AND owner_account.status='active' AND owner_account.email_verified IS TRUE AND owner_account.role<>'admin'
              AND provider_account.status='active' AND provider_account.email_verified IS TRUE AND provider_account.role<>'admin'
              AND ((r.property_id IS NOT NULL AND registry.status='active' AND registry.custodian_id=r.custodian_id
                    AND registry.property_class=r.property_class)
                   OR (r.listing_id IS NOT NULL AND listing.status='active' AND listing.type::text=r.source_listing_type
                       AND (listing.owner_id=r.custodian_id OR (listing.owner_id IS NULL
                            AND listing.submitted_by=r.custodian_id AND owner_account.role='owner'))))
              AND ((NEW.sender_role='owner' AND NEW.sender_id=r.custodian_id)
                   OR (NEW.sender_role='provider' AND NEW.sender_id=winner.provider_id))
        ) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Service conversation requires a current awarded participant';
        END IF;
        RETURN NEW;
        END $$""")
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON {TABLE} FROM PUBLIC")
    op.execute(f"DROP TRIGGER IF EXISTS retain_service_conversation_message ON {TABLE}")
    op.execute(f"""CREATE TRIGGER retain_service_conversation_message BEFORE INSERT OR UPDATE OR DELETE ON {TABLE}
        FOR EACH ROW EXECUTE FUNCTION {GUARD}()""")
    op.execute(f"REVOKE ALL ON FUNCTION {GUARD}() FROM PUBLIC")
    for role in ("anon", "authenticated", "service_role"):
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
                REVOKE ALL ON {TABLE} FROM {role};
                REVOKE ALL ON FUNCTION {GUARD}() FROM {role};
            END IF;
        END $$""")


def downgrade():
    op.execute(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE")
    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM {TABLE}) THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Service conversation messages must be retained';
        END IF;
    END $$""")
    op.execute(f"DROP TABLE {TABLE}")
    op.execute(f"DROP FUNCTION {GUARD}()")
