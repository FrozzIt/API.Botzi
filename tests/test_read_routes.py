from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from proxy_api.auth.routes import get_auth_service
from proxy_api.auth.service import AuthenticatedSession, AuthenticationFailed
from proxy_api.infrastructure import get_provider_adapter
from proxy_api.main import create_app
from proxy_api.projects.routes import get_configured_provider_account
from proxy_api.provider import ProviderObjectNotFound, ProviderUnavailable


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


def contact_payload(project_id: int, contact_id: int) -> dict[str, object]:
    return {
        "id": contact_id,
        "project_id": project_id,
        "details": [{"id": contact_id + 100, "type": "email", "data": "client@example.test"}],
        "fields": [{"id": "12", "type": "string", "name": "Region", "value": "Test"}],
        "created_at": "01.01.26 10:00:00",
        "name": f"Contact {contact_id}",
        "site": "example.test",
    }


def lead_payload(project_id: int, lead_id: int, contact_id: int) -> dict[str, object]:
    return {
        "id": lead_id,
        "contact_id": contact_id,
        "name": f"Lead {lead_id}",
        "deal": False,
        "created_at": "01.01.26 10:00",
        "contact": contact_payload(project_id, contact_id),
        "view": {
            "id": lead_id + 100,
            "project_id": project_id,
            "uuid": f"view-{lead_id}",
            "source": "test",
        },
        "custom": [{"id": 900, "type": "owner", "value": {"id": 1}}],
    }


class FakeProvider:
    def __init__(self) -> None:
        contact_a = contact_payload(10001, 20001)
        contact_b = contact_payload(10002, 20002)
        lead_a = lead_payload(10001, 30001, 20001)
        lead_b = lead_payload(10002, 30002, 20002)
        self.responses: dict[str, object] = {
            "/contact/20001": {"status": "success", "result": contact_a},
            "/contact/20002": {"status": "success", "result": contact_b},
            "/lead/30001": {"status": "success", "result": lead_a},
            "/lead/30002": {"status": "success", "result": lead_b},
            "/lead/10001/list": {"status": "success", "result": [lead_a]},
            "/lead/10002/list": [lead_b],
        }
        self.calls: list[tuple[str, Mapping[str, str | int] | None]] = []
        self.error: Exception | None = None

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
    ) -> object:
        self.calls.append((path, params))
        if self.error is not None:
            raise self.error
        try:
            return self.responses[path]
        except KeyError:
            raise ProviderObjectNotFound("private provider detail") from None


def make_client() -> tuple[TestClient, FakeProvider]:
    application = create_app()
    application.router.lifespan_context = no_op_lifespan
    auth = FakeAuthService()
    provider = FakeProvider()
    application.dependency_overrides[get_auth_service] = lambda: auth
    application.dependency_overrides[get_provider_adapter] = lambda: provider
    application.dependency_overrides[get_configured_provider_account] = lambda: "primary"
    return TestClient(application), provider


def not_found() -> dict[str, object]:
    return {
        "status": "error",
        "errors": [{"code": 404, "message": "Object not found"}],
    }


@pytest.mark.parametrize(
    ("token", "path", "expected_id", "expected_project"),
    [
        ("token-a", "/contact/20001", 20001, 10001),
        ("token-b", "/contact/20002", 20002, 10002),
        ("token-a", "/lead/30001", 30001, 10001),
        ("token-b", "/lead/30002", 30002, 10002),
    ],
)
def test_two_clients_can_read_only_owned_objects(
    token: str,
    path: str,
    expected_id: int,
    expected_project: int,
) -> None:
    client, provider = make_client()

    with client:
        response = client.get(path, headers={"token": token})

    assert response.json()["status"] == "success"
    result = response.json()["result"]
    assert result["id"] == expected_id
    if path.startswith("/contact/"):
        assert result["project_id"] == expected_project
    else:
        assert result["contact"]["project_id"] == expected_project
    assert provider.calls == [(path, None)]


def test_two_clients_receive_only_owned_lead_lists() -> None:
    client, provider = make_client()

    with client:
        response_a = client.get("/lead/10001/list", headers={"token": "token-a"})
        response_b = client.get("/lead/10002/list", headers={"token": "token-b"})

    assert [item["id"] for item in response_a.json()["result"]] == [30001]
    assert [item["contact"]["project_id"] for item in response_a.json()["result"]] == [10001]
    assert [item["id"] for item in response_b.json()["result"]] == [30002]
    assert [item["contact"]["project_id"] for item in response_b.json()["result"]] == [10002]
    assert provider.calls == [
        ("/lead/10001/list", {"offset": 0, "limit": 10}),
        ("/lead/10002/list", {"offset": 0, "limit": 10}),
    ]


@pytest.mark.parametrize(
    "path",
    [
        "/contact/20001?project_id=10002",
        "/lead/30001?project_id=10002",
        "/lead/10002/list",
        "/lead/10001/list?project_id=10002",
        "/lead/10001/list?project_id=10001&project_id=10001",
    ],
)
def test_conflicting_or_ambiguous_project_is_rejected_before_provider_call(path: str) -> None:
    client, provider = make_client()

    with client:
        response = client.get(path, headers={"token": "token-a"})

    assert response.json() == not_found()
    assert provider.calls == []


@pytest.mark.parametrize(
    "path",
    ["/contact/29999", "/lead/39999"],
)
def test_unknown_object_is_neutral(path: str) -> None:
    client, provider = make_client()

    with client:
        response = client.get(path, headers={"token": "token-a"})

    assert response.json() == not_found()
    assert "private provider detail" not in response.text
    assert provider.calls == [(path, None)]


