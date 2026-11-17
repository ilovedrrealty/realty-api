"""Store private transaction document metadata and immutable version references."""

from alembic import op


revision = "de248ab5c9f3"
down_revision = "6c942ea8385d"
branch_labels = None
depends_on = None

TABLES = ("transaction_documents", "transaction_document_versions")


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS transaction_documents (
        room_id UUID NOT NULL REFERENCES transaction_rooms(id) ON DELETE RESTRICT,
        id UUID NOT NULL,
        title TEXT NOT NULL,
        creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (room_id, id),
        CONSTRAINT ck_transaction_document_title CHECK (char_length(btrim(title)) BETWEEN 1 AND 160)
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS transaction_document_versions (
        room_id UUID NOT NULL,
        document_id UUID NOT NULL,
        id UUID NOT NULL,
        number INTEGER NOT NULL,
        request_id UUID NOT NULL,
        uploader_id UUID REFERENCES users(id) ON DELETE SET NULL,
        file_name TEXT NOT NULL,
        content_type TEXT NOT NULL,
        byte_count INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        uploaded_at TIMESTAMPTZ,
        reviewed_at TIMESTAMPTZ,
        reviewer_id UUID REFERENCES users(id) ON DELETE SET NULL,
        share_with_buyer BOOLEAN NOT NULL DEFAULT false,
        professional_grant_id UUID REFERENCES transaction_room_grants(id) ON DELETE SET NULL,
        PRIMARY KEY (room_id, document_id, id),
        CONSTRAINT fk_transaction_document_version_parent FOREIGN KEY (room_id, document_id)
            REFERENCES transaction_documents(room_id, id) ON DELETE RESTRICT,
        CONSTRAINT uq_transaction_document_version_number UNIQUE (room_id, document_id, number),
        CONSTRAINT uq_transaction_document_request UNIQUE (room_id, uploader_id, request_id),
        CONSTRAINT ck_transaction_document_version_file CHECK (
            number >= 1 AND byte_count BETWEEN 1 AND 6291456
            AND char_length(btrim(file_name)) BETWEEN 1 AND 140
            AND content_type IN ('application/pdf','image/jpeg','image/png') AND sha256 ~ '^[a-f0-9]{64}$'
        ),
        CONSTRAINT ck_transaction_document_version_state CHECK (
            (status = 'reserved' AND uploaded_at IS NULL AND reviewed_at IS NULL)
            OR (status = 'submitted' AND uploaded_at IS NOT NULL AND reviewed_at IS NULL)
            OR (status IN ('approved','rejected') AND uploaded_at IS NOT NULL AND reviewed_at IS NOT NULL)
        ),
        CONSTRAINT ck_transaction_document_version_audience CHECK (
            status = 'approved' OR (NOT share_with_buyer AND professional_grant_id IS NULL)
        )
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_transaction_document_creator ON transaction_documents (creator_id)")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_transaction_document_version_uploader
        ON transaction_document_versions (uploader_id)""")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_transaction_document_version_reviewer
        ON transaction_document_versions (reviewer_id)""")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_transaction_document_version_grant
        ON transaction_document_versions (professional_grant_id, room_id)""")
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
