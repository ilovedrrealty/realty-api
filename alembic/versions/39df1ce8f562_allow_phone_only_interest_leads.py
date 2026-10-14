"""allow phone-only interest leads

Revision ID: 39df1ce8f562
Revises: bc364345c64b
Create Date: 2026-10-14 04:46:06.236623

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '39df1ce8f562'
down_revision: Union[str, Sequence[str], None] = 'bc364345c64b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Keep real email values and allow phone-only interest leads."""
    op.alter_column('leads', 'email', existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    """Restore the constraint only when doing so preserves every lead.

    Code rollback should keep this additive schema change. Never manufacture
    email addresses or delete phone-only leads to satisfy the old constraint.
    """
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM leads WHERE email IS NULL) THEN
                RAISE EXCEPTION 'Cannot restore required email while phone-only leads exist; keep the nullable email schema when rolling back code';
            END IF;
        END $$;
    """)
    op.alter_column('leads', 'email', existing_type=sa.Text(), nullable=False)
