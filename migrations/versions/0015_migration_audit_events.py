"""Add Hermes migration audit event values.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENT_TYPES = (
    "CONVERSATION_CREATED",
    "CONVERSATION_RECEIVED",
    "CONTEXT_COMPILED",
    "MODEL_REQUEST",
    "MODEL_RESPONSE",
    "MODEL_FAILED",
    "MEMORY_CANDIDATE_CREATED",
    "MEMORY_PROMOTED",
    "MEMORY_REJECTED",
    "MEMORY_CONTRADICTION_RESOLVED",
    "MEMORY_DELETED",
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
    "ARTIFACT_DOWNLOADED",
    "ARTIFACT_DELETED",
    "ARA_HEARTBEAT",
    "CONVERSATION_TURN_CREATED",
    "CONVERSATION_TURN_RUNNING",
    "CONVERSATION_TURN_PAUSED",
    "CONVERSATION_TURN_COMPLETED",
    "CONVERSATION_TURN_FAILED",
    "TOOL_INVOCATION_REQUESTED",
    "TOOL_INVOCATION_COMPLETED",
    "TOOL_INVOCATION_FAILED",
    "TOOL_INVOCATION_DENIED",
    "PERSONA_CREATED",
    "PERSONA_ACTIVATED",
    "PERSONA_REVERTED",
    "PLANNER_DENIED",
    "MIGRATION_STAGED",
    "MIGRATION_ACTIVATED",
    "MIGRATION_ROLLED_BACK",
)


def _event_enum() -> str:
    return (
        "ALTER TABLE audit_events MODIFY event_type ENUM("
        + ", ".join(f"'{item}'" for item in _EVENT_TYPES)
        + ") NOT NULL"
    )


def upgrade() -> None:
    op.execute(_event_enum())


def downgrade() -> None:
    op.execute(_event_enum())
