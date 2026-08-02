"""Hermes Holographic migration provenance and batches.

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


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
    op.create_table(
        "migration_batches",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False, index=True),
        sa.Column("source_system", sa.String(100), nullable=False),
        sa.Column("source_database_fingerprint", sa.CHAR(64), nullable=False),
        sa.Column(
            "state",
            sa.Enum("staged", "active", "rolled_back", name="migrationbatchstate"),
            nullable=False,
        ),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("persona_draft", sa.JSON(), nullable=True),
        sa.Column("persona_profile_id", sa.CHAR(36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_database_fingerprint",
            name="uq_migration_batch_source",
        ),
    )
    with op.batch_alter_table("memory_records") as batch:
        batch.add_column(sa.Column("import_batch_id", sa.CHAR(36), nullable=True))
        batch.add_column(sa.Column("source_system", sa.String(100), nullable=True))
        batch.add_column(sa.Column("source_database_fingerprint", sa.CHAR(64), nullable=True))
        batch.add_column(sa.Column("source_external_id", sa.String(500), nullable=True))
        batch.add_column(
            sa.Column("source_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("memory_records") as batch:
        batch.drop_column("source_metadata")
        batch.drop_column("source_external_id")
        batch.drop_column("source_database_fingerprint")
        batch.drop_column("source_system")
        batch.drop_column("import_batch_id")
    op.drop_table("migration_batches")