@pytest.mark.parametrize(
    ("token", "path"),
    [
        ("token-a", "/contact/20002"),
        ("token-b", "/contact/20001"),
        ("token-a", "/lead/30002"),
        ("token-b", "/lead/30001"),
    ],
)
def test_foreign_object_is_indistinguishable_from_unknown(token: str, path: str) -> None:
    client, provider = make_client()

    with client:
        response = client.get(path, headers={"token": token})

    assert response.json() == not_found()
    assert provider.calls == [(path, None)]


@pytest.mark.parametrize(
    ("token", "path"),
    [
        ("token-a", "/lead/10002/list"),
        ("token-b", "/lead/10001/list"),
    ],
)
def test_foreign_list_project_is_rejected_before_provider_call(
    token: str,
    path: str,
) -> None:
    client, provider = make_client()

    with client:
        response = client.get(path, headers={"token": token})

    assert response.json() == not_found()
    assert provider.calls == []


@pytest.mark.parametrize("path", ["/lead/30001", "/lead/10001/list"])
def test_mismatched_nested_contact_id_closes_whole_response(path: str) -> None:
    client, provider = make_client()
    response_key = "/lead/10001/list" if path.endswith("/list") else path
    response = provider.responses[response_key]
    target = response["result"] if isinstance(response, dict) else response
    item = target[0] if isinstance(target, list) else target
    assert isinstance(item, dict)
    contact = item["contact"]
    assert isinstance(contact, dict)
    contact["id"] = 20099

    with client:
        result = client.get(path, headers={"token": "token-a"})

    assert result.json() == not_found()
    assert "20099" not in result.text


@pytest.mark.parametrize(
    ("path", "response_key"),
    [
        ("/contact/20001", "/contact/20001"),
        ("/lead/30001", "/lead/30001"),
        ("/lead/10001/list", "/lead/10001/list"),
    ],
)
def test_foreign_nested_project_link_closes_whole_response(
    path: str,
    response_key: str,
) -> None:
    client, provider = make_client()
    response = provider.responses[response_key]
    target = response["result"] if isinstance(response, dict) else response
    item = target[0] if isinstance(target, list) else target
    assert isinstance(item, dict)
    item["unexpected_relation"] = {"project_id": 10002}

    with client:
        result = client.get(path, headers={"token": "token-a"})

    assert result.json() == not_found()
    assert "unexpected_relation" not in result.text


@pytest.mark.parametrize(
    ("path", "response_key"),
    [
        ("/contact/20001", "/contact/20001"),
        ("/lead/30001", "/lead/30001"),
        ("/lead/10001/list", "/lead/10001/list"),
    ],
)
def test_unexpected_fields_and_nested_service_data_are_not_returned(
    path: str,
    response_key: str,
) -> None:
    client, provider = make_client()
    response = provider.responses[response_key]
    target = response["result"] if isinstance(response, dict) else response
    item = target[0] if isinstance(target, list) else target
    assert isinstance(item, dict)
    item["internal_secret"] = "must-not-leak"
    if isinstance(item.get("contact"), dict):
        item["contact"]["provider_url"] = "https://provider.invalid/private"
    elif isinstance(item.get("details"), list):
        item["details"][0]["provider_url"] = "https://provider.invalid/private"

    with client:
        result = client.get(path, headers={"token": "token-a"})

    assert result.json()["status"] == "success"
    assert "internal_secret" not in result.text
    assert "must-not-leak" not in result.text
    assert "provider_url" not in result.text
    assert "provider.invalid" not in result.text
    assert "custom" not in result.text


@pytest.mark.parametrize(
    "path",
    ["/contact/20001", "/lead/30001", "/lead/10001/list"],
)
def test_provider_failure_is_neutral_and_does_not_leak(path: str) -> None:
    client, provider = make_client()
    provider.error = ProviderUnavailable("secret upstream response and token")

    with client:
        response = client.get(path, headers={"token": "token-a"})

    assert response.json() == {
        "status": "error",
        "errors": [{"code": 503, "message": "Service unavailable"}],
    }
    assert "secret upstream" not in response.text
    assert "token" not in response.text


@pytest.mark.parametrize(
    "path",
    ["/contact/20001", "/lead/30001", "/lead/10001/list"],
)
def test_invalid_client_token_does_not_call_or_initialize_provider(path: str) -> None:
    application = create_app()
    application.router.lifespan_context = no_op_lifespan
    application.dependency_overrides[get_auth_service] = lambda: FakeAuthService()
    provider_initializations = 0

    def provider_dependency() -> FakeProvider:
        nonlocal provider_initializations
        provider_initializations += 1
        return FakeProvider()

    application.dependency_overrides[get_provider_adapter] = provider_dependency
    application.dependency_overrides[get_configured_provider_account] = lambda: "primary"

    with TestClient(application) as client:
        response = client.get(path, headers={"token": "invalid"})

    assert response.json() == {
        "status": "error",
        "errors": [{"code": 401, "message": "Authentication failed"}],
    }
    assert provider_initializations == 0


@pytest.mark.parametrize(
    "path",
    ["/contact/search?project_id=10001", "/view/1", "/task/1", "/task/10001/list"],
)
def test_unproven_routes_remain_closed_without_provider_call(path: str) -> None:
    client, provider = make_client()

    with client:
        response = client.get(path, headers={"token": "token-a"})

    assert response.json().get("status") != "success"
    assert provider.calls == []
