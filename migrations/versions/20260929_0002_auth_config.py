"""Create config and authentication tables.

Revision ID: 20260929_0002
Revises: 20260928_0001
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260929_0002"
down_revision: str | None = "20260928_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "config_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_config_state_singleton"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision"),
    )
    op.create_table(
        "auth_clients",
        sa.Column("client_key", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("login", sa.String(length=128), nullable=False),
        sa.Column("login_normalized", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("provider_account", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("access_group", sa.String(length=64), nullable=True),
        sa.Column("allowed_staff_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("main_owner_display_name", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("client_key"),
        sa.UniqueConstraint("login_normalized"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_key", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("service", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_key"], ["auth_clients.client_key"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_auth_sessions_client_active",
        "auth_sessions",
        ["client_key", "revoked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_client_active", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("auth_clients")
    op.drop_table("config_state")
