"""Retain one private custodian review for an owner-approved service award."""
from alembic import op

revision = "a61d9e4b7f20"
down_revision = "e4a1c7b9d305"
branch_labels = None
depends_on = None

TABLE = "network_service_reviews"
GUARD = "network_guard_service_review"


def upgrade():
    op.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
        id UUID PRIMARY KEY,
        request_id UUID NOT NULL REFERENCES network_service_work(request_id) ON DELETE RESTRICT,
        reviewer_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        provider_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        submission_id UUID NOT NULL,
        stars INTEGER NOT NULL,
        comment TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_service_review_request UNIQUE (request_id),
        CONSTRAINT uq_service_review_submission UNIQUE (reviewer_id, submission_id),
        CONSTRAINT ck_service_review_stars CHECK (reviewer_id <> provider_id AND stars BETWEEN 1 AND 5),
        CONSTRAINT ck_service_review_comment CHECK (comment IS NULL OR (
            char_length(comment) BETWEEN 1 AND 2000
            AND comment = btrim(comment, ' ' || chr(9) || chr(10) || chr(13))
            AND translate(comment, chr(9) || chr(10) || chr(13), '') COLLATE "C" !~ '[[:cntrl:]]'
        ))
    )""")
    op.execute(f"""CREATE OR REPLACE FUNCTION {GUARD}() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER AS $$ BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION USING ERRCODE='23000', MESSAGE='Private service reviews must be retained';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM network_service_requests r
            JOIN network_service_recipients winner ON winner.request_id=r.id AND winner.status='awarded'
            JOIN network_projects p ON p.id=r.project_id
            JOIN network_project_members m ON m.project_id=p.id AND m.user_id=winner.provider_id
            JOIN network_service_work w ON w.request_id=r.id
            JOIN network_project_updates u ON u.id=w.completion_update_id
            JOIN users reviewer ON reviewer.id=r.custodian_id
            JOIN users provider ON provider.id=winner.provider_id
            LEFT JOIN network_properties registry ON registry.id=r.property_id
            LEFT JOIN listings listing ON listing.id=r.listing_id
            WHERE r.id=NEW.request_id AND r.status='awarded' AND reviewer.id=NEW.reviewer_id
              AND provider.id=NEW.provider_id AND reviewer.status='active' AND provider.status='active'
              AND reviewer.email_verified IS TRUE AND provider.email_verified IS TRUE
              AND reviewer.role<>'admin' AND provider.role<>'admin'
              AND p.owner_id=reviewer.id AND p.status<>'archived' AND m.active IS TRUE AND m.role='professional'
              AND p.property_id IS NOT DISTINCT FROM r.property_id AND p.listing_id IS NOT DISTINCT FROM r.listing_id
              AND w.status='completed' AND u.project_id=p.id AND u.author_id=reviewer.id AND u.kind='progress'
              AND u.correction_of IS NULL AND u.body=w.completion_note AND u.occurred_on=w.completed_on
              AND u.media_ids='[]'::jsonb
              AND EXISTS (SELECT 1 FROM network_service_work_actions a
                  WHERE a.request_id=w.request_id AND a.actor_id=reviewer.id AND a.operation='approve_completion')
              AND ((r.property_id IS NOT NULL AND registry.status='active' AND registry.custodian_id=reviewer.id
                    AND registry.property_class=r.property_class)
                   OR (r.listing_id IS NOT NULL AND listing.status='active' AND listing.type::text=r.source_listing_type
                       AND ((listing.owner_id=reviewer.id AND reviewer.role IN ('owner','realtor'))
                            OR (listing.owner_id IS NULL AND listing.submitted_by=reviewer.id AND reviewer.role='owner'))))
        ) THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Review requires a completed and current service award';
        END IF;
        RETURN NEW;
        END $$""")
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON {TABLE} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON FUNCTION {GUARD}() FROM PUBLIC")
    op.execute(f"DROP TRIGGER IF EXISTS retain_service_review ON {TABLE}")
    op.execute(f"""CREATE TRIGGER retain_service_review BEFORE INSERT OR UPDATE OR DELETE ON {TABLE}
        FOR EACH ROW EXECUTE FUNCTION {GUARD}()""")
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
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Private service reviews must be retained';
        END IF;
    END $$""")
    op.execute(f"DROP TABLE {TABLE}")
    op.execute(f"DROP FUNCTION {GUARD}()")
