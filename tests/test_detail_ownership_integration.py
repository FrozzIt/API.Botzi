from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from proxy_api.auth.service import AuthenticatedSession
from proxy_api.reads.detail_ownership import (
    DetailOwnershipConflict,
    DetailOwnershipStore,
    VerifiedContact,
)
from proxy_api.reads.routes import ReadAccessDenied, verify_contact_detail_parent

pytestmark = pytest.mark.integration


def require_database_url() -> str:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("RUN_INTEGRATION_TESTS=1 is required")
    value = os.getenv("DATABASE_URL")
    if value is None:
        raise RuntimeError("DATABASE_URL is required for integration tests")
    return value


def unique_account() -> str:
    return f"detail-test-{uuid.uuid4().hex}"


@pytest.mark.asyncio
async def test_mapping_persists_across_engine_restart() -> None:
    database_url = require_database_url()
    provider_account = unique_account()
    engine = create_async_engine(database_url)
    first_store = DetailOwnershipStore(async_sessionmaker(engine, expire_on_commit=False))
    await first_store.sync_verified_contacts(
        provider_account,
        [VerifiedContact(20001, 10001, frozenset({20101}))],
    )
    await engine.dispose()

    restarted_engine = create_async_engine(database_url)
    restarted_store = DetailOwnershipStore(
        async_sessionmaker(restarted_engine, expire_on_commit=False)
    )
    try:
        ownership = await restarted_store.get(provider_account, 20101)
    finally:
        await restarted_engine.dispose()

    assert ownership is not None
    assert ownership.provider_account == provider_account
    assert ownership.detail_id == "20101"
    assert ownership.contact_id == "20001"
    assert ownership.project_id == 10001
    assert ownership.active is True


@pytest.mark.asyncio
async def test_contact_refresh_deactivates_details_no_longer_present() -> None:
    engine = create_async_engine(require_database_url())
    store = DetailOwnershipStore(async_sessionmaker(engine, expire_on_commit=False))
    provider_account = unique_account()
    try:
        await store.sync_verified_contacts(
            provider_account,
            [VerifiedContact(20001, 10001, frozenset({20101, 20102}), details_complete=True)],
        )
        await store.sync_verified_contacts(
            provider_account,
            [VerifiedContact(20001, 10001, frozenset({20102}), details_complete=True)],
        )

        removed = await store.get(provider_account, 20101)
        retained = await store.get(provider_account, 20102)
    finally:
        await engine.dispose()

    assert removed is not None
    assert removed.active is False
    assert retained is not None
    assert retained.contact_id == "20001"
    assert retained.project_id == 10001
    assert retained.active is True


@pytest.mark.asyncio
async def test_conflicting_reassignment_is_atomic_and_not_silent() -> None:
    engine = create_async_engine(require_database_url())
    store = DetailOwnershipStore(async_sessionmaker(engine, expire_on_commit=False))
    provider_account = unique_account()
    try:
        await store.sync_verified_contacts(
            provider_account,
            [VerifiedContact(20001, 10001, frozenset({20101}))],
        )

        with pytest.raises(DetailOwnershipConflict):
            await store.sync_verified_contacts(
                provider_account,
                [VerifiedContact(20002, 10002, frozenset({20101, 20102}))],
            )

        original = await store.get(provider_account, 20101)
        partial = await store.get(provider_account, 20102)
    finally:
        await engine.dispose()

    assert original is not None
    assert original.contact_id == "20001"
    assert original.project_id == 10001
    assert partial is None


@pytest.mark.asyncio
async def test_detail_cannot_be_rebound_after_complete_sync_deactivates_it() -> None:
    engine = create_async_engine(require_database_url())
    store = DetailOwnershipStore(async_sessionmaker(engine, expire_on_commit=False))
    provider_account = unique_account()
    try:
        await store.sync_verified_contacts(
            provider_account,
            [VerifiedContact(20001, 10001, frozenset({20101}), details_complete=True)],
        )
        await store.sync_verified_contacts(
            provider_account,
            [VerifiedContact(20001, 10001, frozenset(), details_complete=True)],
        )

        with pytest.raises(DetailOwnershipConflict):
            await store.sync_verified_contacts(
                provider_account,
                [VerifiedContact(20002, 10002, frozenset({20101}))],
            )

        historical = await store.get(provider_account, 20101)
    finally:
        await engine.dispose()

    assert historical is not None
    assert historical.contact_id == "20001"
    assert historical.project_id == 10001
    assert historical.active is False


