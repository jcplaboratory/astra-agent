"""Add memory record persistence.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREVIOUS_EVENT_TYPES = (
    "CONVERSATION_CREATED",
    "CONVERSATION_RECEIVED",
    "CONTEXT_COMPILED",
    "MODEL_REQUEST",
    "MODEL_RESPONSE",
    "MODEL_FAILED",
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
_MEMORY_EVENT_TYPES = (
    "MEMORY_CANDIDATE_CREATED",
    "MEMORY_PROMOTED",
    "MEMORY_DELETED",
)


def _event_enum(event_types: tuple[str, ...]) -> str:
    values = ", ".join(f"'{event_type}'" for event_type in event_types)
    return f"ALTER TABLE audit_events MODIFY event_type ENUM({values}) NOT NULL"


def upgrade() -> None:
    op.execute(_event_enum(_PREVIOUS_EVENT_TYPES + _MEMORY_EVENT_TYPES))
    op.create_table(
        "memory_records",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False, index=True),
        sa.Column(
            "kind",
            sa.Enum("FACT", "PREFERENCE", "PROJECT", "DECISION", "COMMITMENT"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("normalized_content", sa.Text(), nullable=False),
        sa.Column("source_event_id", sa.CHAR(36), nullable=False, index=True),
        sa.Column(
            "source_message_id",
            sa.CHAR(36),
            sa.ForeignKey("conversation_messages.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("state", sa.Enum("CANDIDATE", "PROMOTED", "DELETED"), nullable=False),
        sa.Column("sensitivity", sa.String(100), nullable=False),
        sa.Column("visibility", sa.String(100), nullable=False),
        sa.Column("retention", sa.String(100), nullable=False),
        sa.Column(
            "contradiction_of",
            sa.CHAR(36),
            sa.ForeignKey("memory_records.id"),
            nullable=True,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "normalized_content", name="uq_memory_records_tenant_normalized"
        ),
    )
    op.create_index(
        "ix_memory_records_tenant_state_updated",
        "memory_records",
        ["tenant_id", "state", "updated_at"],
    )


def downgrade() -> None:
    op.drop_table("memory_records")
    memory_types = ", ".join(f"'{value}'" for value in _MEMORY_EVENT_TYPES)
    op.execute(f"DELETE FROM audit_events WHERE event_type IN ({memory_types})")
    op.execute(_event_enum(_PREVIOUS_EVENT_TYPES))
