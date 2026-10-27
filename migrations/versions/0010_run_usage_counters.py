"""runs.agent_reads / agent_sent_copies / agent_events: per-run resource cap counters

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_COLUMNS = ("agent_reads", "agent_sent_copies", "agent_events")


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column("runs", sa.Column(name, sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("runs", name)
