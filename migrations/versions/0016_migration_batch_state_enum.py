"""Normalize migration batch enum values.

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # MariaDB enum labels are case-insensitive. The established lowercase database
    # values match MigrationBatchState.value; SQLAlchemy uses values rather than names.
    return None


def downgrade() -> None:
    return None
