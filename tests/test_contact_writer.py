from __future__ import annotations

import uuid

import pytest

from proxy_api.auth.service import AuthenticatedSession, AuthenticationFailed
from proxy_api.contacts.service import ContactCreateState, ContactWriter
from proxy_api.operations import (
    BeginOperation,
    OperationRecord,
    OperationState,
    OperationTransitionRejected,
    payload_fingerprint,
)
from proxy_api.projects.routes import ClientAuthenticationError
from proxy_api.provider import ProviderDeadlineExceeded
from proxy_api.reads.detail_ownership import VerifiedContact


class FakeJournal:
    def __init__(self) -> None:
        self.records: dict[str, OperationRecord] = {}
        self.transitions: list[tuple[str, uuid.UUID]] = []
        self.begin_error: Exception | None = None
        self.dispatch_error: Exception | None = None

    async def begin(
        self,
        access: AuthenticatedSession,
        *,
        request_id: uuid.UUID,
        operation: str,
        idempotency_key: str | None,
        payload: dict[str, object],
    ) -> BeginOperation:
        del access, operation
        if self.begin_error is not None:
            raise self.begin_error
        fingerprint = self.payload_fingerprint(payload)
        if idempotency_key is not None and idempotency_key in self.records:
            return BeginOperation(self.records[idempotency_key], execute=False)
        record = OperationRecord(request_id, fingerprint, OperationState.PENDING, None)
        if idempotency_key is not None:
            self.records[idempotency_key] = record
        return BeginOperation(record, execute=True)

    @staticmethod
    def payload_fingerprint(payload: dict[str, object]) -> str:
        return payload_fingerprint(payload, b"test-contact-writer-secret")

    async def mark_dispatch_started(self, request_id: uuid.UUID) -> None:
        if self.dispatch_error is not None:
            raise self.dispatch_error
        self.transitions.append(("unknown", request_id))
        self._replace(request_id, state=OperationState.UNKNOWN)

    async def mark_failed_before_send(self, request_id: uuid.UUID) -> None:
        self.transitions.append(("failed", request_id))
        self._replace(request_id, state=OperationState.FAILED)

    async def mark_succeeded(
        self,
        request_id: uuid.UUID,
        safe_result: dict[str, object],
    ) -> None:
        self.transitions.append(("succeeded", request_id))
        self._replace(request_id, state=OperationState.SUCCEEDED, safe_result=safe_result)

    def _replace(
        self,
        request_id: uuid.UUID,
        *,
        state: OperationState,
        safe_result: dict[str, object] | None = None,
    ) -> None:
        for key, record in self.records.items():
            if record.request_id == request_id:
                self.records[key] = OperationRecord(
                    request_id,
                    record.fingerprint,
                    state,
                    safe_result,
                )


class FakeProvider:
    def __init__(self, project_id: int = 10001) -> None:
        self.calls: list[dict[str, object]] = []
        self.rechecks = 0
        self.error_before_send: Exception | None = None
        self.error_after_send: Exception | None = None
        self.recheck_error: Exception | None = None
        self.response: object = {
            "status": "success",
            "result": {
                "id": 20001,
                "project_id": project_id,
                "name": "Synthetic Contact",
                "details": [
                    {
                        "id": 20101,
                        "type": "email",
                        "data": "contact@example.invalid",
                    }
                ],
            },
        }

    async def post_once(
        self,
        path: str,
        *,
        json_body: dict[str, object],
        before_send: object,
        deadline_seconds: float | None = None,
    ) -> object:
        del deadline_seconds
        if self.error_before_send is not None:
            raise self.error_before_send
        await before_send()  # type: ignore[operator]
        self.calls.append({"path": path, "json": json_body})
        if self.error_after_send is not None:
            raise self.error_after_send
        return self.response

    async def recheck_access(self) -> None:
        self.rechecks += 1
        if self.recheck_error is not None:
            raise self.recheck_error


class FakeOwnershipStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[VerifiedContact]]] = []

    async def sync_verified_contacts(
        self,
        provider_account: str,
        contacts: list[VerifiedContact],
    ) -> None:
        self.calls.append((provider_account, contacts))


def access(project_id: int = 10001) -> AuthenticatedSession:
    return AuthenticatedSession("session-a", "client-a", "primary", project_id)


def test_payload_fingerprint_is_canonical_and_contains_no_plaintext() -> None:
    first = {
        "project_id": 10001,
        "name": "Synthetic Contact",
        "details": [{"type": "email", "data": "contact@example.invalid"}],
    }
    reordered = {
        "details": [{"data": "contact@example.invalid", "type": "email"}],
        "name": "Synthetic Contact",
        "project_id": 10001,
    }

    fingerprint = payload_fingerprint(first, b"test-contact-writer-secret")

    assert fingerprint == payload_fingerprint(reordered, b"test-contact-writer-secret")
    assert len(fingerprint) == 64
    assert "contact@example.invalid" not in fingerprint


async def create(
    writer: ContactWriter,
    provider: FakeProvider,
    *,
    key: str | None = "contact-key",
    current_access: AuthenticatedSession | None = None,
) -> object:
    return await writer.create(
        current_access or access(),
        provider,  # type: ignore[arg-type]
        request_id=uuid.uuid4(),
        provider_account="primary",
        idempotency_key=key,
        name="Synthetic Contact",
        detail_type="email",
        detail_data="contact@example.invalid",
    )


@pytest.mark.asyncio
async def test_create_uses_server_project_and_stores_only_safe_result() -> None:
    journal = FakeJournal()
    ownership = FakeOwnershipStore()
    provider = FakeProvider()
    writer = ContactWriter(journal, ownership)  # type: ignore[arg-type]

    outcome = await create(writer, provider)

    assert outcome.state is ContactCreateState.SUCCEEDED
    assert outcome.result == {
        "id": 20001,
        "project_id": 10001,
        "name": "Synthetic Contact",
        "details": [
            {
                "id": 20101,
                "type": "email",
                "data": "contact@example.invalid",
            }
        ],
    }
    assert provider.calls == [
        {
            "path": "/contact",
            "json": {
                "project_id": 10001,
                "name": "Synthetic Contact",
                "details": [{"type": "email", "data": "contact@example.invalid"}],
            },
        }
    ]
    assert [state for state, _ in journal.transitions] == ["unknown", "succeeded"]
    assert ownership.calls[0][0] == "primary"
    assert ownership.calls[0][1][0].detail_ids == frozenset({20101})
    stored = journal.records["contact-key"].safe_result
    assert stored == {
        "id": 20001,
        "project_id": 10001,
        "details": [{"id": 20101, "type": "email"}],
    }
    assert "contact@example.invalid" not in str(stored)
    assert "Synthetic Contact" not in str(stored)


@pytest.mark.asyncio
async def test_same_key_replays_success_after_current_access_recheck() -> None:
    journal = FakeJournal()
    ownership = FakeOwnershipStore()
    provider = FakeProvider()
    writer = ContactWriter(journal, ownership)  # type: ignore[arg-type]

    first = await create(writer, provider)
    replay = await create(writer, provider)

    assert first.state is ContactCreateState.SUCCEEDED
    assert replay.state is ContactCreateState.REPLAYED
    assert replay.request_id == first.request_id
    assert replay.result == first.result
    assert len(provider.calls) == 1
    assert provider.rechecks == 1


