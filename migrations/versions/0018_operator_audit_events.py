"""Add operator lifecycle audit event values.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENT_TYPES = (
    "CONVERSATION_CREATED",
    "CONVERSATION_RECEIVED",
    "CONTEXT_COMPILED",
    "MODEL_REQUEST",
    "MODEL_RESPONSE",
    "MODEL_FAILED",
    "PIPELINE_FAILED",
    "PIPELINE_RECOVERED",
    "JOB_REQUEUED",
    "MEMORY_CANDIDATE_CREATED",
    "MEMORY_PROMOTED",
    "MEMORY_REJECTED",
    "MEMORY_CONTRADICTION_RESOLVED",
    "MEMORY_DELETED",
    "TASK_CREATED",
    "ARA_REGISTERED",
    "ARA_TRUST_UPDATED",
    "ARA_REVOKED",
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


def upgrade() -> None:
    op.execute(
        "ALTER TABLE audit_events MODIFY event_type ENUM("
        + ", ".join(f"'{item}'" for item in _EVENT_TYPES)
        + ") NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE audit_events MODIFY event_type ENUM("
        + ", ".join(
            f"'{item}'"
            for item in _EVENT_TYPES
            if item not in {"JOB_REQUEUED", "ARA_TRUST_UPDATED", "ARA_REVOKED"}
        )
        + ") NOT NULL"
    )