@pytest.mark.asyncio
async def test_detail_cannot_be_rebound_after_explicit_invalidation() -> None:
    engine = create_async_engine(require_database_url())
    store = DetailOwnershipStore(async_sessionmaker(engine, expire_on_commit=False))
    provider_account = unique_account()
    try:
        await store.sync_verified_contacts(
            provider_account,
            [VerifiedContact(20001, 10001, frozenset({20101}))],
        )
        ownership = await store.get(provider_account, 20101)
        assert ownership is not None
        await store.invalidate_if_matches(ownership)

        with pytest.raises(DetailOwnershipConflict):
            await store.sync_verified_contacts(
                provider_account,
                [VerifiedContact(20002, 10002, frozenset({20101}))],
            )

        historical = await store.get(provider_account, 20101)
    finally:
        await engine.dispose()

    assert historical is not None
    assert historical.contact_id == "20001"
    assert historical.project_id == 10001
    assert historical.active is False


@pytest.mark.asyncio
async def test_incomplete_contact_observation_does_not_deactivate_existing_detail() -> None:
    engine = create_async_engine(require_database_url())
    store = DetailOwnershipStore(async_sessionmaker(engine, expire_on_commit=False))
    provider_account = unique_account()
    try:
        await store.sync_verified_contacts(
            provider_account,
            [VerifiedContact(20001, 10001, frozenset({20101}), details_complete=True)],
        )
        await store.sync_verified_contacts(
            provider_account,
            [VerifiedContact(20001, 10001, frozenset(), details_complete=False)],
        )
        ownership = await store.get(provider_account, 20101)
    finally:
        await engine.dispose()

    assert ownership is not None
    assert ownership.active is True


class IncompleteParentProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get(self, path: str, *, params: object = None) -> object:
        assert params is None
        self.calls.append(path)
        return {
            "status": "success",
            "result": {
                "id": 20001,
                "project_id": 10001,
                "fields": [],
            },
        }


@pytest.mark.asyncio
async def test_incomplete_parent_response_denies_without_erasing_real_mapping() -> None:
    engine = create_async_engine(require_database_url())
    store = DetailOwnershipStore(async_sessionmaker(engine, expire_on_commit=False))
    provider_account = unique_account()
    provider = IncompleteParentProvider()
    access = AuthenticatedSession("session-a", "client-a", provider_account, 10001)
    try:
        await store.sync_verified_contacts(
            provider_account,
            [VerifiedContact(20001, 10001, frozenset({20101}))],
        )

        with pytest.raises(ReadAccessDenied):
            await verify_contact_detail_parent(
                20101,
                access,
                provider,  # type: ignore[arg-type]
                provider_account,
                store,
            )

        ownership = await store.get(provider_account, 20101)
    finally:
        await engine.dispose()

    assert provider.calls == ["/contact/20001"]
    assert ownership is not None
    assert ownership.active is True


@pytest.mark.asyncio
async def test_inactive_real_mapping_denies_before_provider_and_still_blocks_rebinding() -> None:
    engine = create_async_engine(require_database_url())
    store = DetailOwnershipStore(async_sessionmaker(engine, expire_on_commit=False))
    provider_account = unique_account()
    provider = IncompleteParentProvider()
    access = AuthenticatedSession("session-a", "client-a", provider_account, 10001)
    try:
        await store.sync_verified_contacts(
            provider_account,
            [VerifiedContact(20001, 10001, frozenset({20101}))],
        )
        ownership = await store.get(provider_account, 20101)
        assert ownership is not None
        await store.invalidate_if_matches(ownership)

        with pytest.raises(ReadAccessDenied):
            await verify_contact_detail_parent(
                20101,
                access,
                provider,  # type: ignore[arg-type]
                provider_account,
                store,
            )
        with pytest.raises(DetailOwnershipConflict):
            await store.sync_verified_contacts(
                provider_account,
                [VerifiedContact(20002, 10002, frozenset({20101}))],
            )
    finally:
        await engine.dispose()

    assert provider.calls == []
