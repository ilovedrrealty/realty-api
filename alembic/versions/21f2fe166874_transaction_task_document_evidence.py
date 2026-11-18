"""Pin due-diligence review tasks to a private room's exact document version."""

from alembic import op


revision = "21f2fe166874"
down_revision = "e12ac7d49f03"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE transaction_room_tasks
        ADD COLUMN IF NOT EXISTS evidence_document_id UUID,
        ADD COLUMN IF NOT EXISTS evidence_version_id UUID""")
    op.execute("""DO $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'transaction_room_tasks'::regclass AND conname = 'ck_transaction_task_evidence_pair'
        ) THEN
            ALTER TABLE transaction_room_tasks ADD CONSTRAINT ck_transaction_task_evidence_pair
                CHECK ((evidence_document_id IS NULL) = (evidence_version_id IS NULL));
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'transaction_room_tasks'::regclass AND conname = 'fk_transaction_task_evidence_version'
        ) THEN
            ALTER TABLE transaction_room_tasks ADD CONSTRAINT fk_transaction_task_evidence_version
                FOREIGN KEY (room_id, evidence_document_id, evidence_version_id)
                REFERENCES transaction_document_versions (room_id, document_id, id) ON DELETE RESTRICT;
        END IF;
    END $$""")


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM transaction_room_tasks WHERE evidence_version_id IS NOT NULL) THEN
            RAISE EXCEPTION 'Preserve linked transaction review evidence before downgrading';
        END IF;
    END $$""")
    op.execute("""ALTER TABLE transaction_room_tasks
        DROP CONSTRAINT fk_transaction_task_evidence_version,
        DROP CONSTRAINT ck_transaction_task_evidence_pair,
        DROP COLUMN evidence_document_id,
        DROP COLUMN evidence_version_id""")
