"""add private lead tracking and submission idempotency

Revision ID: 49d50da0e05b
Revises: e77ca75c44ba
Create Date: 2026-10-22 14:42:27.670350

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '49d50da0e05b'
down_revision: Union[str, Sequence[str], None] = 'e77ca75c44ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Bootstrap creates current model columns for an empty installation; these
    # guards also preserve all rows on existing installations. No grants or RLS
    # policies change, and attribution is never exposed through a public view.
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS submission_id UUID")
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS tracking JSONB")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS leads_submission_id_unique ON leads (submission_id)")


def downgrade() -> None:
    # Deliberately retain captured metadata and deduplication keys on rollback.
    # Older application versions ignore these nullable columns safely.
    pass
