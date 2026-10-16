"""Harden operation journal secret consistency.

Revision ID: 20261016_0005
Revises: 20261016_0004
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261016_0005"
down_revision: str | None = "20261016_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    config_columns = {column["name"] for column in inspector.get_columns("config_state")}
    journal_columns = {column["name"] for column in inspector.get_columns("operation_journal")}
    row_count = connection.scalar(sa.text("SELECT count(*) FROM operation_journal"))

    if "idempotency_key" in journal_columns:
        if row_count:
            raise RuntimeError(
                "Existing operation journal requires explicit idempotency-key reconciliation"
            )
        op.drop_constraint(
            "uq_operation_journal_scope_key",
            "operation_journal",
            type_="unique",
        )
        op.drop_column("operation_journal", "idempotency_key")
        op.add_column(
            "operation_journal",
            sa.Column("idempotency_digest", sa.String(length=64), nullable=True),
        )
        op.create_unique_constraint(
            "uq_operation_journal_scope_key",
            "operation_journal",
            [
                "provider_account",
                "client_key",
                "project_id",
                "operation",
                "idempotency_digest",
            ],
        )

    if "pending_expires_at" not in journal_columns:
        if row_count:
            raise RuntimeError(
                "Existing operation journal requires explicit pending-state reconciliation"
            )
        op.add_column(
            "operation_journal",
            sa.Column("pending_expires_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "operation_journal_digest_key_verifier" not in config_columns:
        op.add_column(
            "config_state",
            sa.Column(
                "operation_journal_digest_key_verifier",
                sa.String(length=64),
                nullable=True,
            ),
        )


def downgrade() -> None:
    op.drop_column("config_state", "operation_journal_digest_key_verifier")
    op.drop_constraint(
        "uq_operation_journal_scope_key",
        "operation_journal",
        type_="unique",
    )
    op.drop_column("operation_journal", "pending_expires_at")
    op.drop_column("operation_journal", "idempotency_digest")
    op.add_column(
        "operation_journal",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )
    op.create_unique_constraint(
        "uq_operation_journal_scope_key",
        "operation_journal",
        ["provider_account", "client_key", "project_id", "operation", "idempotency_key"],
    )
