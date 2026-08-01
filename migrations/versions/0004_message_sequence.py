"""Add deterministic message ordering.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("conversation_messages")}
    if "sequence" not in columns:
        op.add_column(
            "conversation_messages",
            sa.Column("sequence", sa.BigInteger(), autoincrement=True, nullable=True),
        )
    op.execute("SET @astra_message_sequence := 0")
    op.execute(
        "UPDATE conversation_messages "
        "SET sequence = (@astra_message_sequence := @astra_message_sequence + 1) "
        "ORDER BY created_at, id"
    )
    op.execute(
        "ALTER TABLE conversation_messages MODIFY sequence BIGINT NOT NULL AUTO_INCREMENT UNIQUE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE conversation_messages MODIFY sequence BIGINT NOT NULL")
    indexes = inspect(op.get_bind()).get_indexes("conversation_messages")
    sequence_index = next(
        (item["name"] for item in indexes if item["column_names"] == ["sequence"]), None
    )
    if sequence_index:
        op.drop_index(sequence_index, table_name="conversation_messages")
    op.drop_column("conversation_messages", "sequence")
