"""Keep explicitly shared summaries and their consent history private."""
from alembic import op

revision = "e64a91f327bc"
down_revision = "d8f3a7c2e615"
branch_labels = None
depends_on = None

SHARES = "network_history_shares"
ACTIONS = "network_history_share_actions"
GUARD_SHARE = "network_guard_history_share"
GUARD_ACTION = "network_guard_history_share_action"
INVALIDATE = "network_invalidate_history_share_scope"
SCOPES = {
    "network_projects": "AFTER UPDATE OF status",
    "network_properties": "AFTER UPDATE OF status, property_class",
    "listings": "AFTER UPDATE OF status, owner_id, submitted_by, type",
    "users": "AFTER UPDATE OF status, email_verified, role",
}


def upgrade():
    op.execute(f"""CREATE TABLE IF NOT EXISTS {SHARES} (
        id UUID PRIMARY KEY,
        owner_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        recipient_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        property_id UUID REFERENCES network_properties(id) ON DELETE RESTRICT,
        listing_id UUID REFERENCES listings(id) ON DELETE RESTRICT,
        anchor_project_id UUID NOT NULL REFERENCES network_projects(id) ON DELETE RESTRICT,
        member_id UUID NOT NULL REFERENCES network_project_members(id) ON DELETE RESTRICT,
        member_version INTEGER NOT NULL,
        label TEXT NOT NULL,
        items JSONB NOT NULL,
        status TEXT NOT NULL,
        version INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        invalidated_at TIMESTAMPTZ,
        CONSTRAINT ck_history_share_scope CHECK ((property_id IS NULL) <> (listing_id IS NULL) AND owner_id <> recipient_id),
        CONSTRAINT ck_history_share_content CHECK (char_length(label) BETWEEN 1 AND 120 AND jsonb_typeof(items)='array'
            AND jsonb_array_length(items) BETWEEN 1 AND 25 AND member_version >= 1),
        CONSTRAINT ck_history_share_expiry CHECK (expires_at-created_at IN
            (interval '24 hours',interval '168 hours',interval '336 hours',interval '720 hours')),
        CONSTRAINT ck_history_share_state CHECK ((status='pending' AND version=1)
            OR (status IN ('accepted','declined') AND version=2) OR (status='revoked' AND version IN (2,3)))
    )""")
    op.execute(f"""CREATE TABLE IF NOT EXISTS {ACTIONS} (
        actor_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        submission_id UUID NOT NULL,
        share_id UUID NOT NULL REFERENCES {SHARES}(id) ON DELETE RESTRICT,
        operation TEXT NOT NULL,
        status TEXT NOT NULL,
        version INTEGER NOT NULL,
        creation_sha256 TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (actor_id, submission_id),
        CONSTRAINT ck_history_share_action_digest CHECK (creation_sha256 ~ '^[0-9a-f]{{64}}$'),
        CONSTRAINT ck_history_share_action_state CHECK ((operation='create' AND status='pending' AND version=1)
            OR (operation='accept' AND status='accepted' AND version=2) OR (operation='decline' AND status='declined' AND version=2)
            OR (operation='revoke' AND status='revoked' AND version IN (2,3)))
    )""")
    for name, columns in {"owner": "owner_id, created_at, id", "recipient": "recipient_id, created_at, id",
                          "registry": "property_id", "listing": "listing_id", "anchor": "anchor_project_id"}.items():
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_history_share_{name} ON {SHARES} ({columns})")
    for table in (SHARES, ACTIONS):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        for role in ("anon", "authenticated", "service_role"):
            op.execute(f"""DO $$ BEGIN
                IF EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') THEN REVOKE ALL ON {table} FROM {role}; END IF;
            END $$""")

    op.execute(f"""CREATE OR REPLACE FUNCTION {GUARD_SHARE}() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='History share consent must be retained';
        ELSIF TG_OP='INSERT' THEN
            IF NEW.status<>'pending' OR NEW.version<>1 OR NEW.invalidated_at IS NOT NULL
                OR NOT EXISTS (SELECT FROM network_projects p JOIN network_project_members m ON m.project_id=p.id
                    WHERE p.id=NEW.anchor_project_id AND p.owner_id=NEW.owner_id AND p.status IN ('open','in_progress')
                    AND p.property_id IS NOT DISTINCT FROM NEW.property_id AND p.listing_id IS NOT DISTINCT FROM NEW.listing_id
                    AND m.id=NEW.member_id AND m.user_id=NEW.recipient_id AND m.active IS TRUE
                    AND m.role='professional' AND m.reminder_grant_version=NEW.member_version)
                OR EXISTS (SELECT FROM jsonb_array_elements(NEW.items) AS entry(value)
                    LEFT JOIN network_project_updates u ON u.id=(entry.value->>'update_id')::uuid
                    LEFT JOIN network_projects p ON p.id=u.project_id
                    WHERE jsonb_typeof(entry.value) IS DISTINCT FROM 'object' OR u.id IS NULL
                    OR p.owner_id IS DISTINCT FROM NEW.owner_id OR p.status IS DISTINCT FROM 'completed'
                    OR p.property_id IS DISTINCT FROM NEW.property_id OR p.listing_id IS DISTINCT FROM NEW.listing_id
                    OR u.kind NOT IN ('progress','correction') OR entry.value->>'occurred_on' IS DISTINCT FROM to_char(u.occurred_on,'YYYY-MM-DD')
                    OR entry.value->'service_category_ids' IS DISTINCT FROM p.service_category_ids
                    OR jsonb_typeof(entry.value->'summary') IS DISTINCT FROM 'string'
                    OR char_length(entry.value->>'summary') NOT BETWEEN 1 AND 600
                    OR entry.value - ARRAY['update_id','occurred_on','service_category_ids','summary'] <> '{{}}'::jsonb)
                OR (SELECT count(DISTINCT entry.value->>'update_id') FROM jsonb_array_elements(NEW.items) AS entry(value))
                    <> jsonb_array_length(NEW.items) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Invalid private history share scope';
            END IF;
        ELSE
            IF to_jsonb(NEW) - ARRAY['status','version','invalidated_at']
                IS DISTINCT FROM to_jsonb(OLD) - ARRAY['status','version','invalidated_at'] THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Shared history evidence is immutable';
            END IF;
            IF OLD.invalidated_at IS NULL AND NEW.invalidated_at IS NOT NULL
                AND NEW.status=OLD.status AND NEW.version=OLD.version THEN
                RETURN NEW;
            END IF;
            IF OLD.invalidated_at IS NOT NULL OR NEW.invalidated_at IS NOT NULL OR NEW.version<>OLD.version+1
                OR NOT ((OLD.status='pending' AND NEW.status IN ('accepted','declined','revoked'))
                     OR (OLD.status='accepted' AND NEW.status='revoked')) THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Invalid history share transition';
            END IF;
        END IF;
        RETURN NEW;
    END $$""")
    op.execute(f"""CREATE OR REPLACE FUNCTION {GUARD_ACTION}() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP<>'INSERT' THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='History share receipts must be retained';
        END IF;
        IF NOT EXISTS (SELECT FROM {SHARES} s WHERE s.id=NEW.share_id AND s.version=NEW.version AND s.status=NEW.status
            AND s.invalidated_at IS NULL AND ((NEW.operation='create' AND NEW.actor_id=s.owner_id)
            OR (NEW.operation IN ('accept','decline') AND NEW.actor_id=s.recipient_id)
            OR (NEW.operation='revoke' AND NEW.actor_id IN (s.owner_id,s.recipient_id)))) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Invalid private history share receipt';
        END IF;
        RETURN NEW;
    END $$""")
    for table, guard in ((SHARES, GUARD_SHARE), (ACTIONS, GUARD_ACTION)):
        op.execute(f"DROP TRIGGER IF EXISTS history_share_guard ON {table}")
        op.execute(f"CREATE TRIGGER history_share_guard BEFORE INSERT OR UPDATE OR DELETE ON {table} "
                   f"FOR EACH ROW EXECUTE FUNCTION {guard}()")

    op.execute(f"""CREATE OR REPLACE FUNCTION {INVALIDATE}() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_TABLE_NAME='network_projects' THEN
            IF OLD.status IN ('open','in_progress') AND NEW.status NOT IN ('open','in_progress') THEN
                UPDATE {SHARES} SET invalidated_at=statement_timestamp()
                WHERE anchor_project_id=NEW.id AND invalidated_at IS NULL AND status IN ('pending','accepted');
            END IF;
        ELSIF TG_TABLE_NAME='network_properties' THEN
            IF OLD.status='active' AND (NEW.status<>'active' OR NEW.property_class IS DISTINCT FROM OLD.property_class) THEN
                UPDATE {SHARES} SET invalidated_at=statement_timestamp()
                WHERE property_id=NEW.id AND invalidated_at IS NULL AND status IN ('pending','accepted');
            END IF;
        ELSIF TG_TABLE_NAME='listings' THEN
            IF OLD.status='active' AND (NEW.status<>'active' OR NEW.type IS DISTINCT FROM OLD.type
                OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
                OR (NEW.owner_id IS NULL AND NEW.submitted_by IS DISTINCT FROM OLD.submitted_by)) THEN
                UPDATE {SHARES} SET invalidated_at=statement_timestamp()
                WHERE listing_id=NEW.id AND invalidated_at IS NULL AND status IN ('pending','accepted');
            END IF;
        ELSIF OLD.status IS DISTINCT FROM NEW.status OR OLD.email_verified IS DISTINCT FROM NEW.email_verified
            OR OLD.role IS DISTINCT FROM NEW.role THEN
            UPDATE {SHARES} s SET invalidated_at=statement_timestamp()
            WHERE s.invalidated_at IS NULL AND s.status IN ('pending','accepted')
            AND ((NEW.status<>'active' OR NEW.email_verified IS NOT TRUE OR NEW.role='admin')
                 AND NEW.id IN (s.owner_id,s.recipient_id)
                 OR NEW.id=s.owner_id AND EXISTS (SELECT FROM listings l WHERE l.id=s.listing_id
                     AND (l.owner_id IS NULL AND NEW.role<>'owner' OR l.owner_id IS NOT NULL AND NEW.role NOT IN ('owner','realtor'))));
        END IF;
        RETURN NEW;
    END $$""")
    for table, event in SCOPES.items():
        op.execute(f"DROP TRIGGER IF EXISTS history_share_scope_changed ON {table}")
        op.execute(f"CREATE TRIGGER history_share_scope_changed {event} ON {table} "
                   f"FOR EACH ROW EXECUTE FUNCTION {INVALIDATE}()")
    for function in (GUARD_ACTION, GUARD_SHARE, INVALIDATE):
        op.execute(f"REVOKE ALL ON FUNCTION {function}() FROM PUBLIC")
        for role in ("anon", "authenticated", "service_role"):
            op.execute(f"""DO $$ BEGIN
                IF EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') THEN
                    REVOKE ALL ON FUNCTION {function}() FROM {role};
                END IF;
            END $$""")


def downgrade():
    op.execute(f"LOCK TABLE users, listings, network_properties, network_projects, {SHARES}, {ACTIONS} IN ACCESS EXCLUSIVE MODE")
    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT FROM {SHARES}) OR EXISTS (SELECT FROM {ACTIONS}) THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='History shares and consent receipts must be retained';
        END IF;
    END $$""")
    for table in SCOPES:
        op.execute(f"DROP TRIGGER history_share_scope_changed ON {table}")
    op.execute(f"DROP TABLE {ACTIONS}")
    op.execute(f"DROP TABLE {SHARES}")
    for function in (GUARD_ACTION, GUARD_SHARE, INVALIDATE):
        op.execute(f"DROP FUNCTION {function}()")
