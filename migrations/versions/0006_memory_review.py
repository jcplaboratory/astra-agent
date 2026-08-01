"""Add memory review persistence.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREVIOUS_MEMORY_STATES = ("CANDIDATE", "PROMOTED", "DELETED")
_MEMORY_STATES = ("CANDIDATE", "PROMOTED", "REJECTED", "DELETED")
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
    "MEMORY_CANDIDATE_CREATED",
    "MEMORY_PROMOTED",
    "MEMORY_DELETED",
)
_REVIEW_EVENT_TYPES = ("MEMORY_REJECTED", "MEMORY_CONTRADICTION_RESOLVED")


def _enum_sql(table: str, column: str, values: tuple[str, ...]) -> str:
    enum_values = ", ".join(f"'{value}'" for value in values)
    return f"ALTER TABLE {table} MODIFY {column} ENUM({enum_values}) NOT NULL"


def upgrade() -> None:
    op.execute(_enum_sql("memory_records", "state", _MEMORY_STATES))
    op.add_column("memory_records", sa.Column("reviewed_at", sa.DateTime(), nullable=True))
    op.add_column("memory_records", sa.Column("reviewed_by", sa.CHAR(36), nullable=True))
    op.execute(_enum_sql("audit_events", "event_type", _PREVIOUS_EVENT_TYPES + _REVIEW_EVENT_TYPES))


def downgrade() -> None:
    review_events = ", ".join(f"'{value}'" for value in _REVIEW_EVENT_TYPES)
    op.execute(f"DELETE FROM audit_events WHERE event_type IN ({review_events})")
    op.execute(_enum_sql("audit_events", "event_type", _PREVIOUS_EVENT_TYPES))
    op.execute(
        "UPDATE memory_records SET state = 'CANDIDATE', confirmed = 0 WHERE state = 'REJECTED'"
    )
    op.drop_column("memory_records", "reviewed_by")
    op.drop_column("memory_records", "reviewed_at")
    op.execute(_enum_sql("memory_records", "state", _PREVIOUS_MEMORY_STATES))
