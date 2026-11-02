"""Private unlisted-property records and exactly-one project property source.

No listings, users, jobs, profiles, buckets or provider requests are created.
"""
from alembic import op

revision = "f3a5b9c1d723"
down_revision = "e2f4a8b0c612"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS network_properties (
        id UUID PRIMARY KEY, custodian_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        submission_id UUID NOT NULL, title TEXT NOT NULL, location TEXT NOT NULL,
        address TEXT NOT NULL, property_class TEXT NOT NULL, relationship TEXT NOT NULL,
        notes TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        creation_sha256 TEXT NOT NULL, authority_acknowledged BOOLEAN NOT NULL,
        CONSTRAINT uq_network_property_submission UNIQUE(custodian_id,submission_id),
        CONSTRAINT ck_network_property_version CHECK(version >= 1),
        CONSTRAINT ck_network_property_status CHECK(status IN ('active','archived')),
        CONSTRAINT ck_network_property_relationship CHECK(relationship IN ('owner','manager','seller')),
        CONSTRAINT ck_network_property_bounds CHECK(char_length(title) BETWEEN 1 AND 180 AND char_length(location) BETWEEN 1 AND 180 AND char_length(address) <= 500 AND char_length(notes) <= 4000),
        CONSTRAINT ck_network_property_creation_hash CHECK(creation_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_network_property_authority CHECK(authority_acknowledged IS TRUE),
        CONSTRAINT ck_network_property_class CHECK(property_class IN ('house_villa','apartment_condominium','multiunit_building','vacation_rental','hotel_hospitality','office','retail_restaurant','warehouse_industrial','farm_rural','vacant_land'))
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_network_property_custodian ON network_properties(custodian_id,updated_at,id)")
    # This exact append order is also the SQLAlchemy model declaration order.
    op.execute("ALTER TABLE network_projects ADD COLUMN IF NOT EXISTS property_id UUID")
    op.execute("ALTER TABLE network_projects ADD COLUMN IF NOT EXISTS created_by_id UUID")
    op.execute("ALTER TABLE network_projects ADD COLUMN IF NOT EXISTS submission_id UUID")
    op.execute("ALTER TABLE network_projects ADD COLUMN IF NOT EXISTS creation_sha256 TEXT")
    op.execute("ALTER TABLE network_projects ALTER COLUMN listing_id DROP NOT NULL")
    op.execute("""DO $$ BEGIN
        IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='network_projects'::regclass AND conname='network_projects_property_id_fkey') THEN
            ALTER TABLE network_projects ADD CONSTRAINT network_projects_property_id_fkey FOREIGN KEY(property_id) REFERENCES network_properties(id) ON DELETE RESTRICT;
        END IF;
        IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='network_projects'::regclass AND conname='network_projects_created_by_id_fkey') THEN
            ALTER TABLE network_projects ADD CONSTRAINT network_projects_created_by_id_fkey FOREIGN KEY(created_by_id) REFERENCES users(id) ON DELETE RESTRICT;
        END IF;
        IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='network_projects'::regclass AND conname='ck_network_project_one_property') THEN
            ALTER TABLE network_projects ADD CONSTRAINT ck_network_project_one_property CHECK((listing_id IS NULL) <> (property_id IS NULL));
        END IF;
        IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='network_projects'::regclass AND conname='ck_network_project_submission') THEN
            ALTER TABLE network_projects ADD CONSTRAINT ck_network_project_submission CHECK((created_by_id IS NULL AND submission_id IS NULL AND creation_sha256 IS NULL) OR (created_by_id IS NOT NULL AND submission_id IS NOT NULL AND creation_sha256 IS NOT NULL AND creation_sha256 ~ '^[0-9a-f]{64}$'));
        END IF;
        IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='network_projects'::regclass AND conname='ck_network_registry_project_submission') THEN
            ALTER TABLE network_projects ADD CONSTRAINT ck_network_registry_project_submission CHECK(property_id IS NULL OR submission_id IS NOT NULL);
        END IF;
        IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='network_projects'::regclass AND conname='uq_network_project_submission') THEN
            ALTER TABLE network_projects ADD CONSTRAINT uq_network_project_submission UNIQUE(created_by_id,submission_id);
        END IF;
    END $$""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_network_project_registry ON network_projects(property_id,created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_network_project_owner ON network_projects(owner_id,updated_at)")
    op.execute("ALTER TABLE network_properties ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON network_properties FROM PUBLIC")
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON network_properties FROM anon; END IF;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN REVOKE ALL ON network_properties FROM authenticated; END IF;
    END $$""")
    op.execute("""CREATE OR REPLACE FUNCTION network_guard_registry_property() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Private property records are retained';
        END IF;
        IF NEW.id IS DISTINCT FROM OLD.id OR NEW.custodian_id IS DISTINCT FROM OLD.custodian_id
            OR NEW.submission_id IS DISTINCT FROM OLD.submission_id OR NEW.creation_sha256 IS DISTINCT FROM OLD.creation_sha256
            OR NEW.authority_acknowledged IS DISTINCT FROM OLD.authority_acknowledged OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Private property creation identity is immutable';
        END IF;
        RETURN NEW;
        END $$""")
    op.execute("DROP TRIGGER IF EXISTS network_registry_identity ON network_properties")
    op.execute("CREATE TRIGGER network_registry_identity BEFORE UPDATE OR DELETE ON network_properties FOR EACH ROW EXECUTE FUNCTION network_guard_registry_property()")
    op.execute("""CREATE OR REPLACE FUNCTION network_guard_project_creation_identity() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Project records are retained';
        END IF;
        IF NEW.id IS DISTINCT FROM OLD.id OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
            OR NEW.listing_id IS DISTINCT FROM OLD.listing_id OR NEW.property_id IS DISTINCT FROM OLD.property_id
            OR NEW.created_by_id IS DISTINCT FROM OLD.created_by_id OR NEW.submission_id IS DISTINCT FROM OLD.submission_id
            OR NEW.creation_sha256 IS DISTINCT FROM OLD.creation_sha256 THEN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Project property and creation identity are immutable';
        END IF;
        RETURN NEW;
        END $$""")
    op.execute("DROP TRIGGER IF EXISTS network_project_creation_identity ON network_projects")
    op.execute("CREATE TRIGGER network_project_creation_identity BEFORE UPDATE OR DELETE ON network_projects FOR EACH ROW EXECUTE FUNCTION network_guard_project_creation_identity()")
    for function in ("network_guard_registry_property", "network_guard_project_creation_identity"):
        op.execute(f"REVOKE ALL ON FUNCTION {function}() FROM PUBLIC")
        op.execute(f"""DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON FUNCTION {function}() FROM anon; END IF;
            IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN REVOKE ALL ON FUNCTION {function}() FROM authenticated; END IF;
        END $$""")


def downgrade():
    # Lock and check in the same DDL transaction: concurrent new records must
    # never slip between the retention guard and column/table removal.
    op.execute("LOCK TABLE network_properties, network_projects IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM network_properties)
            OR EXISTS(SELECT 1 FROM network_projects WHERE property_id IS NOT NULL OR created_by_id IS NOT NULL OR submission_id IS NOT NULL OR creation_sha256 IS NOT NULL)
            OR EXISTS(SELECT 1 FROM network_audit WHERE target_kind='registry_property') THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Private registry data or creation identities must be retained';
        END IF;
    END $$""")
    op.execute("DROP TRIGGER IF EXISTS network_registry_identity ON network_properties")
    op.execute("DROP TRIGGER IF EXISTS network_project_creation_identity ON network_projects")
    op.execute("DROP FUNCTION IF EXISTS network_guard_registry_property()")
    op.execute("DROP FUNCTION IF EXISTS network_guard_project_creation_identity()")
    for constraint in ("ck_network_project_one_property", "ck_network_project_submission", "ck_network_registry_project_submission", "uq_network_project_submission", "network_projects_property_id_fkey", "network_projects_created_by_id_fkey"):
        op.execute(f"ALTER TABLE network_projects DROP CONSTRAINT IF EXISTS {constraint}")
    op.execute("DROP INDEX IF EXISTS ix_network_project_registry")
    op.execute("DROP INDEX IF EXISTS ix_network_project_owner")
    for column in ("creation_sha256", "submission_id", "created_by_id", "property_id"):
        op.execute(f"ALTER TABLE network_projects DROP COLUMN {column}")
    op.execute("ALTER TABLE network_projects ALTER COLUMN listing_id SET NOT NULL")
    op.execute("DROP TABLE network_properties")
