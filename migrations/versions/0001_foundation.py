"""Astra foundation runtime tables.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "remote_agents",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("runtime_version", sa.String(100), nullable=False),
        sa.Column("status", sa.Enum("ACTIVE", "OFFLINE", "REVOKED"), nullable=False),
        sa.Column("registered_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False, index=True),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("required_capabilities", sa.JSON(), nullable=False),
        sa.Column("deliverable_contract", sa.Text(), nullable=False),
        sa.Column(
            "state",
            sa.Enum("PENDING", "LEASED", "COMPLETED", "FAILED", "CANCELLED"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("deadline", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "leases",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False, index=True),
        sa.Column("task_id", sa.CHAR(36), sa.ForeignKey("tasks.id"), nullable=False, unique=True),
        sa.Column("ara_id", sa.CHAR(36), sa.ForeignKey("remote_agents.id"), nullable=False),
        sa.Column("acquired_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False, index=True),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False, index=True),
        sa.Column(
            "event_type",
            sa.Enum(
                "TASK_CREATED",
                "ARA_REGISTERED",
                "TASK_LEASED",
                "APPROVAL_REQUESTED",
                "APPROVAL_GRANTED",
                "APPROVAL_DENIED",
                "TASK_COMPLETED",
                "TASK_FAILED",
                "TASK_CANCELLED",
            ),
            nullable=False,
        ),
        sa.Column("actor_type", sa.Enum("USER", "COORDINATOR", "ARA"), nullable=False),
        sa.Column("actor_id", sa.CHAR(36), nullable=True),
        sa.Column("task_id", sa.CHAR(36), nullable=True, index=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("leases")
    op.drop_table("tasks")
    op.drop_table("remote_agents")
