from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from proxy_api.auth.routes import get_auth_service
from proxy_api.auth.service import AuthenticatedSession, AuthenticationFailed
from proxy_api.contacts.routes import get_contact_mutation_writer
from proxy_api.contacts.service import ContactCreateOutcome, ContactCreateState
from proxy_api.infrastructure import get_provider_adapter
from proxy_api.main import create_app
from proxy_api.projects.routes import get_configured_provider_account
from proxy_api.reads.detail_ownership import DetailOwnership
from proxy_api.reads.routes import get_detail_ownership_store


@asynccontextmanager
async def no_op_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


class FakeAuthService:
    sessions = {
        "token-a": AuthenticatedSession("session-a", "client-a", "primary", 10001),
        "token-b": AuthenticatedSession("session-b", "client-b", "primary", 10002),
    }

    async def authenticate(self, token: str) -> AuthenticatedSession:
        try:
            return self.sessions[token]
        except KeyError:
            raise AuthenticationFailed("Authentication failed") from None

    @asynccontextmanager
    async def provider_request_guard(
        self,
        access: AuthenticatedSession,
    ) -> AsyncIterator[None]:
        if access not in self.sessions.values():
            raise AuthenticationFailed("Authentication failed")
        yield


class FakeAdapter:
    def __init__(self) -> None:
        self.responses: dict[tuple[str, str], object] = {}
        self.calls: list[tuple[str, str]] = []

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | Sequence[tuple[str, str | int]] | None = None,
        deadline_seconds: float | None = None,
        send_guard: Callable[[], AbstractAsyncContextManager[None]] | None = None,
    ) -> object:
        del params, deadline_seconds
        if send_guard is not None:
            async with send_guard():
                pass
        self.calls.append(("GET", path))
        return self.responses[("GET", path)]

    async def write_once(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, object] | None,
        before_send: Callable[[], Awaitable[None]],
        deadline_seconds: float | None = None,
        send_guard: Callable[[], AbstractAsyncContextManager[None]] | None = None,
    ) -> object:
        del json_body, deadline_seconds
        if send_guard is not None:
            async with send_guard():
                await before_send()
        else:
            await before_send()
        self.calls.append((method, path))
        return self.responses[(method, path)]


class FakeOwnershipStore:
    def __init__(self) -> None:
        self.ownership: DetailOwnership | None = None
        self.synced: list[object] = []

    async def get(self, provider_account: str, detail_id: object) -> DetailOwnership | None:
        del provider_account, detail_id
        return self.ownership

    async def is_current(self, ownership: DetailOwnership) -> bool:
        return ownership == self.ownership

    async def invalidate_if_matches(self, ownership: DetailOwnership) -> None:
        if ownership == self.ownership:
            self.ownership = None

    async def sync_verified_contacts(self, provider_account: str, contacts: list[object]) -> None:
        del provider_account
        self.synced.extend(contacts)


class FakeMutationWriter:
    def __init__(self) -> None:
        self.state = ContactCreateState.SUCCEEDED
        self.calls: list[dict[str, object]] = []

    async def mutate(
        self, access: AuthenticatedSession, provider: object, **kwargs: Any
    ) -> ContactCreateOutcome:
        self.calls.append({"access": access, "provider": provider, **kwargs})
        result: dict[str, object] | None = {}
        if kwargs["operation"] == "C07":
            result = {"id": 501, "type": "email"}
        if self.state not in {ContactCreateState.SUCCEEDED, ContactCreateState.REPLAYED}:
            result = None
        return ContactCreateOutcome(kwargs["request_id"], self.state, result)


def contact(project_id: int, contact_id: int = 101) -> dict[str, object]:
    return {
        "status": "success",
        "result": {
            "id": contact_id,
            "project_id": project_id,
            "name": "Synthetic",
            "details": [{"id": 501, "type": "email", "data": "safe@example.invalid"}],
            "fields": [],
        },
    }


def make_client() -> tuple[TestClient, FakeAdapter, FakeOwnershipStore, FakeMutationWriter]:
    application = create_app()
    application.router.lifespan_context = no_op_lifespan
    adapter = FakeAdapter()
    ownership = FakeOwnershipStore()
    writer = FakeMutationWriter()
    application.dependency_overrides[get_auth_service] = FakeAuthService
    application.dependency_overrides[get_provider_adapter] = lambda: adapter
    application.dependency_overrides[get_configured_provider_account] = lambda: "primary"
    application.dependency_overrides[get_detail_ownership_store] = lambda: ownership
    application.dependency_overrides[get_contact_mutation_writer] = lambda: writer
    return TestClient(application), adapter, ownership, writer


