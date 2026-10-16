from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from proxy_api.auth.service import AuthenticatedSession
from proxy_api.operations import OperationJournalService, OperationState

pytestmark = pytest.mark.integration


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


@pytest.mark.asyncio
async def test_concurrent_same_key_has_one_executor_and_payload_conflict() -> None:
    engine = create_async_engine(require_database_url())
    service = OperationJournalService(async_sessionmaker(engine, expire_on_commit=False))
    current_access = access(f"journal-{uuid.uuid4().hex}")
    try:
        claims = await asyncio.gather(
            *(
                service.begin(
                    current_access,
                    request_id=uuid.uuid4(),
                    operation="C02",
                    idempotency_key="same-key",
                    fingerprint="a" * 64,
                )
                for _ in range(8)
            )
        )
        conflict = await service.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key="same-key",
            fingerprint="b" * 64,
        )
    finally:
        await engine.dispose()

    assert sum(item.execute for item in claims) == 1
    assert len({item.record.request_id for item in claims}) == 1
    assert all(item.record.state is OperationState.PENDING for item in claims)
    assert conflict.execute is False
    assert conflict.record.fingerprint == "a" * 64


@pytest.mark.asyncio
async def test_pending_and_unknown_survive_restart_without_reexecution() -> None:
    database_url = require_database_url()
    current_access = access(f"journal-{uuid.uuid4().hex}")
    first_engine = create_async_engine(database_url)
    first = OperationJournalService(async_sessionmaker(first_engine, expire_on_commit=False))
    pending = await first.begin(
        current_access,
        request_id=uuid.uuid4(),
        operation="C02",
        idempotency_key="pending-key",
        fingerprint="c" * 64,
    )
    unknown = await first.begin(
        current_access,
        request_id=uuid.uuid4(),
        operation="C02",
        idempotency_key="unknown-key",
        fingerprint="d" * 64,
    )
    await first.mark_dispatch_started(unknown.record.request_id)
    await first_engine.dispose()

    restarted_engine = create_async_engine(database_url)
    restarted = OperationJournalService(
        async_sessionmaker(restarted_engine, expire_on_commit=False)
    )
    try:
        pending_replay = await restarted.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key="pending-key",
            fingerprint="c" * 64,
        )
        unknown_replay = await restarted.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key="unknown-key",
            fingerprint="d" * 64,
        )
    finally:
        await restarted_engine.dispose()

    assert pending.execute is True
    assert pending_replay.execute is False
    assert pending_replay.record.state is OperationState.PENDING
    assert unknown_replay.execute is False
    assert unknown_replay.record.state is OperationState.UNKNOWN


@pytest.mark.asyncio
async def test_success_and_failed_state_are_durable_and_no_key_is_not_deduplicated() -> None:
    engine = create_async_engine(require_database_url())
    service = OperationJournalService(async_sessionmaker(engine, expire_on_commit=False))
    current_access = access(f"journal-{uuid.uuid4().hex}")
    try:
        succeeded = await service.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key="success-key",
            fingerprint="e" * 64,
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
            idempotency_key="success-key",
            fingerprint="e" * 64,
        )

        failed = await service.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key="failed-key",
            fingerprint="f" * 64,
        )
        await service.mark_failed_before_send(failed.record.request_id)
        failed_replay = await service.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key="failed-key",
            fingerprint="f" * 64,
        )

        no_key_one = await service.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key=None,
            fingerprint="0" * 64,
        )
        no_key_two = await service.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key=None,
            fingerprint="0" * 64,
        )
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
