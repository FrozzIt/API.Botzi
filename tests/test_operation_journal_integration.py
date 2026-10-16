from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from proxy_api.auth.service import AuthenticatedSession
from proxy_api.contacts.service import ContactCreateState, ContactWriter
from proxy_api.database import ConfigState, OperationJournal
from proxy_api.operations import (
    OperationDigestKeyMismatch,
    OperationJournalService,
    OperationState,
    OperationTransitionRejected,
)
from proxy_api.reads.detail_ownership import VerifiedContact

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
    digest_secret: bytes = TEST_DIGEST_SECRET,
) -> OperationJournalService:
    return OperationJournalService(
        session_factory,
        digest_secret=digest_secret,
        pending_lease_seconds=pending_lease_seconds,
    )


def payload(marker: str) -> dict[str, object]:
    return {"project_id": 10001, "name": marker}


async def reset_journal_database(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(delete(OperationJournal))
        await session.execute(delete(ConfigState))
        session.add(
            ConfigState(
                id=1,
                schema_version=1,
                revision=1,
                content_digest="0" * 64,
            )
        )


async def clear_journal_database(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(delete(OperationJournal))
        await session.execute(delete(ConfigState))


class BlockingBeforeSendProvider:
    def __init__(self) -> None:
        self.before_send_ready = asyncio.Event()
        self.release_before_send = asyncio.Event()
        self.calls = 0

    async def post_once(
        self,
        path: str,
        *,
        json_body: dict[str, object],
        before_send: object,
        deadline_seconds: float | None = None,
    ) -> object:
        del path, json_body, deadline_seconds
        self.before_send_ready.set()
        await self.release_before_send.wait()
        await before_send()  # type: ignore[operator]
        self.calls += 1
        raise AssertionError("Expired operation must not reach provider POST")


class UnusedOwnershipStore:
    async def sync_verified_contacts(
        self,
        provider_account: str,
        contacts: list[VerifiedContact],
    ) -> None:
        del provider_account, contacts
        raise AssertionError("Expired operation must not update ownership")


@pytest.mark.asyncio
async def test_concurrent_same_key_has_one_executor_and_payload_conflict() -> None:
    engine = create_async_engine(require_database_url())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await reset_journal_database(session_factory)
    service = journal(session_factory)
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
        await clear_journal_database(session_factory)
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
    first_session_factory = async_sessionmaker(first_engine, expire_on_commit=False)
    first = journal(
        first_session_factory,
        pending_lease_seconds=0.5,
    )
    await reset_journal_database(first_session_factory)
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
        await clear_journal_database(async_sessionmaker(restarted_engine, expire_on_commit=False))
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
async def test_lease_expiring_while_row_lock_waits_becomes_unknown_without_post() -> None:
    engine = create_async_engine(require_database_url())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await reset_journal_database(session_factory)
    service = journal(session_factory, pending_lease_seconds=1)
    provider = BlockingBeforeSendProvider()
    writer = ContactWriter(service, UnusedOwnershipStore())  # type: ignore[arg-type]
    current_access = access(f"journal-{uuid.uuid4().hex}")
    request_id = uuid.uuid4()
    try:
        pending = asyncio.create_task(
            writer.create(
                current_access,
                provider,  # type: ignore[arg-type]
                request_id=request_id,
                provider_account=current_access.provider_account,
                idempotency_key="lock-expiry-key",
                name="Synthetic Contact",
                detail_type="email",
                detail_data="contact@example.invalid",
            )
        )
        await asyncio.wait_for(provider.before_send_ready.wait(), timeout=1)
        async with session_factory() as blocker, blocker.begin():
            locked = await blocker.scalar(
                select(OperationJournal)
                .where(OperationJournal.request_id == request_id)
                .with_for_update()
            )
            assert locked is not None
            checked_at = datetime.now(UTC)
            assert locked.pending_expires_at > checked_at
            provider.release_before_send.set()
            await asyncio.sleep((locked.pending_expires_at - checked_at).total_seconds() + 0.05)

        outcome = await asyncio.wait_for(pending, timeout=1)
        async with session_factory() as session:
            stored = await session.get(OperationJournal, request_id)
    finally:
        await clear_journal_database(session_factory)
        await engine.dispose()

    assert outcome.state is ContactCreateState.UNKNOWN
    assert provider.calls == 0
    assert stored is not None
    assert stored.state == OperationState.UNKNOWN.value
    assert stored.dispatch_started_at is None


@pytest.mark.asyncio
async def test_success_and_failed_state_are_durable_and_no_key_is_not_deduplicated() -> None:
    engine = create_async_engine(require_database_url())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await reset_journal_database(session_factory)
    service = journal(session_factory)
    current_access = access(f"journal-{uuid.uuid4().hex}")
    try:
        opaque_key = "person@example.invalid bearer-secret-sentinel"
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
        await clear_journal_database(session_factory)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_state", [OperationState.SUCCEEDED, OperationState.UNKNOWN])
async def test_changed_digest_secret_fails_closed_without_a_second_record(
    initial_state: OperationState,
) -> None:
    engine = create_async_engine(require_database_url())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await reset_journal_database(session_factory)
    first = journal(session_factory, digest_secret=b"first-operation-journal-secret")
    rotated = journal(session_factory, digest_secret=b"rotated-operation-journal-secret")
    current_access = access(f"journal-{uuid.uuid4().hex}")
    try:
        begun = await first.begin(
            current_access,
            request_id=uuid.uuid4(),
            operation="C02",
            idempotency_key="stable-key",
            payload=payload("stable"),
        )
        await first.mark_dispatch_started(begun.record.request_id)
        if initial_state is OperationState.SUCCEEDED:
            await first.mark_succeeded(
                begun.record.request_id,
                {"id": 20001, "project_id": 10001},
            )

        with pytest.raises(OperationDigestKeyMismatch):
            await rotated.begin(
                current_access,
                request_id=uuid.uuid4(),
                operation="C02",
                idempotency_key="stable-key",
                payload=payload("stable"),
            )

        async with session_factory() as session:
            record_count = await session.scalar(select(func.count()).select_from(OperationJournal))
    finally:
        await clear_journal_database(session_factory)
        await engine.dispose()

    assert record_count == 1
