"""Add durable background jobs for memory processing.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False),
        sa.Column("kind", sa.Enum("MEMORY_EXTRACTION", "VECTOR_SYNC"), nullable=False),
        sa.Column("source_id", sa.CHAR(36), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "state", sa.Enum("PENDING", "RUNNING", "RETRY", "COMPLETED", "FAILED"), nullable=False
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("lease_id", sa.CHAR(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("tenant_id", "kind", "source_id", name="uq_background_jobs_source"),
    )
    op.create_index("ix_background_jobs_claim", "background_jobs", ["state", "available_at"])
    op.create_index("ix_background_jobs_tenant", "background_jobs", ["tenant_id"])
    op.create_table(
        "job_attempts",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False),
        sa.Column("job_id", sa.CHAR(36), sa.ForeignKey("background_jobs.id"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.UniqueConstraint("job_id", "attempt", name="uq_job_attempts_job_attempt"),
    )
    op.create_index("ix_job_attempts_tenant_job", "job_attempts", ["tenant_id", "job_id"])


def downgrade() -> None:
    op.drop_table("job_attempts")
    op.drop_table("background_jobs")
