from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from proxy_api.auth.routes import get_auth_service
from proxy_api.auth.service import AuthenticatedSession, AuthenticationFailed
from proxy_api.contacts.routes import get_contact_writer
from proxy_api.contacts.service import ContactCreateOutcome, ContactCreateState
from proxy_api.infrastructure import get_provider_adapter
from proxy_api.main import create_app
from proxy_api.projects.routes import get_configured_provider_account


@asynccontextmanager
async def no_op_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


class FakeAuthService:
    def __init__(self) -> None:
        self.sessions = {
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


class FakeWriter:
    def __init__(self) -> None:
        self.state = ContactCreateState.SUCCEEDED
        self.calls: list[dict[str, object]] = []
        self.existing_request_id: uuid.UUID | None = None

    async def create(
        self,
        access: AuthenticatedSession,
        provider: object,
        *,
        request_id: uuid.UUID,
        provider_account: str,
        idempotency_key: str | None,
        name: str,
        detail_type: str,
        detail_data: str,
    ) -> ContactCreateOutcome:
        self.calls.append(
            {
                "access": access,
                "provider": provider,
                "request_id": request_id,
                "provider_account": provider_account,
                "idempotency_key": idempotency_key,
                "name": name,
                "detail_type": detail_type,
                "detail_data": detail_data,
            }
        )
        operation_id = self.existing_request_id or request_id
        result = None
        if self.state in {ContactCreateState.SUCCEEDED, ContactCreateState.REPLAYED}:
            result = {
                "id": 20001,
                "project_id": access.project_id,
                "name": name,
                "details": [
                    {
                        "id": 20101,
                        "type": detail_type,
                        "data": detail_data,
                    }
                ],
            }
        return ContactCreateOutcome(operation_id, self.state, result)


def make_client() -> tuple[TestClient, FakeWriter]:
    application = create_app()
    application.router.lifespan_context = no_op_lifespan
    writer = FakeWriter()
    application.dependency_overrides[get_auth_service] = FakeAuthService
    application.dependency_overrides[get_provider_adapter] = object
    application.dependency_overrides[get_configured_provider_account] = lambda: "primary"
    application.dependency_overrides[get_contact_writer] = lambda: writer
    return TestClient(application), writer


def body(project_id: int | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "name": "Synthetic Contact",
        "details": [{"type": "email", "data": "contact@example.invalid"}],
    }
    if project_id is not None:
        result["project_id"] = project_id
    return result


def test_success_returns_full_result_and_operation_request_id() -> None:
    client, writer = make_client()

    with client:
        response = client.post(
            "/contact",
            headers={"token": "token-a", "Idempotency-Key": "contact-key"},
            json=body(10001),
        )

    operation_id = uuid.UUID(response.headers["X-Request-ID"])
    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "result": {
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
        },
    }
    assert writer.calls[0]["request_id"] == operation_id
    assert writer.calls[0]["idempotency_key"] == "contact-key"
    assert writer.calls[0]["provider_account"] == "primary"


@pytest.mark.parametrize(
    ("state", "code", "message"),
    [
        (
            ContactCreateState.CONFLICT,
            409,
            "Idempotency key conflicts with another request",
        ),
        (ContactCreateState.PENDING, 423, "Operation is still in progress"),
        (ContactCreateState.UNKNOWN, 520, "Operation outcome is unknown"),
        (ContactCreateState.FAILED, 503, "Service unavailable"),
    ],
)
def test_operation_states_have_stable_codes_and_original_request_id(
    state: ContactCreateState,
    code: int,
    message: str,
) -> None:
    client, writer = make_client()
    writer.state = state
    writer.existing_request_id = uuid.uuid4()

    with client:
        response = client.post(
            "/contact",
            headers={"token": "token-a", "Idempotency-Key": "contact-key"},
            json=body(),
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == str(writer.existing_request_id)
    assert response.json() == {
        "status": "error",
        "errors": [{"code": code, "message": message}],
    }


def test_foreign_project_and_unknown_relations_are_rejected_before_writer() -> None:
    client, writer = make_client()

    with client:
        foreign = client.post(
            "/contact",
            headers={"token": "token-a"},
            json=body(10002),
        )
        extended = client.post(
            "/contact",
            headers={"token": "token-a"},
            json={**body(), "owner": 30001},
        )

    assert foreign.json() == {
        "status": "error",
        "errors": [{"code": 404, "message": "Project not found"}],
    }
    assert extended.json() == {
        "status": "error",
        "errors": [{"code": 422, "message": "Request is not valid"}],
    }
    assert uuid.UUID(foreign.headers["X-Request-ID"])
    assert uuid.UUID(extended.headers["X-Request-ID"])
    assert writer.calls == []


def test_invalid_auth_and_duplicate_idempotency_headers_do_not_reach_writer() -> None:
    client, writer = make_client()

    with client:
        denied = client.post(
            "/contact",
            headers={"token": "invalid", "Idempotency-Key": "contact-key"},
            json=body(),
        )
        duplicate = client.post(
            "/contact",
            headers=[
                ("token", "token-a"),
                ("Idempotency-Key", "first"),
                ("Idempotency-Key", "second"),
            ],
            json=body(),
        )
        blank = client.post(
            "/contact",
            headers={"token": "token-a", "Idempotency-Key": "   "},
            json=body(),
        )

    assert denied.json() == {
        "status": "error",
        "errors": [{"code": 401, "message": "Authentication failed"}],
    }
    assert duplicate.json() == {
        "status": "error",
        "errors": [{"code": 422, "message": "Request is not valid"}],
    }
    assert blank.json() == {
        "status": "error",
        "errors": [{"code": 422, "message": "Request is not valid"}],
    }
    assert uuid.UUID(denied.headers["X-Request-ID"])
    assert uuid.UUID(duplicate.headers["X-Request-ID"])
    assert uuid.UUID(blank.headers["X-Request-ID"])
    assert writer.calls == []


def test_two_clients_use_only_their_server_assigned_projects() -> None:
    client, writer = make_client()

    with client:
        response_a = client.post("/contact", headers={"token": "token-a"}, json=body())
        response_b = client.post("/contact", headers={"token": "token-b"}, json=body())

    assert response_a.json()["result"]["project_id"] == 10001
    assert response_b.json()["result"]["project_id"] == 10002
    assert [call["access"].project_id for call in writer.calls] == [10001, 10002]  # type: ignore[union-attr]