def test_deferred_and_unverified_writes_do_not_reach_provider() -> None:
    client, adapter, _, writer = make_client()

    with client:
        deferred = client.delete("/contact/details/501", headers={"token": "token-a"})
        c04 = client.put("/contact/101", headers={"token": "token-a"}, json={"name": "x"})
        c05 = client.delete("/contact/101", headers={"token": "token-a"})
        c11 = client.put(
            "/contact/101/field/701",
            headers={"token": "token-a"},
            json={"value": "x"},
        )
        unauthenticated = client.delete("/contact/details/501")

    assert deferred.json() == {
        "status": "error",
        "errors": [{"code": 501, "message": "Operation is not available in this release"}],
    }
    for response in (c04, c05, c11):
        assert response.json()["errors"][0]["code"] == 503
    assert unauthenticated.json()["errors"][0]["code"] == 401
    assert adapter.calls == []
    assert writer.calls == []


@pytest.mark.parametrize(
    "path",
    [
        "/contact/details/501",
        "/contact/details/arbitrary?project_id=10002",
        "/contact/details/501?project_id=10001&project_id=10002",
    ],
)
def test_c08_is_fixed_after_auth_without_object_or_provider_lookup(path: str) -> None:
    client, adapter, ownership, writer = make_client()
    with client:
        allowed = client.delete(path, headers={"token": "token-a"})
        rejected = client.delete(path, headers={"token": "invalid"})

    assert allowed.status_code == 200
    assert allowed.json() == {
        "status": "error",
        "errors": [{"code": 501, "message": "Operation is not available in this release"}],
    }
    assert rejected.json()["errors"][0]["code"] == 401
    assert ownership.synced == []
    assert adapter.calls == []
    assert writer.calls == []


def test_c06_requires_active_mapping_and_rechecks_parent() -> None:
    client, adapter, ownership, _ = make_client()

    with client:
        unknown = client.get("/contact/details/501", headers={"token": "token-a"})
    assert unknown.json()["errors"][0]["code"] == 404
    assert adapter.calls == []

    ownership.ownership = DetailOwnership(
        "primary",
        "501",
        "101",
        10001,
        True,
        datetime.now(UTC),
    )
    adapter.responses[("GET", "/contact/101")] = contact(10001)
    adapter.responses[("GET", "/contact/details/501")] = {
        "status": "success",
        "result": {"id": 501, "type": "email", "data": "safe@example.invalid"},
    }

    with client:
        allowed = client.get("/contact/details/501", headers={"token": "token-a"})

    assert allowed.json()["result"] == {
        "id": 501,
        "type": "email",
        "data": "safe@example.invalid",
    }
    assert adapter.calls == [
        ("GET", "/contact/101"),
        ("GET", "/contact/details/501"),
    ]


@pytest.mark.parametrize(
    "foreign_link",
    [
        {"project_id": 10002},
        {"metadata": {"last_project_id": 10002}},
    ],
)
def test_c06_rejects_foreign_detail_marker_without_disclosing_data(
    foreign_link: dict[str, object],
) -> None:
    client, adapter, ownership, _ = make_client()
    ownership.ownership = DetailOwnership("primary", "501", "101", 10001, True, datetime.now(UTC))
    adapter.responses[("GET", "/contact/101")] = contact(10001)
    adapter.responses[("GET", "/contact/details/501")] = {
        "status": "success",
        "result": {
            "id": 501,
            "type": "email",
            "data": "foreign-secret@example.invalid",
            **foreign_link,
        },
    }

    with client:
        response = client.get("/contact/details/501", headers={"token": "token-a"})

    assert response.json()["errors"][0]["code"] == 404
    assert "foreign-secret" not in response.text


@pytest.mark.parametrize(
    "parent_link",
    [
        {"contact_id": 202},
        {"person_id": 202},
        {"parent_id": 202},
        {"contact": {"id": 202}},
    ],
)
def test_c06_rejects_contradictory_parent_without_disclosing_detail(
    parent_link: dict[str, object],
) -> None:
    client, adapter, ownership, _ = make_client()
    ownership.ownership = DetailOwnership("primary", "501", "101", 10001, True, datetime.now(UTC))
    adapter.responses[("GET", "/contact/101")] = contact(10001)
    adapter.responses[("GET", "/contact/details/501")] = {
        "status": "success",
        "result": {
            "id": 501,
            "type": "email",
            "data": "foreign-secret@example.invalid",
            **parent_link,
        },
    }

    with client:
        response = client.get("/contact/details/501", headers={"token": "token-a"})

    assert response.json()["errors"][0]["code"] == 404
    assert "foreign-secret" not in response.text


