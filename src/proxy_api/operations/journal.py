from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from proxy_api.auth.service import AuthenticatedSession
from proxy_api.database import OperationJournal

FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")


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


def payload_fingerprint(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class OperationJournalService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def begin(
        self,
        access: AuthenticatedSession,
        *,
        request_id: uuid.UUID,
        operation: str,
        idempotency_key: str | None,
        fingerprint: str,
    ) -> BeginOperation:
        if not operation or len(operation) > 32:
            raise ValueError("Operation identifier is invalid")
        if idempotency_key is not None and (not idempotency_key or len(idempotency_key) > 128):
            raise ValueError("Idempotency key is invalid")
        if not FINGERPRINT_PATTERN.fullmatch(fingerprint):
            raise ValueError("Operation fingerprint is invalid")
        values = {
            "request_id": request_id,
            "provider_account": access.provider_account,
            "client_key": access.client_key,
            "project_id": access.project_id,
            "operation": operation,
            "idempotency_key": idempotency_key,
            "fingerprint": fingerprint,
            "state": OperationState.PENDING.value,
        }
        async with self._session_factory() as session, session.begin():
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
                select(OperationJournal).where(
                    OperationJournal.provider_account == access.provider_account,
                    OperationJournal.client_key == access.client_key,
                    OperationJournal.project_id == access.project_id,
                    OperationJournal.operation == operation,
                    OperationJournal.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise RuntimeError("Operation journal conflict row is unavailable")
            return BeginOperation(self._record(existing), execute=False)

    async def mark_dispatch_started(self, request_id: uuid.UUID) -> None:
        await self._transition(
            request_id,
            expected=OperationState.PENDING,
            target=OperationState.UNKNOWN,
            dispatch_started_at=datetime.now(UTC),
        )

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
        dispatch_started_at: datetime | None = None,
    ) -> None:
        values: dict[str, object] = {
            "state": target.value,
            "updated_at": datetime.now(UTC),
        }
        if safe_result is not None:
            values["safe_result"] = safe_result
        if dispatch_started_at is not None:
            values["dispatch_started_at"] = dispatch_started_at
        async with self._session_factory() as session, session.begin():
            result = await session.execute(
                update(OperationJournal)
                .where(
                    OperationJournal.request_id == request_id,
                    OperationJournal.state == expected.value,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                raise RuntimeError("Operation journal state transition was rejected")

    @staticmethod
    def _record(row: OperationJournal) -> OperationRecord:
        result = dict(row.safe_result) if row.safe_result is not None else None
        return OperationRecord(
            request_id=row.request_id,
            fingerprint=row.fingerprint,
            state=OperationState(row.state),
            safe_result=result,
        )
