"""Link ARA tool invocations to delegated tasks.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tool_invocations",
        sa.Column("task_id", sa.CHAR(36), sa.ForeignKey("tasks.id"), nullable=True),
    )
    op.create_index("ix_tool_invocations_task_id", "tool_invocations", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_tool_invocations_task_id", table_name="tool_invocations")
    op.drop_column("tool_invocations", "task_id")