def test_c06_accepts_matching_parent_marker() -> None:
    client, adapter, ownership, _ = make_client()
    ownership.ownership = DetailOwnership("primary", "501", "101", 10001, True, datetime.now(UTC))
    adapter.responses[("GET", "/contact/101")] = contact(10001)
    adapter.responses[("GET", "/contact/details/501")] = {
        "status": "success",
        "result": {
            "id": 501,
            "type": "email",
            "data": "safe@example.invalid",
            "contact_id": 101,
            "contact": {"id": 101},
        },
    }

    with client:
        response = client.get("/contact/details/501", headers={"token": "token-a"})

    assert response.json()["result"]["data"] == "safe@example.invalid"


def test_c07_uses_parent_mapping_and_journal_writer() -> None:
    client, adapter, ownership, writer = make_client()
    ownership.ownership = DetailOwnership(
        "primary",
        "501",
        "101",
        10001,
        True,
        datetime.now(UTC),
    )
    adapter.responses[("GET", "/contact/101")] = contact(10001)

    with client:
        response = client.put(
            "/contact/details/501",
            headers={"token": "token-a", "Idempotency-Key": "detail-key"},
            json={"value": "changed@example.invalid"},
        )

    assert response.json()["result"] == {
        "id": 501,
        "type": "email",
        "data": "changed@example.invalid",
    }
    call = writer.calls[0]
    assert call["operation"] == "C07"
    assert call["method"] == "PUT"
    assert call["path"] == "/contact/details/501"
    assert call["idempotency_key"] == "detail-key"


def test_c09_rejects_entire_mixed_lead_list() -> None:
    client, adapter, _, _ = make_client()
    adapter.responses[("GET", "/contact/101")] = contact(10001)
    adapter.responses[("GET", "/contact/101/leads")] = {
        "status": "success",
        "result": [
            {"id": 801, "contact_id": 101},
            {"id": 802, "contact_id": 101},
        ],
    }
    adapter.responses[("GET", "/lead/801")] = {
        "status": "success",
        "result": {
            "id": 801,
            "contact_id": 101,
            "contact": contact(10001)["result"],
        },
    }
    adapter.responses[("GET", "/lead/802")] = {
        "status": "success",
        "result": {
            "id": 802,
            "contact_id": 101,
            "contact": contact(10002)["result"],
        },
    }

    with client:
        response = client.get("/contact/101/leads", headers={"token": "token-a"})

    assert response.json()["errors"][0]["code"] == 404
    assert ("GET", "/lead/802") in adapter.calls


@pytest.mark.parametrize("foreign_link", [{"project_id": 10002}, {"last_project_id": 10002}])
def test_c09_rejects_foreign_list_item_before_lead_reads(
    foreign_link: dict[str, int],
) -> None:
    client, adapter, _, _ = make_client()
    adapter.responses[("GET", "/contact/101")] = contact(10001)
    adapter.responses[("GET", "/contact/101/leads")] = {
        "status": "success",
        "result": [
            {"id": 801, "contact_id": 101, "name": "safe"},
            {"id": 802, "contact_id": 101, "name": "foreign-secret", **foreign_link},
        ],
    }

    with client:
        response = client.get("/contact/101/leads", headers={"token": "token-a"})

    assert response.json()["errors"][0]["code"] == 404
    assert "foreign-secret" not in response.text
    assert not any(path.startswith("/lead/") for _, path in adapter.calls)


def test_c09_rejects_nested_contact_conflict_for_entire_list() -> None:
    client, adapter, _, _ = make_client()
    adapter.responses[("GET", "/contact/101")] = contact(10001)
    adapter.responses[("GET", "/contact/101/leads")] = {
        "status": "success",
        "result": [
            {"id": 801, "contact_id": 101, "contact": {"id": 101}},
            {
                "id": 802,
                "contact_id": 101,
                "contact": {"id": 202},
                "name": "foreign-secret",
            },
        ],
    }
    adapter.responses[("GET", "/lead/801")] = {
        "status": "success",
        "result": {"id": 801, "contact_id": 101, "contact": contact(10001)["result"]},
    }
    adapter.responses[("GET", "/lead/802")] = {
        "status": "success",
        "result": {"id": 802, "contact_id": 101, "contact": contact(10001)["result"]},
    }

    with client:
        response = client.get("/contact/101/leads", headers={"token": "token-a"})

    assert response.json()["errors"][0]["code"] == 404
    assert "foreign-secret" not in response.text
    assert not any(path.startswith("/lead/") for _, path in adapter.calls)


def test_c09_accepts_consistent_nested_contact_marker() -> None:
    client, adapter, _, _ = make_client()
    adapter.responses[("GET", "/contact/101")] = contact(10001)
    adapter.responses[("GET", "/contact/101/leads")] = {
        "status": "success",
        "result": [{"id": 801, "contact_id": 101, "contact": {"id": 101}}],
    }
    adapter.responses[("GET", "/lead/801")] = {
        "status": "success",
        "result": {"id": 801, "contact_id": 101, "contact": contact(10001)["result"]},
    }

    with client:
        response = client.get("/contact/101/leads", headers={"token": "token-a"})

    assert response.json()["result"][0]["id"] == 801


