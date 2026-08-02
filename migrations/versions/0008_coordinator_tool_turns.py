"""Add durable coordinator turns and tool invocations.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREVIOUS_EVENT_TYPES = (
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
)
_TURN_EVENT_TYPES = (
    "CONVERSATION_TURN_CREATED",
    "CONVERSATION_TURN_RUNNING",
    "CONVERSATION_TURN_PAUSED",
    "CONVERSATION_TURN_COMPLETED",
    "CONVERSATION_TURN_FAILED",
    "TOOL_INVOCATION_REQUESTED",
    "TOOL_INVOCATION_COMPLETED",
    "TOOL_INVOCATION_FAILED",
    "TOOL_INVOCATION_DENIED",
)


def _event_enum(event_types: tuple[str, ...]) -> str:
    values = ", ".join(f"'{event_type}'" for event_type in event_types)
    return f"ALTER TABLE audit_events MODIFY event_type ENUM({values}) NOT NULL"


def upgrade() -> None:
    op.execute(_event_enum(_PREVIOUS_EVENT_TYPES + _TURN_EVENT_TYPES))
    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False, index=True),
        sa.Column(
            "conversation_id",
            sa.CHAR(36),
            sa.ForeignKey("conversations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_message_id",
            sa.CHAR(36),
            sa.ForeignKey("conversation_messages.id"),
            nullable=False,
        ),
        sa.Column("client_request_id", sa.CHAR(36), nullable=False),
        sa.Column(
            "state",
            sa.Enum("PENDING", "RUNNING", "PAUSED", "COMPLETED", "FAILED"),
            nullable=False,
            index=True,
        ),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("run_lease_id", sa.CHAR(36), nullable=True, index=True),
        sa.Column("run_lease_expires_at", sa.DateTime(), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True, index=True),
        sa.UniqueConstraint("tenant_id", "client_request_id", name="uq_turns_tenant_request"),
    )
    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False, index=True),
        sa.Column(
            "turn_id",
            sa.CHAR(36),
            sa.ForeignKey("conversation_turns.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("tool_call_id", sa.String(200), nullable=False),
        sa.Column("tool_name", sa.String(200), nullable=False),
        sa.Column("target", sa.Enum("LOCAL", "ARA"), nullable=False, index=True),
        sa.Column(
            "state",
            sa.Enum("PENDING", "AWAITING_APPROVAL", "RUNNING", "COMPLETED", "FAILED", "DENIED"),
            nullable=False,
            index=True,
        ),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("arguments_sha256", sa.CHAR(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True, index=True),
        sa.UniqueConstraint("turn_id", "tool_call_id", name="uq_invocations_turn_call"),
    )
    op.alter_column("approvals", "task_id", existing_type=sa.CHAR(36), nullable=True)
    op.add_column(
        "approvals",
        sa.Column(
            "tool_invocation_id",
            sa.CHAR(36),
            sa.ForeignKey("tool_invocations.id"),
            nullable=True,
            index=True,
        ),
    )
    op.alter_column("approvals", "requested_by", existing_type=sa.CHAR(36), nullable=True)
    op.add_column(
        "approvals",
        sa.Column(
            "requestor_type",
            sa.Enum("COORDINATOR", "ARA"),
            nullable=False,
            server_default="ARA",
        ),
    )
    op.add_column("approvals", sa.Column("decided_by", sa.CHAR(36), nullable=True))
    op.create_check_constraint(
        "ck_approvals_one_subject",
        "approvals",
        "(task_id IS NULL) <> (tool_invocation_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_approvals_ara_requestor",
        "approvals",
        "requestor_type <> 'ARA' OR requested_by IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_approvals_ara_requestor", "approvals", type_="check")
    op.drop_constraint("ck_approvals_one_subject", "approvals", type_="check")
    op.drop_column("approvals", "decided_by")
    op.drop_column("approvals", "requestor_type")
    op.alter_column("approvals", "requested_by", existing_type=sa.CHAR(36), nullable=False)
    op.drop_column("approvals", "tool_invocation_id")
    op.alter_column("approvals", "task_id", existing_type=sa.CHAR(36), nullable=False)
    op.drop_table("tool_invocations")
    op.drop_table("conversation_turns")
    turn_events = ", ".join(f"'{value}'" for value in _TURN_EVENT_TYPES)
    op.execute(f"DELETE FROM audit_events WHERE event_type IN ({turn_events})")
    op.execute(_event_enum(_PREVIOUS_EVENT_TYPES))
