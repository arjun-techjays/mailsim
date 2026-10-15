"""actions.interface: which interface an action came through

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("actions", sa.Column("interface", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("actions", "interface")
