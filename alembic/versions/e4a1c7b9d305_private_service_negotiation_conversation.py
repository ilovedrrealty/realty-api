"""Keep private negotiation messages in their exact pre-award recipient scope."""
from alembic import op

revision = "e4a1c7b9d305"
down_revision = "c6f3a2e8d519"
branch_labels = None
depends_on = None

TABLE = "network_service_negotiation_messages"
AWARDED_TABLE = "network_service_conversation_messages"
GUARD = "network_guard_service_negotiation_message"


def upgrade():
    op.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
        id UUID PRIMARY KEY,
        request_id UUID NOT NULL REFERENCES network_service_requests(id) ON DELETE RESTRICT,
        recipient_id UUID NOT NULL REFERENCES network_service_recipients(id) ON DELETE RESTRICT,
        sender_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        submission_id UUID NOT NULL,
        sender_role TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_service_negotiation_sender_submission UNIQUE (sender_id, submission_id),
        CONSTRAINT ck_service_negotiation_sender_role CHECK (sender_role IN ('owner','provider')),
        CONSTRAINT ck_service_negotiation_body CHECK (
            char_length(body) BETWEEN 1 AND 2000 AND body = btrim(body, ' ' || chr(9) || chr(10) || chr(13))
            AND translate(body, chr(9) || chr(10) || chr(13), '') COLLATE "C" !~ '[[:cntrl:]]'
        )
    )""")
    op.execute(f"""CREATE INDEX IF NOT EXISTS ix_service_negotiation_thread_newest
        ON {TABLE}(request_id, recipient_id, created_at, id)""")
    op.execute(f"""CREATE OR REPLACE FUNCTION {GUARD}() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER AS $$ BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Service negotiation messages must be retained';
        END IF;
        -- Share the application writer's existing per-sender lock across both message tables.
        PERFORM 1 FROM users WHERE id=NEW.sender_id FOR NO KEY UPDATE;
        IF TG_TABLE_NAME='{AWARDED_TABLE}' THEN
            IF EXISTS (SELECT 1 FROM {TABLE} WHERE sender_id=NEW.sender_id AND submission_id=NEW.submission_id) THEN
                RAISE EXCEPTION USING ERRCODE='23505', MESSAGE='Conversation submission is already in use';
            END IF;
            RETURN NEW;
        END IF;
        IF EXISTS (SELECT 1 FROM {AWARDED_TABLE} WHERE sender_id=NEW.sender_id AND submission_id=NEW.submission_id) THEN
            RAISE EXCEPTION USING ERRCODE='23505', MESSAGE='Conversation submission is already in use';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM network_service_requests r
            JOIN network_service_recipients recipient ON recipient.id=NEW.recipient_id AND recipient.request_id=r.id
            JOIN users owner_account ON owner_account.id=r.custodian_id
            JOIN users provider_account ON provider_account.id=recipient.provider_id
            JOIN network_service_preferences preference ON preference.user_id=recipient.provider_id
            LEFT JOIN network_properties registry ON registry.id=r.property_id
            LEFT JOIN listings listing ON listing.id=r.listing_id
            WHERE r.id=NEW.request_id AND r.status='open' AND r.project_id IS NULL AND recipient.status='accepted'
              AND owner_account.status='active' AND owner_account.email_verified IS TRUE AND owner_account.role<>'admin'
              AND provider_account.status='active' AND provider_account.email_verified IS TRUE AND provider_account.role<>'admin'
              AND provider_account.user_code>0 AND preference.service_opt_in IS TRUE
              AND preference.category_ids ? r.category_id AND preference.property_class_ids ? r.property_class
              AND ((r.property_id IS NOT NULL AND registry.status='active' AND registry.custodian_id=r.custodian_id
                    AND registry.property_class=r.property_class)
                   OR (r.listing_id IS NOT NULL AND listing.status='active' AND listing.type::text=r.source_listing_type
                       AND ((listing.owner_id=r.custodian_id AND owner_account.role IN ('owner','realtor'))
                            OR (listing.owner_id IS NULL AND listing.submitted_by=r.custodian_id AND owner_account.role='owner'))))
              AND ((NEW.sender_role='owner' AND NEW.sender_id=r.custodian_id)
                   OR (NEW.sender_role='provider' AND NEW.sender_id=recipient.provider_id))
        ) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Service negotiation requires a current accepted participant';
        END IF;
        RETURN NEW;
        END $$""")
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON {TABLE} FROM PUBLIC")
    op.execute(f"DROP TRIGGER IF EXISTS retain_service_negotiation_message ON {TABLE}")
    op.execute(f"""CREATE TRIGGER retain_service_negotiation_message BEFORE INSERT OR UPDATE OR DELETE ON {TABLE}
        FOR EACH ROW EXECUTE FUNCTION {GUARD}()""")
    op.execute(f"DROP TRIGGER IF EXISTS scope_service_conversation_submission ON {AWARDED_TABLE}")
    op.execute(f"""CREATE TRIGGER scope_service_conversation_submission BEFORE INSERT ON {AWARDED_TABLE}
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
    op.execute(f"LOCK TABLE {AWARDED_TABLE}, {TABLE} IN ACCESS EXCLUSIVE MODE")
    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM {TABLE}) THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Service negotiation messages must be retained';
        END IF;
    END $$""")
    op.execute(f"DROP TRIGGER scope_service_conversation_submission ON {AWARDED_TABLE}")
    op.execute(f"DROP TABLE {TABLE}")
    op.execute(f"DROP FUNCTION {GUARD}()")
