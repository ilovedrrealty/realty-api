"""add moderated professional profiles

Revision ID: 4614c6845ccd
Revises: bcf3e5feca73
Create Date: 2026-10-17 22:45:41.160040

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4614c6845ccd'
down_revision: Union[str, Sequence[str], None] = 'bcf3e5feca73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE TABLE IF NOT EXISTS professional_profiles (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            version INTEGER NOT NULL DEFAULT 1,
            draft_data JSONB NOT NULL DEFAULT '{}'::jsonb,
            published_data JSONB,
            review_status TEXT NOT NULL DEFAULT 'draft',
            listing_status TEXT NOT NULL DEFAULT 'hidden',
            review_note TEXT,
            submitted_at TIMESTAMPTZ,
            reviewed_at TIMESTAMPTZ,
            reviewed_by UUID REFERENCES users(id) ON DELETE SET NULL,
            published_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_professional_version CHECK (version >= 1),
            CONSTRAINT ck_professional_review_status CHECK (review_status IN ('draft','pending','approved','rejected')),
            CONSTRAINT ck_professional_listing_status CHECK (listing_status IN ('hidden','published','suspended'))
        );
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS professional_profile_revisions (
            id UUID PRIMARY KEY,
            profile_id UUID NOT NULL REFERENCES professional_profiles(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            action TEXT NOT NULL,
            snapshot JSONB NOT NULL,
            note TEXT,
            actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_professional_revision_version UNIQUE (profile_id, version)
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_professional_review_queue ON professional_profiles (review_status, updated_at DESC, id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_professional_public_categories ON professional_profiles USING gin ((published_data -> 'category_ids')) WHERE listing_status = 'published';")
    op.execute("CREATE INDEX IF NOT EXISTS ix_professional_public_areas ON professional_profiles USING gin ((published_data -> 'service_area_ids')) WHERE listing_status = 'published';")
    op.execute("ALTER TABLE professional_profiles ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE professional_profile_revisions ENABLE ROW LEVEL SECURITY;")


def downgrade() -> None:
    """Downgrade schema."""
    # Retain professional profiles, published snapshots, consent and audit history
    # on application rollback. Migration-aware code is required to move the marker.
    pass
