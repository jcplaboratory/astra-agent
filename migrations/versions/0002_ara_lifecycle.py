"""Add ARA lifecycle persistence.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENT_TYPES = (
    "TASK_CREATED",
    "ARA_REGISTERED",
    "TASK_LEASED",
    "LEASE_RENEWED",
    "ARA_PROGRESS",
    "APPROVAL_REQUESTED",
    "APPROVAL_GRANTED",
    "APPROVAL_DENIED",
    "TASK_COMPLETED",
    "TASK_FAILED",
    "TASK_CANCELLED",
    "ARTIFACT_PRODUCED",
)
_FOUNDATION_EVENT_TYPES = tuple(
    event_type
    for event_type in _EVENT_TYPES
    if event_type not in {"LEASE_RENEWED", "ARA_PROGRESS", "ARTIFACT_PRODUCED"}
)


def _event_enum(event_types: tuple[str, ...]) -> str:
    values = ", ".join(f"'{event_type}'" for event_type in event_types)
    return f"ALTER TABLE audit_events MODIFY event_type ENUM({values}) NOT NULL"


def upgrade() -> None:
    op.execute(_event_enum(_EVENT_TYPES))
    op.create_table(
        "approvals",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False, index=True),
        sa.Column("task_id", sa.CHAR(36), sa.ForeignKey("tasks.id"), nullable=False, index=True),
        sa.Column("capability", sa.JSON(), nullable=False),
        sa.Column("requested_by", sa.CHAR(36), sa.ForeignKey("remote_agents.id"), nullable=False),
        sa.Column("state", sa.Enum("PENDING", "GRANTED", "DENIED"), nullable=False, index=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "artifacts",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False, index=True),
        sa.Column("task_id", sa.CHAR(36), sa.ForeignKey("tasks.id"), nullable=False, index=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("media_type", sa.String(200), nullable=False),
        sa.Column("object_key", sa.String(1000), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("artifacts")
    op.drop_table("approvals")
    op.execute(
        "DELETE FROM audit_events "
        "WHERE event_type IN ('LEASE_RENEWED', 'ARA_PROGRESS', 'ARTIFACT_PRODUCED')"
    )
    op.execute(_event_enum(_FOUNDATION_EVENT_TYPES))
