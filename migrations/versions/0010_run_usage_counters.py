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
    # runs already in flight: agent_events is derived from the log (the agent's events of the
    # current epoch), so it can be backfilled exactly. Reads and sent copies were never
    # counted before this revision; they start at 0.
    op.execute(
        sa.text(
            "UPDATE runs SET agent_events = (SELECT count(*) FROM events e "
            "WHERE e.run_id = runs.id AND e.actor = 'agent' AND e.epoch = runs.epoch)"
        )
    )


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("runs", name)
