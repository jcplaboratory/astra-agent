"""Add immutable tenant persona profiles and active profile pointers.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
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
)


def _event_enum(event_types: tuple[str, ...]) -> str:
    return (
        "ALTER TABLE audit_events MODIFY event_type ENUM("
        + ", ".join(f"'{item}'" for item in event_types)
        + ") NOT NULL"
    )


def upgrade() -> None:
    op.execute(_event_enum(_EVENT_TYPES))
    op.create_table(
        "persona_profiles",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("authored_core", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "version", name="uq_persona_profiles_tenant_version"),
    )
    op.create_index("ix_persona_profiles_tenant_id", "persona_profiles", ["tenant_id"])
    op.create_index("ix_persona_profiles_created_at", "persona_profiles", ["created_at"])
    op.create_table(
        "active_personas",
        sa.Column("tenant_id", sa.CHAR(36), primary_key=True),
        sa.Column(
            "profile_id",
            sa.CHAR(36),
            sa.ForeignKey("persona_profiles.id"),
            nullable=False,
            unique=True,
        ),
    )
    op.create_table(
        "learned_persona_adaptations",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False),
        sa.Column("profile_id", sa.CHAR(36), sa.ForeignKey("persona_profiles.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "state", sa.Enum("active", "reversed", name="learnedadaptationstate"), nullable=False
        ),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_learned_persona_adaptations_tenant_id",
        "learned_persona_adaptations",
        ["tenant_id"],
    )
    op.create_index(
        "ix_learned_persona_adaptations_profile_id",
        "learned_persona_adaptations",
        ["profile_id"],
    )
    op.create_index(
        "ix_learned_persona_adaptations_state", "learned_persona_adaptations", ["state"]
    )
    op.create_index(
        "ix_learned_persona_adaptations_created_at",
        "learned_persona_adaptations",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_learned_persona_adaptations_created_at", table_name="learned_persona_adaptations"
    )
    op.drop_index("ix_learned_persona_adaptations_state", table_name="learned_persona_adaptations")
    op.drop_index(
        "ix_learned_persona_adaptations_profile_id", table_name="learned_persona_adaptations"
    )
    op.drop_index(
        "ix_learned_persona_adaptations_tenant_id", table_name="learned_persona_adaptations"
    )
    op.drop_table("learned_persona_adaptations")
    op.drop_table("active_personas")
    op.drop_index("ix_persona_profiles_created_at", table_name="persona_profiles")
    op.drop_index("ix_persona_profiles_tenant_id", table_name="persona_profiles")
    op.drop_table("persona_profiles")
    previous = tuple(item for item in _EVENT_TYPES if not item.startswith("PERSONA_"))
    op.execute(
        "DELETE FROM audit_events WHERE event_type IN ("
        "'PERSONA_CREATED', 'PERSONA_ACTIVATED', 'PERSONA_REVERTED')"
    )
    op.execute(_event_enum(previous))
