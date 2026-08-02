"""Add artifact retention and reliable ARA lifecycle events.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tasks MODIFY state "
        "ENUM('PENDING','LEASED','COMPLETED','FAILED','CANCELLED','CANCELLING') NOT NULL"
    )
    op.add_column("artifacts", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column("artifacts", sa.Column("retention_until", sa.DateTime(), nullable=True))
    op.create_index("ix_artifacts_deleted_at", "artifacts", ["deleted_at"])
    op.create_index("ix_artifacts_retention_until", "artifacts", ["retention_until"])
    op.execute(
        "ALTER TABLE audit_events MODIFY event_type ENUM("
        "'CONVERSATION_CREATED','CONVERSATION_RECEIVED','CONTEXT_COMPILED','MODEL_REQUEST',"
        "'MODEL_RESPONSE','MODEL_FAILED','MEMORY_CANDIDATE_CREATED','MEMORY_PROMOTED',"
        "'MEMORY_REJECTED','MEMORY_CONTRADICTION_RESOLVED','MEMORY_DELETED','TASK_CREATED',"
        "'ARA_REGISTERED','TASK_LEASED','LEASE_RENEWED','ARA_PROGRESS','APPROVAL_REQUESTED',"
        "'APPROVAL_GRANTED','APPROVAL_DENIED','TASK_COMPLETED','TASK_FAILED','TASK_CANCELLED',"
        "'ARTIFACT_PRODUCED','ARTIFACT_DOWNLOADED','ARTIFACT_DELETED','ARA_HEARTBEAT',"
        "'CONVERSATION_TURN_CREATED','CONVERSATION_TURN_RUNNING','CONVERSATION_TURN_PAUSED',"
        "'CONVERSATION_TURN_COMPLETED','CONVERSATION_TURN_FAILED','TOOL_INVOCATION_REQUESTED',"
        "'TOOL_INVOCATION_COMPLETED','TOOL_INVOCATION_FAILED','TOOL_INVOCATION_DENIED',"
        "'PERSONA_CREATED','PERSONA_ACTIVATED','PERSONA_REVERTED') NOT NULL"
    )


def downgrade() -> None:
    op.execute("UPDATE tasks SET state = 'CANCELLED' WHERE state = 'CANCELLING'")
    op.execute(
        "ALTER TABLE tasks MODIFY state "
        "ENUM('PENDING','LEASED','COMPLETED','FAILED','CANCELLED') NOT NULL"
    )
    op.drop_index("ix_artifacts_retention_until", table_name="artifacts")
    op.drop_index("ix_artifacts_deleted_at", table_name="artifacts")
    op.drop_column("artifacts", "retention_until")
    op.drop_column("artifacts", "deleted_at")
