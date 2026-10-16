from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class ConfigState(Base):
    __tablename__ = "config_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class AuthClient(Base):
    __tablename__ = "auth_clients"

    client_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    login: Mapped[str] = mapped_column(String(128), nullable=False)
    login_normalized: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    provider_account: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    access_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    allowed_staff_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    main_owner_display_name: Mapped[str] = mapped_column(String(128), nullable=False)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4, primary_key=True)
    client_key: Mapped[str] = mapped_column(
        ForeignKey("auth_clients.client_key"),
        nullable=False,
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    service: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ContactDetailOwnership(Base):
    __tablename__ = "contact_detail_ownership"
    __table_args__ = (
        Index(
            "ix_contact_detail_ownership_parent",
            "provider_account",
            "project_id",
            "contact_id",
        ),
    )

    provider_account: Mapped[str] = mapped_column(String(64), primary_key=True)
    detail_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    contact_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class OperationJournal(Base):
    __tablename__ = "operation_journal"
    __table_args__ = (
        UniqueConstraint(
            "provider_account",
            "client_key",
            "project_id",
            "operation",
            "idempotency_key",
            name="uq_operation_journal_scope_key",
        ),
        CheckConstraint(
            "state IN ('pending', 'succeeded', 'failed', 'unknown')",
            name="ck_operation_journal_state",
        ),
        Index("ix_operation_journal_state_created", "state", "created_at"),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4, primary_key=True)
    provider_account: Mapped[str] = mapped_column(String(64), nullable=False)
    client_key: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    safe_result: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    dispatch_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
