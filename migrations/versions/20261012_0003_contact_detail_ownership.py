"""Create trusted contact detail ownership index.

Revision ID: 20261012_0003
Revises: 20260929_0002
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0003"
down_revision: str | None = "20260929_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contact_detail_ownership",
        sa.Column("provider_account", sa.String(length=64), nullable=False),
        sa.Column("detail_id", sa.String(length=128), nullable=False),
        sa.Column("contact_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("provider_account", "detail_id"),
    )
    op.create_index(
        "ix_contact_detail_ownership_parent",
        "contact_detail_ownership",
        ["provider_account", "project_id", "contact_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_contact_detail_ownership_parent",
        table_name="contact_detail_ownership",
    )
    op.drop_table("contact_detail_ownership")
