from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from proxy_api.auth.service import AuthenticatedSession
from proxy_api.database import OperationJournal
from proxy_api.operations import (
    OperationJournalService,
    OperationState,
    OperationTransitionRejected,
)

pytestmark = pytest.mark.integration
TEST_DIGEST_SECRET = b"operation-journal-integration-secret"


def require_database_url() -> str:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("RUN_INTEGRATION_TESTS=1 is required")
    value = os.getenv("DATABASE_URL")
    if value is None:
        raise RuntimeError("DATABASE_URL is required for integration tests")
    return value


def access(provider_account: str) -> AuthenticatedSession:
    return AuthenticatedSession(
        session_id=str(uuid.uuid4()),
        client_key=f"client-{uuid.uuid4().hex}",
        provider_account=provider_account,
        project_id=10001,
    )


def journal(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    pending_lease_seconds: float = 5,
) -> OperationJournalService:
    return OperationJournalService(
        session_factory,
        digest_secret=TEST_DIGEST_SECRET,
        pending_lease_seconds=pending_lease_seconds,
    )


def payload(marker: str) -> dict[str, object]:
    return {"project_id": 10001, "name": marker}


@pytest.mark.asyncio
async def test_concurrent_same_key_has_one_executor_and_payload_conflict() -> None:
    engine = create_async_engine(require_database_url())
    service = journal(async_sessionmaker(engine, expire_on_commit=False))
    current_access = access(f"journal-{uuid.uuid4().hex}")
    try:
        claims = await asyncio.gather(
            *(
                service.begin(
                    current_access,
                    request_id=uuid.uuid4(),
                    operation="C02",
                    idempotency_key="same-key",
                    payload=payload("same"),
                )
                for _ in range(8)
            )
        )
        conflict = await service.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key="same-key",
            payload=payload("different"),
        )
    finally:
        await engine.dispose()

    assert sum(item.execute for item in claims) == 1
    assert len({item.record.request_id for item in claims}) == 1
    assert all(item.record.state is OperationState.PENDING for item in claims)
    assert conflict.execute is False
    assert conflict.record.fingerprint == service.payload_fingerprint(payload("same"))


@pytest.mark.asyncio
async def test_pending_is_live_within_lease_then_becomes_unknown_after_restart() -> None:
    database_url = require_database_url()
    current_access = access(f"journal-{uuid.uuid4().hex}")
    first_engine = create_async_engine(database_url)
    first = journal(
        async_sessionmaker(first_engine, expire_on_commit=False),
        pending_lease_seconds=0.5,
    )
    pending = await first.begin(
        current_access,
        request_id=uuid.uuid4(),
        operation="C02",
        idempotency_key="pending-key",
        payload=payload("pending"),
    )
    unknown = await first.begin(
        current_access,
        request_id=uuid.uuid4(),
        operation="C02",
        idempotency_key="unknown-key",
        payload=payload("unknown"),
    )
    crashed = await first.begin(
        current_access,
        request_id=uuid.uuid4(),
        operation="C02",
        idempotency_key="crashed-key",
        payload=payload("crashed"),
    )
    await first.mark_dispatch_started(unknown.record.request_id)
    await first_engine.dispose()

    restarted_engine = create_async_engine(database_url)
    restarted = journal(
        async_sessionmaker(restarted_engine, expire_on_commit=False),
        pending_lease_seconds=0.5,
    )
    try:
        pending_replay = await restarted.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key="pending-key",
            payload=payload("pending"),
        )
        unknown_replay = await restarted.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key="unknown-key",
            payload=payload("unknown"),
        )
        await asyncio.sleep(0.55)
        with pytest.raises(OperationTransitionRejected):
            await restarted.mark_dispatch_started(pending.record.request_id)
        crash_replay = await restarted.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key="crashed-key",
            payload=payload("crashed"),
        )
        stale_replay = await restarted.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key="pending-key",
            payload=payload("pending"),
        )
        durable_unknown = await restarted.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key="pending-key",
            payload=payload("pending"),
        )
    finally:
        await restarted_engine.dispose()

    assert pending.execute is True
    assert pending_replay.execute is False
    assert pending_replay.record.state is OperationState.PENDING
    assert stale_replay.execute is False
    assert stale_replay.record.state is OperationState.UNKNOWN
    assert durable_unknown.execute is False
    assert durable_unknown.record.state is OperationState.UNKNOWN
    assert stale_replay.record.request_id == pending.record.request_id
    assert crash_replay.execute is False
    assert crash_replay.record.state is OperationState.UNKNOWN
    assert crash_replay.record.request_id == crashed.record.request_id
    assert unknown_replay.execute is False
    assert unknown_replay.record.state is OperationState.UNKNOWN


@pytest.mark.asyncio
async def test_success_and_failed_state_are_durable_and_no_key_is_not_deduplicated() -> None:
    engine = create_async_engine(require_database_url())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    service = journal(session_factory)
    current_access = access(f"journal-{uuid.uuid4().hex}")
    try:
        opaque_key = "person@example.invalid bearer-secret-sentinel " + ("x" * 512)
        succeeded = await service.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key=opaque_key,
            payload=payload("success"),
        )
        await service.mark_dispatch_started(succeeded.record.request_id)
        await service.mark_succeeded(
            succeeded.record.request_id,
            {"id": 20001, "project_id": 10001},
        )
        replayed = await service.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key=opaque_key,
            payload=payload("success"),
        )

        failed = await service.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key="failed-key",
            payload=payload("failed"),
        )
        await service.mark_failed_before_send(failed.record.request_id)
        failed_replay = await service.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key="failed-key",
            payload=payload("failed"),
        )

        no_key_one = await service.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key=None,
            payload=payload("no-key"),
        )
        no_key_two = await service.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key=None,
            payload=payload("no-key"),
        )
        async with session_factory() as session:
            stored = await session.get(OperationJournal, succeeded.record.request_id)
    finally:
        await engine.dispose()

    assert replayed.execute is False
    assert replayed.record.state is OperationState.SUCCEEDED
    assert replayed.record.safe_result == {"id": 20001, "project_id": 10001}
    assert failed_replay.execute is False
    assert failed_replay.record.state is OperationState.FAILED
    assert no_key_one.execute is True
    assert no_key_two.execute is True
    assert no_key_one.record.request_id != no_key_two.record.request_id
    assert stored is not None
    assert stored.idempotency_digest is not None
    assert len(stored.idempotency_digest) == 64
    assert "person@example.invalid" not in stored.idempotency_digest
    assert "bearer-secret-sentinel" not in stored.idempotency_digest
    assert not hasattr(stored, "idempotency_key")
