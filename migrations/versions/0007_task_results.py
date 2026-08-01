"""Add Astra task results and completion timestamps.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("result", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("completed_at", sa.DateTime(), nullable=True))
    op.create_index("ix_tasks_completed_at", "tasks", ["completed_at"])


def downgrade() -> None:
    op.drop_index("ix_tasks_completed_at", table_name="tasks")
    op.drop_column("tasks", "completed_at")
    op.drop_column("tasks", "result")
