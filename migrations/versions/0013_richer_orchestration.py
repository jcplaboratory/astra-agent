"""Add ARA trust and orchestration provenance.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "remote_agents", sa.Column("trust_level", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column("tasks", sa.Column("target_ara_id", sa.CHAR(36), nullable=True))
    op.add_column("tasks", sa.Column("completed_by_ara_id", sa.CHAR(36), nullable=True))
    op.create_foreign_key(
        "fk_tasks_target_ara", "tasks", "remote_agents", ["target_ara_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_tasks_completed_by_ara", "tasks", "remote_agents", ["completed_by_ara_id"], ["id"]
    )
    op.create_index("ix_tasks_target_ara_id", "tasks", ["target_ara_id"])
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
        "'PERSONA_CREATED','PERSONA_ACTIVATED','PERSONA_REVERTED','PLANNER_DENIED') NOT NULL"
    )


def downgrade() -> None:
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
    op.drop_index("ix_tasks_target_ara_id", table_name="tasks")
    op.drop_constraint("fk_tasks_completed_by_ara", "tasks", type_="foreignkey")
    op.drop_constraint("fk_tasks_target_ara", "tasks", type_="foreignkey")
    op.drop_column("tasks", "completed_by_ara_id")
    op.drop_column("tasks", "target_ara_id")
    op.drop_column("remote_agents", "trust_level")
