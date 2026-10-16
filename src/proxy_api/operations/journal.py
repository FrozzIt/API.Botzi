from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from proxy_api.auth.service import AuthenticatedSession
from proxy_api.database import ConfigState, OperationJournal

FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IDEMPOTENCY_KEY_MAX_LENGTH = 128


class OperationState(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class OperationRecord:
    request_id: uuid.UUID
    fingerprint: str
    state: OperationState
    safe_result: dict[str, object] | None


@dataclass(frozen=True)
class BeginOperation:
    record: OperationRecord
    execute: bool


def payload_fingerprint(payload: dict[str, object], secret: bytes) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hmac.new(secret, b"proxyapi:payload:v1\0" + canonical, hashlib.sha256).hexdigest()


def idempotency_digest(idempotency_key: str, secret: bytes) -> str:
    return hmac.new(
        secret,
        b"proxyapi:idempotency:v1\0" + idempotency_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def digest_key_verifier(secret: bytes) -> str:
    return hmac.new(
        secret,
        b"proxyapi:operation-journal-key:v1",
        hashlib.sha256,
    ).hexdigest()


class OperationTransitionRejected(RuntimeError):
    """The durable state changed before the requested transition."""


class OperationDigestKeyMismatch(RuntimeError):
    """The configured digest key does not match the durable journal verifier."""


class OperationJournalService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        digest_secret: bytes,
        pending_lease_seconds: float,
    ) -> None:
        if not digest_secret:
            raise ValueError("Operation journal digest secret is required")
        if pending_lease_seconds <= 0:
            raise ValueError("Operation journal pending lease must be positive")
        self._session_factory = session_factory
        self._digest_secret = digest_secret
        self._pending_lease = timedelta(seconds=pending_lease_seconds)

    def payload_fingerprint(self, payload: dict[str, object]) -> str:
        return payload_fingerprint(payload, self._digest_secret)

    async def begin(
        self,
        access: AuthenticatedSession,
        *,
        request_id: uuid.UUID,
        operation: str,
        idempotency_key: str | None,
        payload: dict[str, object],
    ) -> BeginOperation:
        if not operation or len(operation) > 32:
            raise ValueError("Operation identifier is invalid")
        if idempotency_key is not None and (
            not idempotency_key.strip() or len(idempotency_key) > IDEMPOTENCY_KEY_MAX_LENGTH
        ):
            raise ValueError("Idempotency key is invalid")
        fingerprint = self.payload_fingerprint(payload)
        if not FINGERPRINT_PATTERN.fullmatch(fingerprint):
            raise ValueError("Operation fingerprint is invalid")
        key_digest = (
            idempotency_digest(idempotency_key, self._digest_secret)
            if idempotency_key is not None
            else None
        )
        now = datetime.now(UTC)
        values = {
            "request_id": request_id,
            "provider_account": access.provider_account,
            "client_key": access.client_key,
            "project_id": access.project_id,
            "operation": operation,
            "idempotency_digest": key_digest,
            "fingerprint": fingerprint,
            "state": OperationState.PENDING.value,
            "pending_expires_at": now + self._pending_lease,
        }
        async with self._session_factory() as session, session.begin():
            await self._verify_digest_key(session)
            if idempotency_key is None:
                session.add(OperationJournal(**values))
                return BeginOperation(
                    OperationRecord(
                        request_id=request_id,
                        fingerprint=fingerprint,
                        state=OperationState.PENDING,
                        safe_result=None,
                    ),
                    execute=True,
                )

            inserted_id = await session.scalar(
                insert(OperationJournal)
                .values(**values)
                .on_conflict_do_nothing(constraint="uq_operation_journal_scope_key")
                .returning(OperationJournal.request_id)
            )
            if inserted_id is not None:
                return BeginOperation(
                    OperationRecord(
                        request_id=inserted_id,
                        fingerprint=fingerprint,
                        state=OperationState.PENDING,
                        safe_result=None,
                    ),
                    execute=True,
                )

            existing = await session.scalar(
                select(OperationJournal)
                .where(
                    OperationJournal.provider_account == access.provider_account,
                    OperationJournal.client_key == access.client_key,
                    OperationJournal.project_id == access.project_id,
                    OperationJournal.operation == operation,
                    OperationJournal.idempotency_digest == key_digest,
                )
                .with_for_update()
            )
            if existing is None:
                raise RuntimeError("Operation journal conflict row is unavailable")
            checked_at = datetime.now(UTC)
            if (
                existing.state == OperationState.PENDING.value
                and existing.pending_expires_at <= checked_at
            ):
                existing.state = OperationState.UNKNOWN.value
                existing.updated_at = checked_at
            return BeginOperation(self._record(existing), execute=False)

    async def _verify_digest_key(self, session: AsyncSession) -> None:
        expected = digest_key_verifier(self._digest_secret)
        state = await session.get(ConfigState, 1, with_for_update=True)
        if state is None:
            raise OperationDigestKeyMismatch("Operation journal configuration is unavailable")
        durable = state.operation_journal_digest_key_verifier
        if durable is None:
            existing = await session.scalar(select(OperationJournal.request_id).limit(1))
            if existing is not None:
                raise OperationDigestKeyMismatch(
                    "Operation journal digest key requires explicit reconciliation"
                )
            state.operation_journal_digest_key_verifier = expected
            return
        if not hmac.compare_digest(durable, expected):
            raise OperationDigestKeyMismatch("Operation journal digest key does not match")

    async def mark_dispatch_started(self, request_id: uuid.UUID) -> None:
        rejected = False
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(OperationJournal)
                .where(OperationJournal.request_id == request_id)
                .with_for_update()
            )
            checked_at = datetime.now(UTC)
            if row is None or row.state != OperationState.PENDING.value:
                rejected = True
            elif row.pending_expires_at <= checked_at:
                row.state = OperationState.UNKNOWN.value
                row.updated_at = checked_at
                rejected = True
            else:
                row.state = OperationState.UNKNOWN.value
                row.dispatch_started_at = checked_at
                row.updated_at = checked_at
        if rejected:
            raise OperationTransitionRejected("Operation journal state transition was rejected")

    async def mark_failed_before_send(self, request_id: uuid.UUID) -> None:
        await self._transition(
            request_id,
            expected=OperationState.PENDING,
            target=OperationState.FAILED,
        )

    async def mark_succeeded(
        self,
        request_id: uuid.UUID,
        safe_result: dict[str, object],
    ) -> None:
        await self._transition(
            request_id,
            expected=OperationState.UNKNOWN,
            target=OperationState.SUCCEEDED,
            safe_result=safe_result,
        )

    async def _transition(
        self,
        request_id: uuid.UUID,
        *,
        expected: OperationState,
        target: OperationState,
        safe_result: dict[str, object] | None = None,
    ) -> None:
        values: dict[str, object] = {
            "state": target.value,
            "updated_at": datetime.now(UTC),
        }
        if safe_result is not None:
            values["safe_result"] = safe_result
        async with self._session_factory() as session, session.begin():
            statement = update(OperationJournal).where(
                OperationJournal.request_id == request_id,
                OperationJournal.state == expected.value,
            )
            result = await session.execute(statement.values(**values))
            if result.rowcount != 1:
                raise OperationTransitionRejected("Operation journal state transition was rejected")

    @staticmethod
    def _record(row: OperationJournal) -> OperationRecord:
        result = dict(row.safe_result) if row.safe_result is not None else None
        return OperationRecord(
            request_id=row.request_id,
            fingerprint=row.fingerprint,
            state=OperationState(row.state),
            safe_result=result,
        )
