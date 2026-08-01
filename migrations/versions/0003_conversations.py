"""Add conversation persistence.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ARA_EVENT_TYPES = (
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
_CONVERSATION_EVENT_TYPES = (
    "CONVERSATION_CREATED",
    "CONVERSATION_RECEIVED",
    "CONTEXT_COMPILED",
    "MODEL_REQUEST",
    "MODEL_RESPONSE",
    "MODEL_FAILED",
)
_EVENT_TYPES = _CONVERSATION_EVENT_TYPES + _ARA_EVENT_TYPES


def _event_enum(event_types: tuple[str, ...]) -> str:
    values = ", ".join(f"'{event_type}'" for event_type in event_types)
    return f"ALTER TABLE audit_events MODIFY event_type ENUM({values}) NOT NULL"


def upgrade() -> None:
    op.execute(_event_enum(_EVENT_TYPES))
    op.create_table(
        "conversations",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False, index=True),
        sa.Column("user_id", sa.CHAR(36), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False, index=True),
    )
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False, index=True),
        sa.Column(
            "conversation_id",
            sa.CHAR(36),
            sa.ForeignKey("conversations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("role", sa.Enum("USER", "ASSISTANT"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("conversation_messages")
    op.drop_table("conversations")
    conversation_types = ", ".join(f"'{value}'" for value in _CONVERSATION_EVENT_TYPES)
    op.execute(f"DELETE FROM audit_events WHERE event_type IN ({conversation_types})")
    op.execute(_event_enum(_ARA_EVENT_TYPES))