def test_c10_and_c12_verify_contact_and_project_field() -> None:
    client, adapter, _, writer = make_client()
    adapter.responses[("GET", "/contact/101")] = contact(10001)
    adapter.responses[("GET", "/project/10001/fields")] = {
        "status": "success",
        "result": [{"id": 701, "name": "Safe field", "type": "string"}],
    }
    adapter.responses[("GET", "/contact/101/field/701")] = {
        "status": "success",
        "result": {"id": 701, "name": "Safe field", "type": "string", "value": "safe"},
    }

    with client:
        read = client.get("/contact/101/field/701", headers={"token": "token-a"})
        cleared = client.delete(
            "/contact/101/field/701",
            headers={"token": "token-a", "Idempotency-Key": "clear-key"},
        )

    assert read.json()["result"]["value"] == "safe"
    assert cleared.json() == {"status": "success"}
    call = writer.calls[0]
    assert call["operation"] == "C12"
    assert call["method"] == "DELETE"
    assert call["path"] == "/contact/101/field/701"
    assert call["idempotency_key"] == "clear-key"


@pytest.mark.parametrize(
    "foreign_link", [{"project_id": 10002}, {"metadata": {"last_project_id": 10002}}]
)
def test_c10_rejects_foreign_field_marker_without_disclosing_value(
    foreign_link: dict[str, object],
) -> None:
    client, adapter, _, _ = make_client()
    adapter.responses[("GET", "/contact/101")] = contact(10001)
    adapter.responses[("GET", "/project/10001/fields")] = {
        "status": "success",
        "result": [{"id": 701, "name": "Safe field", "type": "string"}],
    }
    adapter.responses[("GET", "/contact/101/field/701")] = {
        "status": "success",
        "result": {
            "id": 701,
            "name": "Safe field",
            "type": "string",
            "value": "foreign-secret",
            **foreign_link,
        },
    }

    with client:
        response = client.get("/contact/101/field/701", headers={"token": "token-a"})

    assert response.json()["errors"][0]["code"] == 404
    assert "foreign-secret" not in response.text


@pytest.mark.parametrize(
    "parent_link",
    [
        {"contact_id": 202},
        {"person_id": 202},
        {"parent_id": 202},
        {"contact": {"id": 202}},
    ],
)
def test_c10_rejects_contradictory_parent_without_disclosing_value(
    parent_link: dict[str, object],
) -> None:
    client, adapter, _, _ = make_client()
    adapter.responses[("GET", "/contact/101")] = contact(10001)
    adapter.responses[("GET", "/project/10001/fields")] = {
        "status": "success",
        "result": [{"id": 701, "name": "Safe field", "type": "string"}],
    }
    adapter.responses[("GET", "/contact/101/field/701")] = {
        "status": "success",
        "result": {
            "id": 701,
            "name": "Safe field",
            "type": "string",
            "value": "foreign-secret",
            **parent_link,
        },
    }

    with client:
        response = client.get("/contact/101/field/701", headers={"token": "token-a"})

    assert response.json()["errors"][0]["code"] == 404
    assert "foreign-secret" not in response.text


def test_c10_accepts_matching_parent_marker() -> None:
    client, adapter, _, _ = make_client()
    adapter.responses[("GET", "/contact/101")] = contact(10001)
    adapter.responses[("GET", "/project/10001/fields")] = {
        "status": "success",
        "result": [{"id": 701, "name": "Safe field", "type": "string"}],
    }
    adapter.responses[("GET", "/contact/101/field/701")] = {
        "status": "success",
        "result": {
            "id": 701,
            "name": "Safe field",
            "type": "string",
            "value": "safe",
            "contact_id": 101,
            "contact": {"id": 101},
        },
    }

    with client:
        response = client.get("/contact/101/field/701", headers={"token": "token-a"})

    assert response.json()["result"]["value"] == "safe"


def test_foreign_or_repeated_project_query_stops_before_provider() -> None:
    client, adapter, ownership, writer = make_client()
    ownership.ownership = DetailOwnership(
        "primary",
        "501",
        "101",
        10001,
        True,
        datetime.now(UTC),
    )

    with client:
        foreign = client.put(
            "/contact/details/501?project_id=10002",
            headers={"token": "token-a"},
            json={"value": "changed@example.invalid"},
        )
        repeated = client.get(
            "/contact/101/field/701",
            params=[("project_id", "10001"), ("project_id", "10001")],
            headers={"token": "token-a"},
        )

    assert foreign.json()["errors"][0]["code"] == 404
    assert repeated.json()["errors"][0]["code"] == 404
    assert adapter.calls == []
    assert writer.calls == []
