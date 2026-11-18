"""Private opted-in member requests and retained service agreements.

No accounts, professional imports, messages, roles or provider operations.
"""
from alembic import op

revision = "a4b6c0d2e834"
down_revision = "f3a5b9c1d723"
branch_labels = None
depends_on = None

TABLES = ("network_service_preferences", "network_service_requests", "network_service_recipients",
          "network_service_proposals", "network_service_actions")


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS network_service_preferences (
        user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT,
        service_opt_in BOOLEAN NOT NULL DEFAULT false, member_alias TEXT NOT NULL,
        category_ids JSONB NOT NULL, property_class_ids JSONB NOT NULL, version INTEGER NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_service_preference_version CHECK(version>=1),
        CONSTRAINT ck_service_preference_bounds CHECK(char_length(member_alias)<=120 AND jsonb_typeof(category_ids)='array' AND jsonb_array_length(category_ids)<=12 AND jsonb_typeof(property_class_ids)='array' AND jsonb_array_length(property_class_ids)<=10),
        CONSTRAINT ck_service_preference_consent CHECK(NOT service_opt_in OR (char_length(member_alias)>=1 AND jsonb_array_length(category_ids)>=1 AND jsonb_array_length(property_class_ids)>=1))
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS network_service_requests (
        id UUID PRIMARY KEY, property_id UUID REFERENCES network_properties(id) ON DELETE RESTRICT,
        listing_id UUID REFERENCES listings(id) ON DELETE RESTRICT,
        custodian_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        requester_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        source_listing_type TEXT, category_id TEXT NOT NULL, property_class TEXT NOT NULL,
        title TEXT NOT NULL, brief TEXT NOT NULL, area_label TEXT NOT NULL, requester_alias TEXT NOT NULL,
        desired_start_on DATE, recipient_user_codes JSONB NOT NULL, scope_sha256 TEXT NOT NULL,
        status TEXT NOT NULL, version INTEGER NOT NULL,
        project_id UUID UNIQUE REFERENCES network_projects(id) ON DELETE RESTRICT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_service_request_source CHECK((property_id IS NULL)<>(listing_id IS NULL)),
        CONSTRAINT ck_service_request_state CHECK(version>=1 AND status IN ('open','awarded','cancelled')),
        CONSTRAINT ck_service_request_project CHECK((status='awarded')=(project_id IS NOT NULL)),
        CONSTRAINT ck_service_request_bounds CHECK(char_length(title) BETWEEN 1 AND 180 AND char_length(brief) BETWEEN 1 AND 4000 AND char_length(area_label)<=120 AND char_length(requester_alias) BETWEEN 1 AND 120 AND scope_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_service_request_recipients CHECK(jsonb_typeof(recipient_user_codes)='array' AND jsonb_array_length(recipient_user_codes) BETWEEN 1 AND 5)
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS network_service_recipients (
        id UUID PRIMARY KEY, request_id UUID NOT NULL REFERENCES network_service_requests(id) ON DELETE RESTRICT,
        provider_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        user_code INTEGER NOT NULL, member_alias TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
        current_proposal_id UUID,
        CONSTRAINT uq_service_recipient_member UNIQUE(request_id,provider_id),
        CONSTRAINT ck_service_recipient_bounds CHECK(user_code>0 AND char_length(member_alias) BETWEEN 1 AND 120 AND version>=1),
        CONSTRAINT ck_service_recipient_state CHECK(status IN ('pending','accepted','declined','revoked','closed','awarded'))
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS network_service_proposals (
        id UUID PRIMARY KEY, request_id UUID NOT NULL REFERENCES network_service_requests(id) ON DELETE RESTRICT,
        recipient_id UUID NOT NULL REFERENCES network_service_recipients(id) ON DELETE RESTRICT,
        author_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        parent_proposal_id UUID REFERENCES network_service_proposals(id) ON DELETE RESTRICT,
        revision_no INTEGER NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
        amount NUMERIC(12,2) NOT NULL, currency TEXT NOT NULL, pricing_basis TEXT NOT NULL,
        scope_text TEXT NOT NULL, exclusions TEXT NOT NULL, estimated_days INTEGER, valid_until DATE NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_service_proposal_revision UNIQUE(recipient_id,revision_no),
        CONSTRAINT ck_service_proposal_state CHECK(revision_no>=1 AND version>=1 AND kind IN ('provider_quote','owner_counter') AND status IN ('submitted','accepted','declined','withdrawn','superseded','closed','awarded')),
        CONSTRAINT ck_service_proposal_price CHECK(amount>0 AND amount<=9999999999.99 AND currency IN ('USD','DOP') AND pricing_basis IN ('fixed','estimate')),
        CONSTRAINT ck_service_proposal_bounds CHECK(char_length(scope_text) BETWEEN 1 AND 4000 AND char_length(exclusions)<=2000 AND (estimated_days IS NULL OR estimated_days BETWEEN 1 AND 365))
    )""")
    op.execute("""DO $$ BEGIN
        IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='network_service_recipients'::regclass AND conname='fk_service_recipient_current') THEN
            ALTER TABLE network_service_recipients ADD CONSTRAINT fk_service_recipient_current FOREIGN KEY(current_proposal_id) REFERENCES network_service_proposals(id) ON DELETE RESTRICT;
        END IF;
    END $$""")
    op.execute("""CREATE TABLE IF NOT EXISTS network_service_actions (
        id UUID PRIMARY KEY, actor_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        submission_id UUID NOT NULL, operation TEXT NOT NULL, target_id UUID NOT NULL,
        request_id UUID REFERENCES network_service_requests(id) ON DELETE RESTRICT,
        recipient_id UUID REFERENCES network_service_recipients(id) ON DELETE RESTRICT,
        proposal_id UUID REFERENCES network_service_proposals(id) ON DELETE RESTRICT,
        creation_sha256 TEXT NOT NULL, result_versions JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_service_action_submission UNIQUE(actor_id,submission_id),
        CONSTRAINT ck_service_action_hash CHECK(creation_sha256 ~ '^[0-9a-f]{64}$' AND jsonb_typeof(result_versions)='object'),
        CONSTRAINT ck_service_action_operation CHECK(operation IN ('preference_save','request_create','recipient_accept','recipient_decline','recipient_revoke','provider_quote','owner_counter','counter_accept','counter_decline','proposal_withdraw','request_cancel','request_award'))
    )""")
    for name, table, columns in (
        ("ix_service_preference_optin", TABLES[0], "service_opt_in,user_id"),
        ("ix_service_request_property", TABLES[1], "property_id,created_at,id"),
        ("ix_service_request_listing", TABLES[1], "listing_id,created_at,id"),
        ("ix_service_request_custodian", TABLES[1], "custodian_id,created_at,id"),
        ("ix_service_request_requester", TABLES[1], "requester_id,created_at,id"),
        ("ix_service_recipient_inbox", TABLES[2], "provider_id,status,request_id"),
        ("ix_service_recipient_request", TABLES[2], "request_id,id"),
        ("ix_service_recipient_current", TABLES[2], "current_proposal_id"),
        ("ix_service_proposal_request", TABLES[3], "request_id,id"),
        ("ix_service_proposal_author", TABLES[3], "author_id"),
        ("ix_service_proposal_parent", TABLES[3], "parent_proposal_id"),
        ("ix_service_action_request", TABLES[4], "request_id,created_at,id"),
        ("ix_service_action_recipient", TABLES[4], "recipient_id,created_at,id"),
        ("ix_service_action_proposal", TABLES[4], "proposal_id")):
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({columns})")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_service_recipient_winner ON network_service_recipients(request_id) WHERE status='awarded'")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"""DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON {table} FROM anon; END IF;
            IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN REVOKE ALL ON {table} FROM authenticated; END IF;
        END $$""")
    op.execute("""CREATE OR REPLACE FUNCTION network_guard_service_identity() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER AS $$ DECLARE mutable TEXT[]; BEGIN
        IF TG_OP='DELETE' OR TG_TABLE_NAME='network_service_actions' THEN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Service agreements and action identities are retained';
        END IF;
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
    op.execute("""CREATE OR REPLACE FUNCTION network_guard_service_catalog() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER AS $$ DECLARE
        categories CONSTANT TEXT[] := ARRAY['real_estate_agent','property_law','notary','surveying','mortgage_lending','banking','insurance','accounting_tax','architecture','engineering','construction','renovation','electrical','plumbing','air_conditioning','interior_design','property_management','vacation_rental_management','cleaning','landscaping','pool_service','security','moving_storage','home_inspection','solar_energy','internet_utilities','other_services','handyman','painting','roofing_waterproofing','pest_control','appliance_repair','carpentry_cabinetry','metalwork_welding','flooring_tiling','water_systems','septic_drainage','backup_power','smart_property','fire_safety','waste_removal','property_media','agricultural_services'];
        classes CONSTANT TEXT[] := ARRAY['house_villa','apartment_condominium','multiunit_building','vacation_rental','hotel_hospitality','office','retail_restaurant','warehouse_industrial','farm_rural','vacant_land'];
        BEGIN
        IF TG_TABLE_NAME='network_service_preferences' THEN
            IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(NEW.category_ids) AS item(value) WHERE NOT(value=ANY(categories)))
                OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(NEW.property_class_ids) AS item(value) WHERE NOT(value=ANY(classes)))
                OR (SELECT count(DISTINCT value) FROM jsonb_array_elements_text(NEW.category_ids) AS item(value))<>jsonb_array_length(NEW.category_ids)
                OR (SELECT count(DISTINCT value) FROM jsonb_array_elements_text(NEW.property_class_ids) AS item(value))<>jsonb_array_length(NEW.property_class_ids) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Choose unique known service contexts';
            END IF;
            IF NEW.service_opt_in AND NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.user_id AND user_code>0 AND status='active' AND email_verified IS TRUE) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Offering services requires an active verified member';
            END IF;
        ELSE
            IF NOT(NEW.category_id=ANY(categories)) OR NOT(NEW.property_class=ANY(classes))
                OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.recipient_user_codes) AS item(value) WHERE jsonb_typeof(value)<>'number' OR value::text !~ '^[1-9][0-9]*$')
                OR (SELECT count(DISTINCT value) FROM jsonb_array_elements(NEW.recipient_user_codes) AS item(value))<>jsonb_array_length(NEW.recipient_user_codes) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Choose unique known service members and context';
            END IF;
        END IF;
        RETURN NEW;
        END $$""")
    op.execute("""CREATE OR REPLACE FUNCTION network_guard_service_links() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER AS $$ DECLARE parent RECORD; recipient RECORD; BEGIN
        IF TG_TABLE_NAME='network_service_recipients' THEN
            IF NEW.current_proposal_id IS NOT NULL THEN
                IF NOT EXISTS(SELECT 1 FROM network_service_proposals WHERE id=NEW.current_proposal_id AND recipient_id=NEW.id AND request_id=NEW.request_id) THEN
                    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Service proposal scope mismatch';
                END IF;
            END IF;
        ELSIF TG_TABLE_NAME='network_service_proposals' AND TG_OP='INSERT' THEN
            SELECT * INTO recipient FROM network_service_recipients WHERE id=NEW.recipient_id;
            IF NOT FOUND OR recipient.request_id<>NEW.request_id OR NEW.version<>1 OR NEW.status<>'submitted'
                OR recipient.current_proposal_id IS DISTINCT FROM NEW.parent_proposal_id THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Service proposal scope mismatch';
            END IF;
            IF NEW.parent_proposal_id IS NULL THEN
                IF NEW.revision_no<>1 THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Service revision mismatch'; END IF;
            ELSE
                SELECT * INTO parent FROM network_service_proposals WHERE id=NEW.parent_proposal_id;
                IF NOT FOUND OR parent.recipient_id<>NEW.recipient_id OR parent.request_id<>NEW.request_id OR NEW.revision_no<>parent.revision_no+1 THEN
                    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Service revision mismatch';
                END IF;
            END IF;
            IF NEW.kind='provider_quote' AND NEW.author_id<>recipient.provider_id THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Service proposal author mismatch';
            END IF;
            IF NEW.kind='owner_counter' AND NOT EXISTS(SELECT 1 FROM network_service_requests r WHERE r.id=NEW.request_id AND
                (NEW.author_id IN (r.custodian_id,r.requester_id) OR EXISTS(SELECT 1 FROM users WHERE id=NEW.author_id AND role='admin'))) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Service proposal author mismatch';
            END IF;
        ELSIF TG_TABLE_NAME='network_service_actions' THEN
            IF NEW.operation='preference_save' THEN
                IF NEW.request_id IS NOT NULL OR NEW.recipient_id IS NOT NULL OR NEW.proposal_id IS NOT NULL OR NEW.target_id<>NEW.actor_id THEN
                    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Service action scope mismatch';
                END IF;
            ELSIF NEW.request_id IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Service action requires request scope';
            END IF;
            IF NEW.recipient_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM network_service_recipients WHERE id=NEW.recipient_id AND request_id=NEW.request_id) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Service action recipient mismatch';
            END IF;
            IF NEW.proposal_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM network_service_proposals WHERE id=NEW.proposal_id AND request_id=NEW.request_id AND recipient_id=NEW.recipient_id) THEN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Service action proposal mismatch';
            END IF;
        END IF;
        RETURN NEW;
        END $$""")
    for table in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS service_identity ON {table}")
        op.execute(f"CREATE TRIGGER service_identity BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION network_guard_service_identity()")
    for table in TABLES[2:]:
        op.execute(f"DROP TRIGGER IF EXISTS service_links ON {table}")
        op.execute(f"CREATE TRIGGER service_links BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION network_guard_service_links()")
    for table in TABLES[:2]:
        op.execute(f"DROP TRIGGER IF EXISTS service_catalog ON {table}")
        op.execute(f"CREATE TRIGGER service_catalog BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION network_guard_service_catalog()")
    for function in ("network_guard_service_identity", "network_guard_service_links", "network_guard_service_catalog"):
        op.execute(f"REVOKE ALL ON FUNCTION {function}() FROM PUBLIC")
        op.execute(f"""DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON FUNCTION {function}() FROM anon; END IF;
            IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN REVOKE ALL ON FUNCTION {function}() FROM authenticated; END IF;
        END $$""")


def downgrade():
    op.execute("LOCK TABLE " + ",".join(TABLES) + " IN ACCESS EXCLUSIVE MODE")
    for table in TABLES:
        op.execute(f"""DO $$ BEGIN IF EXISTS(SELECT 1 FROM {table}) THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Service consent and agreement history must be retained';
        END IF; END $$""")
    op.execute("ALTER TABLE network_service_recipients DROP CONSTRAINT fk_service_recipient_current")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION network_guard_service_identity()")
    op.execute("DROP FUNCTION network_guard_service_links()")
    op.execute("DROP FUNCTION network_guard_service_catalog()")
