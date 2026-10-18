"""add versioned website content settings

Revision ID: bcf3e5feca73
Revises: 39df1ce8f562
Create Date: 2026-10-17 21:46:56.746600

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'bcf3e5feca73'
down_revision: Union[str, Sequence[str], None] = '39df1ce8f562'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add separate content tables; never modify platform settings or users."""
    op.execute("""
        CREATE TABLE IF NOT EXISTS website_settings (
            id INTEGER PRIMARY KEY DEFAULT 1,
            version INTEGER NOT NULL DEFAULT 0,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
            CONSTRAINT ck_website_settings_singleton CHECK (id = 1),
            CONSTRAINT ck_website_settings_version CHECK (version >= 0)
        );
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS website_settings_revisions (
            version INTEGER PRIMARY KEY,
            snapshot JSONB NOT NULL,
            actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_website_settings_revision_version CHECK (version >= 0)
        );
    """)
    # Public clients use the filtered API, never direct table access. No anon or
    # authenticated policy exposes private tool URLs or the revision history.
    op.execute("ALTER TABLE website_settings ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE website_settings_revisions ENABLE ROW LEVEL SECURITY;")


def downgrade() -> None:
    """Retain content and revision history during a code rollback."""
    pass
