"""Join live transactional delivery with the tested property network chain.

Both branches retain their deployed or independently tested revisions. Their
delivery tables use the same final shape, so this revision records the joined
ancestry without changing records or enqueueing historical notifications.
"""

revision = "b19d8e4c720a"
down_revision = ("d721f0b6a84c", "a4e72d1c718b")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
