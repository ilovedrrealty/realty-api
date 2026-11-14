"""Track actual private registry custody without moving prior work or publishing records."""
from alembic import op

revision = "f4b27d6e913c"
down_revision = "e64a91f327bc"
branch_labels = None
depends_on = None

TRANSFERS = "network_property_custody_transfers"
ACTIONS = "network_property_custody_actions"
PROPERTIES = "network_properties"
PROJECTS = "network_projects"
REQUESTS = "network_service_requests"
MAINTENANCE = "network_maintenance_preferences"


def _constraint(table, name, expression):
    op.execute(f"""DO $$ BEGIN
        IF NOT EXISTS (SELECT FROM pg_constraint WHERE conrelid='{table}'::regclass AND conname='{name}') THEN
            ALTER TABLE {table} ADD CONSTRAINT {name} {expression};
        END IF;
    END $$""")


def _private(kind, name):
    op.execute(f"REVOKE ALL ON {kind} {name} FROM PUBLIC")
    for role in ("anon", "authenticated", "service_role"):
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') THEN REVOKE ALL ON {kind} {name} FROM {role}; END IF;
        END $$""")


def _trigger(table, name, event, function):
    op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    op.execute(f"CREATE TRIGGER {name} {event} ON {table} FOR EACH ROW EXECUTE FUNCTION {function}()")


def _existing_schema():
    op.execute(f"ALTER TABLE {PROPERTIES} ADD COLUMN IF NOT EXISTS current_custodian_id UUID")
    op.execute(f"ALTER TABLE {PROPERTIES} ADD COLUMN IF NOT EXISTS custody_version INTEGER NOT NULL DEFAULT 1")
    op.execute(f"UPDATE {PROPERTIES} SET current_custodian_id=custodian_id WHERE current_custodian_id IS NULL")
    op.execute(f"ALTER TABLE {PROPERTIES} ALTER COLUMN current_custodian_id SET NOT NULL")
    _constraint(PROPERTIES, "network_properties_current_custodian_id_fkey",
                "FOREIGN KEY (current_custodian_id) REFERENCES users(id) ON DELETE RESTRICT")
    _constraint(PROPERTIES, "ck_network_property_custody_version", "CHECK (custody_version >= 1)")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_network_property_current_custodian ON {PROPERTIES}(current_custodian_id,updated_at,id)")
    op.execute(f"ALTER TABLE {PROJECTS} ADD COLUMN IF NOT EXISTS closed_custody_title TEXT")
    _constraint(PROJECTS, "ck_network_project_closed_custody_title", "CHECK (closed_custody_title IS NULL OR "
                "(property_id IS NOT NULL AND char_length(closed_custody_title) BETWEEN 1 AND 180))")
    op.execute(f"ALTER TABLE {REQUESTS} ADD COLUMN IF NOT EXISTS custody_closed BOOLEAN NOT NULL DEFAULT false")
    _constraint(REQUESTS, "ck_service_request_closed_custody", "CHECK (NOT custody_closed OR property_id IS NOT NULL)")


def upgrade_maintenance_preferences():
    op.execute(f"ALTER TABLE {MAINTENANCE} ADD COLUMN IF NOT EXISTS custody_version INTEGER")
    op.execute(f"UPDATE {MAINTENANCE} SET custody_version=1 WHERE property_id IS NOT NULL AND custody_version IS NULL")
    op.execute(f"ALTER TABLE {MAINTENANCE} DROP CONSTRAINT IF EXISTS uq_maintenance_preference_registry")
    _constraint(MAINTENANCE, "uq_maintenance_preference_registry_custody", "UNIQUE (actor_id,property_id,custody_version)")
    _constraint(MAINTENANCE, "ck_maintenance_preference_custody", "CHECK ((property_id IS NULL AND custody_version IS NULL) OR "
                "(property_id IS NOT NULL AND custody_version IS NOT NULL AND custody_version >= 1))")


def _transfer_tables():
    op.execute(f"""CREATE TABLE IF NOT EXISTS {TRANSFERS} (
        id UUID PRIMARY KEY,
        property_id UUID NOT NULL REFERENCES {PROPERTIES}(id) ON DELETE RESTRICT,
        offered_by_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        recipient_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        property_version INTEGER NOT NULL,
        custody_version INTEGER NOT NULL,
        label TEXT NOT NULL,
        fields JSONB NOT NULL,
        prior_notes TEXT NOT NULL,
        status TEXT NOT NULL,
        version INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        finished_at TIMESTAMPTZ,
        CONSTRAINT ck_property_custody_actor_scope CHECK (offered_by_id <> recipient_id AND property_version >= 1 AND custody_version >= 1),
        CONSTRAINT ck_property_custody_content CHECK (char_length(label) BETWEEN 1 AND 120 AND jsonb_typeof(fields)='object'
            AND char_length(prior_notes) <= 4000),
        CONSTRAINT ck_property_custody_times CHECK (expires_at-created_at = interval '168 hours'
            AND (finished_at IS NULL OR finished_at >= created_at)),
        CONSTRAINT ck_property_custody_state CHECK ((status='pending' AND version=1 AND finished_at IS NULL) OR
            (status IN ('accepted','declined','cancelled','expired') AND version=2 AND finished_at IS NOT NULL))
    )""")
    op.execute(f"""CREATE TABLE IF NOT EXISTS {ACTIONS} (
        actor_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        submission_id UUID NOT NULL,
        transfer_id UUID NOT NULL REFERENCES {TRANSFERS}(id) ON DELETE RESTRICT,
        operation TEXT NOT NULL,
        status TEXT NOT NULL,
        version INTEGER NOT NULL,
        creation_sha256 TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(actor_id,submission_id),
        CONSTRAINT ck_property_custody_action_digest CHECK (creation_sha256 ~ '^[0-9a-f]{{64}}$'),
        CONSTRAINT ck_property_custody_action_state CHECK ((operation='offer' AND status='pending' AND version=1) OR
            (version=2 AND (operation,status) IN (('accept','accepted'),('decline','declined'),('cancel','cancelled'))))
    )""")
    op.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_property_custody_pending_source ON {TRANSFERS}(property_id) WHERE status='pending'")
    op.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_property_custody_accepted_generation "
               f"ON {TRANSFERS}(property_id,custody_version) WHERE status='accepted'")
    for name, column in (("offerer", "offered_by_id"), ("recipient", "recipient_id")):
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_property_custody_{name} ON {TRANSFERS}({column},created_at,id)")
    for table in (TRANSFERS, ACTIONS):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        _private("TABLE", table)


def _service_identity(custody):
    # Preserve the previously reviewed identity guard on all other fields and
    # tables. The sole exception is the permanent private-custody close bit.
    addition = f"""
        IF TG_TABLE_NAME='network_service_requests' THEN
            IF OLD.custody_closed THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Former-custody service request is retained';
            ELSIF NEW.custody_closed THEN
                IF (to_jsonb(NEW)-'custody_closed') IS DISTINCT FROM (to_jsonb(OLD)-'custody_closed')
                    OR NOT EXISTS (SELECT FROM {PROPERTIES} p JOIN {TRANSFERS} t ON t.property_id=p.id
                        WHERE p.id=OLD.property_id AND p.custody_version=t.custody_version+1
                        AND p.current_custodian_id=t.recipient_id AND t.offered_by_id=OLD.custodian_id AND t.status='accepted') THEN
                    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Private custody proof is required to close a service request';
                END IF;
                RETURN NEW;
            END IF;
        END IF;
    """ if custody else ""
    op.execute(f"""CREATE OR REPLACE FUNCTION network_guard_service_identity() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER AS $$ DECLARE mutable TEXT[]; BEGIN
        IF TG_OP='DELETE' OR TG_TABLE_NAME='network_service_actions' THEN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Service agreements and action identities are retained';
        END IF;
        {addition}
        mutable := CASE TG_TABLE_NAME
            WHEN 'network_service_preferences' THEN ARRAY['service_opt_in','member_alias','category_ids','property_class_ids','version','updated_at']
            WHEN 'network_service_requests' THEN ARRAY['status','version','project_id','updated_at']
            WHEN 'network_service_recipients' THEN ARRAY['status','version','current_proposal_id']
            WHEN 'network_service_proposals' THEN ARRAY['status','version'] END;
        IF mutable IS NULL OR (to_jsonb(NEW)-mutable) IS DISTINCT FROM (to_jsonb(OLD)-mutable) OR NEW.version<>OLD.version+1 THEN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Service original identity and terms are immutable';
        END IF;
        IF TG_TABLE_NAME='network_service_requests' THEN
            IF OLD.status<>'open' OR (OLD.project_id IS NOT NULL AND NEW.project_id IS DISTINCT FROM OLD.project_id) THEN
                RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Closed service request is retained';
            END IF;
        ELSIF TG_TABLE_NAME='network_service_recipients' THEN
            IF OLD.status IN ('declined','revoked','closed','awarded') THEN
                RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Closed service recipient is retained';
            END IF;
        ELSIF TG_TABLE_NAME='network_service_proposals' THEN
            IF OLD.status NOT IN ('submitted','accepted') THEN
                RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Closed service proposal is retained';
            END IF;
        END IF;
        RETURN NEW;
        END $$""")


def _custody_guards():
    op.execute(f"""CREATE OR REPLACE FUNCTION network_guard_custody_transfer() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE source RECORD; BEGIN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Property custody consent is retained';
        ELSIF TG_OP='INSERT' THEN
            SELECT * INTO source FROM {PROPERTIES} WHERE id=NEW.property_id;
            IF NOT FOUND OR NEW.status<>'pending' OR source.status<>'active' OR source.version<>NEW.property_version
                OR source.custody_version<>NEW.custody_version OR source.current_custodian_id<>NEW.offered_by_id
                OR NEW.prior_notes IS DISTINCT FROM source.notes OR NEW.fields IS DISTINCT FROM
                    jsonb_build_object('title',source.title,'location',source.location,'address',source.address,
                        'property_class',source.property_class,'relationship',source.relationship)
                OR (SELECT count(*) FROM users WHERE id IN (NEW.offered_by_id,NEW.recipient_id)
                    AND status='active' AND email_verified IS TRUE AND role<>'admin') <> 2 THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Invalid property custody offer scope';
            END IF;
        ELSE
            IF OLD.status<>'pending' OR NEW.status='pending' OR
                to_jsonb(NEW)-ARRAY['status','version','finished_at'] IS DISTINCT FROM to_jsonb(OLD)-ARRAY['status','version','finished_at']
                OR ((NEW.status='expired') IS DISTINCT FROM (NEW.finished_at>=OLD.expires_at)) THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Invalid property custody transition';
            END IF;
        END IF;
        RETURN NEW;
    END $$""")
    op.execute(f"""CREATE OR REPLACE FUNCTION network_guard_custody_receipt() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP<>'INSERT' THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Property custody receipts are retained';
        END IF;
        IF NOT EXISTS (SELECT FROM {TRANSFERS} t JOIN {PROPERTIES} p ON p.id=t.property_id
            WHERE t.id=NEW.transfer_id AND t.status=NEW.status AND t.version=NEW.version
            AND ((NEW.operation IN ('offer','cancel') AND NEW.actor_id=t.offered_by_id)
                OR (NEW.operation IN ('accept','decline') AND NEW.actor_id=t.recipient_id))
            AND (NEW.operation<>'accept' OR (p.current_custodian_id=t.recipient_id AND p.custody_version=t.custody_version+1))) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Property custody receipt scope mismatch';
        END IF;
        RETURN NEW;
    END $$""")
    op.execute(f"""CREATE OR REPLACE FUNCTION network_verify_accepted_registry_custody() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.status='accepted' AND (NOT EXISTS (SELECT FROM {PROPERTIES} p WHERE p.id=NEW.property_id
            AND p.current_custodian_id=NEW.recipient_id AND p.custody_version=NEW.custody_version+1)
            OR NOT EXISTS (SELECT FROM {ACTIONS} a WHERE a.transfer_id=NEW.id AND a.actor_id=NEW.recipient_id
                AND a.operation='accept' AND a.status='accepted' AND a.version=2)) THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Accepted custody requires its property transition and recipient receipt';
        END IF;
        RETURN NEW;
    END $$""")
    op.execute(f"""CREATE OR REPLACE FUNCTION network_guard_registry_custody() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP='INSERT' THEN
            IF NEW.current_custodian_id IS NULL THEN NEW.current_custodian_id:=NEW.custodian_id; END IF;
            IF NEW.current_custodian_id<>NEW.custodian_id OR NEW.custody_version<>1 THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='A new private record starts with its creating custodian';
            END IF;
        ELSIF (OLD.current_custodian_id,OLD.custody_version) IS DISTINCT FROM (NEW.current_custodian_id,NEW.custody_version) THEN
            IF OLD.current_custodian_id=NEW.current_custodian_id OR NEW.custody_version<>OLD.custody_version+1
                OR NEW.version<>OLD.version+1 OR NEW.notes<>'' OR OLD.status<>'active'
                OR to_jsonb(NEW)-ARRAY['current_custodian_id','custody_version','version','notes','updated_at']
                    IS DISTINCT FROM to_jsonb(OLD)-ARRAY['current_custodian_id','custody_version','version','notes','updated_at']
                OR NOT EXISTS (SELECT FROM {TRANSFERS} t WHERE t.property_id=OLD.id AND t.status='accepted'
                    AND t.offered_by_id=OLD.current_custodian_id AND t.recipient_id=NEW.current_custodian_id
                    AND t.custody_version=OLD.custody_version AND t.property_version=OLD.version
                    AND t.prior_notes IS NOT DISTINCT FROM OLD.notes AND t.finished_at=NEW.updated_at)
                OR (SELECT count(*) FROM users WHERE id IN (OLD.current_custodian_id,NEW.current_custodian_id)
                    AND status='active' AND email_verified IS TRUE AND role<>'admin') <> 2
                OR EXISTS (SELECT FROM {PROJECTS} p WHERE p.property_id=OLD.id AND p.closed_custody_title IS NULL
                    AND p.status NOT IN ('completed','archived'))
                OR EXISTS (SELECT FROM {REQUESTS} r LEFT JOIN {PROJECTS} p ON p.id=r.project_id
                    LEFT JOIN network_service_work w ON w.request_id=r.id WHERE r.property_id=OLD.id AND NOT r.custody_closed
                    AND (r.status='open' OR r.status='awarded' AND (p.property_id IS DISTINCT FROM OLD.id
                        OR coalesce(p.status,'') NOT IN ('completed','archived')
                        OR coalesce(w.status,'') NOT IN ('completed','cancelled')))) THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Current custody can change only after an exact accepted transfer';
            END IF;
        END IF;
        RETURN NEW;
    END $$""")
    op.execute(f"""CREATE OR REPLACE FUNCTION network_guard_custody_project() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF OLD.closed_custody_title IS NOT NULL THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Historical-custody project is retained';
        ELSIF NEW.closed_custody_title IS NOT NULL AND
            ((to_jsonb(NEW)-'closed_custody_title') IS DISTINCT FROM (to_jsonb(OLD)-'closed_custody_title')
                OR NOT EXISTS (SELECT FROM {PROPERTIES} p JOIN {TRANSFERS} t ON t.property_id=p.id
                    WHERE p.id=OLD.property_id AND p.custody_version=t.custody_version+1 AND p.current_custodian_id=t.recipient_id
                    AND t.offered_by_id=OLD.owner_id AND t.status='accepted' AND t.fields->>'title'=NEW.closed_custody_title)) THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Exact accepted custody required to close a project';
        END IF;
        RETURN NEW;
    END $$""")
    op.execute(f"""CREATE OR REPLACE FUNCTION network_close_registry_custody() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        UPDATE {PROJECTS} SET closed_custody_title=OLD.title WHERE property_id=OLD.id AND closed_custody_title IS NULL;
        UPDATE {REQUESTS} SET custody_closed=true WHERE property_id=OLD.id AND NOT custody_closed;
        UPDATE network_history_shares SET invalidated_at=NEW.updated_at WHERE property_id=OLD.id
            AND invalidated_at IS NULL AND status IN ('pending','accepted');
        RETURN NEW;
    END $$""")
    for table, name, event, function in (
        (TRANSFERS,"custody_transfer_guard","BEFORE INSERT OR UPDATE OR DELETE","network_guard_custody_transfer"),
        (ACTIONS,"custody_receipt_guard","BEFORE INSERT OR UPDATE OR DELETE","network_guard_custody_receipt"),
        (PROPERTIES,"registry_custody_guard","BEFORE INSERT OR UPDATE","network_guard_registry_custody"),
        (PROJECTS,"custody_project_guard","BEFORE UPDATE","network_guard_custody_project"),
    ):
        _trigger(table, name, event, function)
    op.execute(f"DROP TRIGGER IF EXISTS registry_custody_closed ON {PROPERTIES}")
    op.execute(f"""CREATE TRIGGER registry_custody_closed AFTER UPDATE OF current_custodian_id ON {PROPERTIES}
        FOR EACH ROW WHEN (OLD.current_custodian_id IS DISTINCT FROM NEW.current_custodian_id)
        EXECUTE FUNCTION network_close_registry_custody()""")
    op.execute(f"DROP TRIGGER IF EXISTS custody_accepted_consistency ON {TRANSFERS}")
    op.execute(f"""CREATE CONSTRAINT TRIGGER custody_accepted_consistency AFTER UPDATE ON {TRANSFERS}
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION network_verify_accepted_registry_custody()""")
    for function in ("network_guard_custody_transfer", "network_guard_custody_receipt", "network_guard_registry_custody",
                     "network_guard_custody_project", "network_close_registry_custody", "network_verify_accepted_registry_custody"):
        _private("FUNCTION", function + "()")


def upgrade():
    _existing_schema()
    upgrade_maintenance_preferences()
    _transfer_tables()
    _service_identity(True)
    _custody_guards()


def downgrade():
    op.execute(f"LOCK TABLE {PROPERTIES},{PROJECTS},{REQUESTS},{MAINTENANCE},{TRANSFERS},{ACTIONS} IN ACCESS EXCLUSIVE MODE")
    op.execute(f"""DO $$ BEGIN IF EXISTS (SELECT FROM {TRANSFERS}) OR EXISTS (SELECT FROM {ACTIONS}) THEN
        RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Private property custody lineage and consent must be retained';
    END IF; END $$""")
    op.execute(f"DROP TRIGGER registry_custody_guard ON {PROPERTIES}")
    op.execute(f"DROP TRIGGER registry_custody_closed ON {PROPERTIES}")
    op.execute(f"DROP TRIGGER custody_project_guard ON {PROJECTS}")
    _service_identity(False)
    op.execute(f"DROP TABLE {ACTIONS}")
    op.execute(f"DROP TABLE {TRANSFERS}")
    for function in ("network_guard_custody_transfer", "network_guard_custody_receipt", "network_guard_registry_custody",
                     "network_guard_custody_project", "network_close_registry_custody", "network_verify_accepted_registry_custody"):
        op.execute(f"DROP FUNCTION {function}()")
    op.execute(f"ALTER TABLE {MAINTENANCE} DROP COLUMN custody_version")
    _constraint(MAINTENANCE, "uq_maintenance_preference_registry", "UNIQUE (actor_id,property_id)")
    op.execute(f"ALTER TABLE {REQUESTS} DROP COLUMN custody_closed")
    op.execute(f"ALTER TABLE {PROJECTS} DROP COLUMN closed_custody_title")
    op.execute(f"ALTER TABLE {PROPERTIES} DROP COLUMN custody_version, DROP COLUMN current_custodian_id")
