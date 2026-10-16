"""Create durable operation journal.

Revision ID: 20261016_0004
Revises: 20261012_0003
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261016_0004"
down_revision: str | None = "20261012_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operation_journal",
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("provider_account", sa.String(length=64), nullable=False),
        sa.Column("client_key", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("idempotency_digest", sa.String(length=64), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column(
            "safe_result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'succeeded', 'failed', 'unknown')",
            name="ck_operation_journal_state",
        ),
        sa.PrimaryKeyConstraint("request_id"),
        sa.UniqueConstraint(
            "provider_account",
            "client_key",
            "project_id",
            "operation",
            "idempotency_digest",
            name="uq_operation_journal_scope_key",
        ),
    )
    op.create_index(
        "ix_operation_journal_state_created",
        "operation_journal",
        ["state", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_operation_journal_state_created",
        table_name="operation_journal",
    )
    op.drop_table("operation_journal")
