from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from proxy_api.auth.routes import get_auth_service
from proxy_api.auth.service import AuthenticatedSession, AuthenticationFailed
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

    async def logout(self, token: str) -> None:
        if self.sessions.pop(token, None) is None:
            raise AuthenticationFailed("Authentication failed")


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.responses: dict[int, dict[str, object]] = {
            10001: {"id": 10001, "name": "Project A"},
            10002: {"id": 10002, "name": "Project B"},
        }
        self.error: Exception | None = None

    async def get(self, path: str) -> dict[str, object]:
        self.calls.append(path)
        if self.error is not None:
            raise self.error
        project_id = int(path.rsplit("/", 1)[1])
        return {"status": "success", "result": self.responses[project_id]}


def make_client() -> tuple[TestClient, FakeAuthService, FakeProvider]:
    application = create_app()
    application.router.lifespan_context = no_op_lifespan
    auth = FakeAuthService()
    provider = FakeProvider()
    application.dependency_overrides[get_auth_service] = lambda: auth
    application.dependency_overrides[get_provider_adapter] = lambda: provider
    application.dependency_overrides[get_configured_provider_account] = lambda: "primary"
    return TestClient(application), auth, provider


def test_two_clients_each_receive_only_their_configured_project() -> None:
    client, _, provider = make_client()

    with client:
        list_a = client.get("/projects", headers={"token": "token-a"})
        item_a = client.get("/project/10001", headers={"token": "token-a"})
        list_b = client.get("/projects", headers={"token": "token-b"})
        item_b = client.get("/project/10002", headers={"token": "token-b"})

    assert list_a.json() == {
        "status": "success",
        "result": [{"id": 10001, "name": "Project A"}],
    }
    assert item_a.json() == {
        "status": "success",
        "result": {"id": 10001, "name": "Project A"},
    }
    assert list_b.json() == {
        "status": "success",
        "result": [{"id": 10002, "name": "Project B"}],
    }
    assert item_b.json() == {
        "status": "success",
        "result": {"id": 10002, "name": "Project B"},
    }
    assert provider.calls == [
        "/project/10001",
        "/project/10001",
        "/project/10002",
        "/project/10002",
    ]
    assert "/projects" not in provider.calls


def test_foreign_project_is_rejected_before_provider_call() -> None:
    client, _, provider = make_client()

    with client:
        response = client.get("/project/10002", headers={"token": "token-a"})

    assert response.json() == {
        "status": "error",
        "errors": [{"code": 404, "message": "Project not found"}],
    }
    assert provider.calls == []


def test_unconfigured_provider_account_is_rejected_before_provider_call() -> None:
    client, auth, provider = make_client()
    auth.sessions["token-a"] = AuthenticatedSession(
        "session-a",
        "client-a",
        "secondary",
        10001,
    )

    with client:
        response = client.get("/projects", headers={"token": "token-a"})

    assert response.json() == {
        "status": "error",
        "errors": [{"code": 503, "message": "Service unavailable"}],
    }
    assert provider.calls == []


def test_invalid_token_is_rejected_before_provider_call() -> None:
    client, _, provider = make_client()

    with client:
        response = client.get("/projects", headers={"token": "invalid-token"})

    assert response.json() == {
        "status": "error",
        "errors": [{"code": 401, "message": "Authentication failed"}],
    }
    assert provider.calls == []


@pytest.mark.parametrize("headers", [{}, {"token": "invalid-token"}])
def test_auth_rejection_stops_before_provider_initialization(
    headers: dict[str, str],
) -> None:
    application = create_app()
    application.router.lifespan_context = no_op_lifespan
    auth = FakeAuthService()
    provider_initializations = 0

    def unavailable_provider() -> FakeProvider:
        nonlocal provider_initializations
        provider_initializations += 1
        raise RuntimeError("synthetic provider initialization failure")

    application.dependency_overrides[get_auth_service] = lambda: auth
    application.dependency_overrides[get_provider_adapter] = unavailable_provider
    application.dependency_overrides[get_configured_provider_account] = lambda: "primary"

    with TestClient(application) as client:
        response = client.get("/projects", headers=headers)

    assert response.json() == {
        "status": "error",
        "errors": [{"code": 401, "message": "Authentication failed"}],
    }
    assert provider_initializations == 0


def test_logout_of_one_client_does_not_affect_the_other() -> None:
    client, _, provider = make_client()

    with client:
        assert client.post("/logout", headers={"token": "token-a"}).json() == {
            "status": "success",
            "result": {},
        }
        revoked = client.get("/projects", headers={"token": "token-a"})
        unaffected = client.get("/projects", headers={"token": "token-b"})

    assert revoked.json() == {
        "status": "error",
        "errors": [{"code": 401, "message": "Authentication failed"}],
    }
    assert unaffected.json() == {
        "status": "success",
        "result": [{"id": 10002, "name": "Project B"}],
    }
    assert provider.calls == ["/project/10002"]


def test_provider_error_is_neutral() -> None:
    from proxy_api.provider import ProviderUnavailable

    client, _, provider = make_client()
    provider.error = ProviderUnavailable(
        "raw provider token credential and other-project marker must stay private"
    )

    with client:
        response = client.get("/projects", headers={"token": "token-a"})

    assert response.json() == {
        "status": "error",
        "errors": [{"code": 503, "message": "Service unavailable"}],
    }
    assert "raw provider" not in response.text
    assert "other-project" not in response.text


@pytest.mark.parametrize(
    "unexpected_result",
    [
        {"id": 10002, "name": "Foreign project"},
        {"id": "10001", "name": "String ID"},
        {"name": "Missing ID"},
    ],
)
def test_unexpected_provider_project_is_not_returned(
    unexpected_result: dict[str, object],
) -> None:
    client, _, provider = make_client()
    provider.responses[10001] = unexpected_result

    with client:
        response = client.get("/projects", headers={"token": "token-a"})

    assert response.json() == {
        "status": "error",
        "errors": [{"code": 503, "message": "Service unavailable"}],
    }
    assert str(unexpected_result) not in response.text