@pytest.mark.asyncio
async def test_success_replay_fails_if_current_access_was_revoked() -> None:
    journal = FakeJournal()
    provider = FakeProvider()
    writer = ContactWriter(journal, FakeOwnershipStore())  # type: ignore[arg-type]
    await create(writer, provider)
    provider.recheck_error = AuthenticationFailed("Authentication failed")

    with pytest.raises(AuthenticationFailed):
        await create(writer, provider)

    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_same_key_with_different_payload_conflicts_without_post() -> None:
    journal = FakeJournal()
    provider = FakeProvider()
    writer = ContactWriter(journal, FakeOwnershipStore())  # type: ignore[arg-type]
    first = await create(writer, provider)

    conflict = await writer.create(
        access(),
        provider,  # type: ignore[arg-type]
        request_id=uuid.uuid4(),
        provider_account="primary",
        idempotency_key="contact-key",
        name="Different Contact",
        detail_type="email",
        detail_data="different@example.invalid",
    )

    assert conflict.state is ContactCreateState.CONFLICT
    assert conflict.request_id == first.request_id
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_timeout_after_send_is_unknown_and_never_replayed() -> None:
    journal = FakeJournal()
    provider = FakeProvider()
    provider.error_after_send = ProviderDeadlineExceeded("Provider deadline exceeded")
    writer = ContactWriter(journal, FakeOwnershipStore())  # type: ignore[arg-type]

    first = await create(writer, provider)
    replay = await create(writer, provider)

    assert first.state is ContactCreateState.UNKNOWN
    assert replay.state is ContactCreateState.UNKNOWN
    assert replay.request_id == first.request_id
    assert len(provider.calls) == 1
    assert [state for state, _ in journal.transitions] == ["unknown"]


@pytest.mark.asyncio
async def test_pre_send_failure_is_durable_failed_without_post() -> None:
    journal = FakeJournal()
    provider = FakeProvider()
    provider.error_before_send = ProviderDeadlineExceeded("Provider deadline exceeded")
    writer = ContactWriter(journal, FakeOwnershipStore())  # type: ignore[arg-type]

    first = await create(writer, provider)
    replay = await create(writer, provider)

    assert first.state is ContactCreateState.FAILED
    assert replay.state is ContactCreateState.FAILED
    assert provider.calls == []
    assert [state for state, _ in journal.transitions] == ["failed"]


@pytest.mark.asyncio
async def test_journal_failure_is_failed_without_provider_send() -> None:
    journal = FakeJournal()
    journal.begin_error = RuntimeError("journal unavailable")
    provider = FakeProvider()
    writer = ContactWriter(journal, FakeOwnershipStore())  # type: ignore[arg-type]

    outcome = await create(writer, provider)

    assert outcome.state is ContactCreateState.FAILED
    assert provider.calls == []
    assert journal.transitions == []


@pytest.mark.asyncio
async def test_revocation_before_business_send_fails_journal_and_preserves_auth_error() -> None:
    journal = FakeJournal()
    provider = FakeProvider()
    provider.error_before_send = ClientAuthenticationError()
    writer = ContactWriter(journal, FakeOwnershipStore())  # type: ignore[arg-type]

    with pytest.raises(ClientAuthenticationError):
        await create(writer, provider)

    assert provider.calls == []
    assert [state for state, _ in journal.transitions] == ["failed"]


@pytest.mark.asyncio
async def test_expired_pending_executor_cannot_send_and_returns_unknown() -> None:
    journal = FakeJournal()
    journal.dispatch_error = OperationTransitionRejected("pending lease expired")
    provider = FakeProvider()
    writer = ContactWriter(journal, FakeOwnershipStore())  # type: ignore[arg-type]

    outcome = await create(writer, provider)

    assert outcome.state is ContactCreateState.UNKNOWN
    assert provider.calls == []
    assert journal.transitions == []


@pytest.mark.asyncio
async def test_wrong_upstream_project_is_unknown_and_not_disclosed() -> None:
    journal = FakeJournal()
    provider = FakeProvider(project_id=10002)
    writer = ContactWriter(journal, FakeOwnershipStore())  # type: ignore[arg-type]

    outcome = await create(writer, provider)

    assert outcome.state is ContactCreateState.UNKNOWN
    assert outcome.result is None
    assert len(provider.calls) == 1
    assert [state for state, _ in journal.transitions] == ["unknown"]
