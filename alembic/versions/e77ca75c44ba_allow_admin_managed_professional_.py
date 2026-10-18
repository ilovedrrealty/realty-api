"""allow admin managed professional directory entries

Revision ID: e77ca75c44ba
Revises: 4614c6845ccd
Create Date: 2026-10-18 00:04:00.271449

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e77ca75c44ba'
down_revision: Union[str, Sequence[str], None] = '4614c6845ccd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # PostgreSQL's existing UNIQUE(user_id) still enforces one account profile
    # while allowing multiple NULL admin-managed directory entries.
    op.execute("ALTER TABLE professional_profiles ALTER COLUMN user_id DROP NOT NULL;")
    op.execute("ALTER TABLE professional_profiles ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE professional_profile_revisions ENABLE ROW LEVEL SECURITY;")


def downgrade() -> None:
    """Downgrade schema."""
    # Do not restore NOT NULL: it would reject or require deleting managed rows.
    # Retain all profiles/history and deploy nullable-aware rollback code.
    pass
