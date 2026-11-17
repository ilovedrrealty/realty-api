"""Bind approved room tour metadata to the exact public listing gallery."""
from alembic import op

revision = "d8f3a7c2e615"
down_revision = "a6e4c2b8d517"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE listings ADD COLUMN IF NOT EXISTS room_photo_tour_snapshot JSONB")
    op.execute("ALTER TABLE listing_edits ADD COLUMN IF NOT EXISTS room_photo_tour_base_gallery_sha256 TEXT")


def downgrade():
    op.execute("ALTER TABLE listing_edits DROP COLUMN room_photo_tour_base_gallery_sha256")
    op.execute("ALTER TABLE listings DROP COLUMN room_photo_tour_snapshot")
