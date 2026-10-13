from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from proxy_api.auth.routes import get_auth_service
from proxy_api.auth.service import AuthenticatedSession, AuthenticationFailed
from proxy_api.infrastructure import get_provider_adapter
from proxy_api.main import create_app
from proxy_api.projects.routes import get_configured_provider_account
from proxy_api.provider import ProviderObjectNotFound, ProviderUnavailable
from proxy_api.reads.detail_ownership import (
    DetailOwnership,
    DetailOwnershipConflict,
    DetailOwnershipUnavailable,
    VerifiedContact,
    normalize_identifier,
)
from proxy_api.reads.routes import (
    ReadAccessDenied,
    get_detail_ownership_store,
    verify_contact_detail_parent,
)


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


def view_payload(project_id: int, view_id: int) -> dict[str, object]:
    return {
        "id": view_id,
        "project_id": project_id,
        "uuid": f"view-{view_id}",
        "source": "test",
        "page": "https://public.example.test/page",
        "visitor": {"internal_id": "must-not-leak"},
    }


def task_payload(project_id: int, task_id: int, staff_id: int) -> dict[str, object]:
    return {
        "id": task_id,
        "project_id": project_id,
        "title": f"Task {task_id}",
        "text": "Safe task text",
        "owner_id": staff_id,
        "date_start": 1_700_000_000,
        "date_end": 1_700_003_600,
        "remind_type": 1,
        "remind_method": "popup",
        "lead_id": task_id + 1000,
        "visibility": 2,
        "status": 0,
        "observers": [
            {
                "id": staff_id,
                "name": "Private Staff Name",
                "email": "staff@example.test",
                "phone": "70000000000",
                "job": "Manager",
                "avatar": "https://provider.invalid/private-avatar",
                "last_project_id": project_id,
            }
        ],
        "labels": [
            {
                "id": task_id + 2000,
                "name": "Safe label",
                "color": "F7F0C5",
                "internal_url": "https://provider.invalid/private-label",
            }
        ],
        "internal_url": "https://provider.invalid/private-task",
    }


class FakeProvider:
    def __init__(self) -> None:
        contact_a = contact_payload(10001, 20001)
        contact_b = contact_payload(10002, 20002)
        lead_a = lead_payload(10001, 30001, 20001)
        lead_b = lead_payload(10002, 30002, 20002)
        view_a = view_payload(10001, 40001)
        view_b = view_payload(10002, 40002)
        task_a = task_payload(10001, 50001, 60001)
        task_b = task_payload(10002, 50002, 60002)
        self.responses: dict[str, object] = {
            "/contact/search:10001": {"status": "success", "result": [contact_a]},
            "/contact/search:10002": {"status": "success", "result": [contact_b]},
            "/contact/20001": {"status": "success", "result": contact_a},
            "/contact/20002": {"status": "success", "result": contact_b},
            "/view/40001": {"status": "success", "result": view_a},
            "/view/40002": {"status": "success", "result": view_b},
            "/view/view-40001": {"status": "success", "result": view_a},
            "/lead/30001": {"status": "success", "result": lead_a},
            "/lead/30002": {"status": "success", "result": lead_b},
            "/lead/10001/list": {"status": "success", "result": [lead_a]},
            "/lead/10002/list": [lead_b],
            "/task/50001": {"status": "success", "result": task_a},
            "/task/50002": {"status": "success", "result": task_b},
            "/task/10001/list": {"status": "success", "result": [task_a]},
            "/task/10002/list": [task_b],
        }
        self.calls: list[
            tuple[
                str,
                Mapping[str, str | int] | Sequence[tuple[str, str | int]] | None,
            ]
        ] = []
        self.error: Exception | None = None

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | Sequence[tuple[str, str | int]] | None = None,
    ) -> object:
        self.calls.append((path, params))
        if self.error is not None:
            raise self.error
        response_key = path
        if path == "/contact/search":
            assert params is not None
            project_id = (
                next(value for key, value in params.items() if key == "project_id")
                if isinstance(params, Mapping)
                else next(value for key, value in params if key == "project_id")
            )
            response_key = f"{path}:{project_id}"
        try:
            return self.responses[response_key]
        except KeyError:
            raise ProviderObjectNotFound("private provider detail") from None


class FakeDetailOwnershipStore:
    def __init__(self) -> None:
        self.sync_calls: list[tuple[str, list[VerifiedContact]]] = []
        self.mappings: dict[tuple[str, str], DetailOwnership] = {}
        self.error: Exception | None = None

    async def sync_verified_contacts(
        self,
        provider_account: str,
        contacts: list[VerifiedContact],
    ) -> None:
        if self.error is not None:
            raise self.error
        pending: dict[tuple[str, str], DetailOwnership] = {}
        for contact in contacts:
            contact_id = normalize_identifier(contact.contact_id)
            for raw_detail_id in contact.detail_ids:
                detail_id = normalize_identifier(raw_detail_id)
                key = (provider_account, detail_id)
                ownership = DetailOwnership(
                    provider_account=provider_account,
                    detail_id=detail_id,
                    contact_id=contact_id,
                    project_id=contact.project_id,
                    active=True,
                    verified_at=datetime.now(UTC),
                )
                existing = self.mappings.get(key) or pending.get(key)
                if existing is not None and (
                    existing.contact_id != ownership.contact_id
                    or existing.project_id != ownership.project_id
                ):
                    raise DetailOwnershipConflict("synthetic conflict")
                pending[key] = ownership
        self.sync_calls.append((provider_account, contacts))
        self.mappings.update(pending)

    async def get(self, provider_account: str, detail_id: int | str) -> DetailOwnership | None:
        if self.error is not None:
            raise self.error
        return self.mappings.get((provider_account, normalize_identifier(detail_id)))

    async def invalidate_if_matches(self, ownership: DetailOwnership) -> None:
        if self.error is not None:
            raise self.error
        key = (ownership.provider_account, ownership.detail_id)
        if self.mappings.get(key) == ownership:
            self.mappings[key] = replace(ownership, active=False)

    async def is_current(self, ownership: DetailOwnership) -> bool:
        if self.error is not None:
            raise self.error
        return self.mappings.get((ownership.provider_account, ownership.detail_id)) == ownership

    def add(
        self,
        *,
        provider_account: str = "primary",
        detail_id: int | str = 20101,
        contact_id: int | str = 20001,
        project_id: int = 10001,
        active: bool = True,
    ) -> DetailOwnership:
        ownership = DetailOwnership(
            provider_account=provider_account,
            detail_id=normalize_identifier(detail_id),
            contact_id=normalize_identifier(contact_id),
            project_id=project_id,
            active=active,
            verified_at=datetime.now(UTC),
        )
        self.mappings[(provider_account, ownership.detail_id)] = ownership
        return ownership


def make_client() -> tuple[TestClient, FakeProvider]:
    application = create_app()
    application.router.lifespan_context = no_op_lifespan
    auth = FakeAuthService()
    provider = FakeProvider()
    provider.ownership_store = FakeDetailOwnershipStore()
    application.dependency_overrides[get_auth_service] = lambda: auth
    application.dependency_overrides[get_provider_adapter] = lambda: provider
    application.dependency_overrides[get_configured_provider_account] = lambda: "primary"
    application.dependency_overrides[get_detail_ownership_store] = lambda: provider.ownership_store
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
        "/contact/20001",
        "/contact/search?project_id=10001&phone=70000000001",
        "/lead/30001",
        "/lead/10001/list",
    ],
)
def test_verified_contact_reads_register_detail_ownership(path: str) -> None:
    client, provider = make_client()

    with client:
        response = client.get(path, headers={"token": "token-a"})

    assert response.json()["status"] == "success"
    assert len(provider.ownership_store.sync_calls) == 1
    provider_account, contacts = provider.ownership_store.sync_calls[0]
    assert provider_account == "primary"
    assert contacts == [
        VerifiedContact(
            contact_id=20001,
            project_id=10001,
            detail_ids=frozenset({20101}),
            details_complete=path == "/contact/20001",
        )
    ]


def test_contact_without_details_is_not_treated_as_complete_for_index() -> None:
    client, provider = make_client()
    response = provider.responses["/contact/20001"]
    assert isinstance(response, dict)
    contact = response["result"]
    assert isinstance(contact, dict)
    contact.pop("details")

    with client:
        result = client.get("/contact/20001", headers={"token": "token-a"})

    assert result.json()["status"] == "success"
    assert provider.ownership_store.sync_calls == [
        (
            "primary",
            [
                VerifiedContact(
                    contact_id=20001,
                    project_id=10001,
                    detail_ids=frozenset(),
                    details_complete=False,
                )
            ],
        )
    ]


def test_rejected_mixed_lead_list_does_not_partially_register_details() -> None:
    client, provider = make_client()
    response = provider.responses["/lead/10001/list"]
    assert isinstance(response, dict)
    result = response["result"]
    assert isinstance(result, list)
    result.append(lead_payload(10002, 30002, 20002))

    with client:
        response = client.get("/lead/10001/list", headers={"token": "token-a"})

    assert response.json() == not_found()
    assert provider.ownership_store.sync_calls == []
    assert provider.ownership_store.mappings == {}


@pytest.mark.parametrize("path", ["/contact/20001", "/lead/30001"])
def test_ownership_store_failure_closes_verified_contact_reads(path: str) -> None:
    client, provider = make_client()
    provider.ownership_store.error = DetailOwnershipUnavailable("private database detail")

    with client:
        response = client.get(path, headers={"token": "token-a"})

    assert response.json() == {
        "status": "error",
        "errors": [{"code": 503, "message": "Service unavailable"}],
    }
    assert "database" not in response.text


def test_conflicting_detail_mapping_closes_contact_read_without_rebinding() -> None:
    client, provider = make_client()
    existing = provider.ownership_store.add(
        detail_id=20101,
        contact_id=20002,
        project_id=10002,
    )

    with client:
        response = client.get("/contact/20001", headers={"token": "token-a"})

    assert response.json() == not_found()
    assert provider.ownership_store.mappings[("primary", "20101")] == existing


@pytest.mark.parametrize(
    ("token", "path", "expected_id", "expected_project", "expected_call"),
    [
        (
            "token-a",
            "/contact/search?project_id=10001&phone=70000000001",
            20001,
            10001,
            (
                "/contact/search",
                [("project_id", 10001), ("phone", "70000000001")],
            ),
        ),
        (
            "token-b",
            "/contact/search?project_id=10002&email=client-b%40example.test",
            20002,
            10002,
            (
                "/contact/search",
                [("project_id", 10002), ("email", "client-b@example.test")],
            ),
        ),
        ("token-a", "/view/40001", 40001, 10001, ("/view/40001", None)),
        ("token-b", "/view/40002", 40002, 10002, ("/view/40002", None)),
        ("token-a", "/task/50001", 50001, 10001, ("/task/50001", None)),
        ("token-b", "/task/50002", 50002, 10002, ("/task/50002", None)),
    ],
)
def test_two_clients_can_read_only_owned_search_view_and_task_objects(
    token: str,
    path: str,
    expected_id: int,
    expected_project: int,
    expected_call: tuple[
        str,
        Mapping[str, str | int] | Sequence[tuple[str, str | int]] | None,
    ],
) -> None:
    client, provider = make_client()

    with client:
        response = client.get(path, headers={"token": token})

    assert response.json()["status"] == "success"
    payload = response.json()["result"]
    result = payload[0] if isinstance(payload, list) else payload
    assert result["id"] == expected_id
    assert result["project_id"] == expected_project
    assert provider.calls == [expected_call]


def test_two_clients_receive_only_owned_task_lists_in_both_provider_shapes() -> None:
    client, provider = make_client()

    with client:
        response_a = client.get("/task/10001/list", headers={"token": "token-a"})
        response_b = client.get("/task/10002/list", headers={"token": "token-b"})

    assert [(item["id"], item["project_id"]) for item in response_a.json()["result"]] == [
        (50001, 10001)
    ]
    assert [(item["id"], item["project_id"]) for item in response_b.json()["result"]] == [
        (50002, 10002)
    ]
    assert provider.calls == [
        ("/task/10001/list", {"offset": 0, "limit": 10}),
        ("/task/10002/list", {"offset": 0, "limit": 10}),
    ]


def test_view_can_be_read_by_matching_uuid() -> None:
    client, provider = make_client()

    with client:
        response = client.get("/view/view-40001", headers={"token": "token-a"})

    assert response.json()["result"]["id"] == 40001
    assert provider.calls == [("/view/view-40001", None)]


@pytest.mark.parametrize(
    "path",
    [
        "/contact/search?project_id=10002&phone=70000000001",
        "/contact/20001?project_id=10002",
        "/view/40001?project_id=10002",
        "/lead/30001?project_id=10002",
        "/lead/10002/list",
        "/lead/10001/list?project_id=10002",
        "/lead/10001/list?project_id=10001&project_id=10001",
        "/task/50001?project_id=10002",
        "/task/10002/list",
        "/task/10001/list?project_id=10002",
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
    ["/contact/29999", "/view/49999", "/lead/39999", "/task/59999"],
)
def test_unknown_object_is_neutral(path: str) -> None:
    client, provider = make_client()

    with client:
        response = client.get(path, headers={"token": "token-a"})

    assert response.json() == not_found()
    assert "private provider detail" not in response.text
    assert provider.calls == [(path, None)]


def test_contact_search_without_matches_returns_an_empty_list() -> None:
    client, provider = make_client()
    provider.responses["/contact/search:10001"] = {"status": "success", "result": []}

    with client:
        response = client.get(
            "/contact/search?project_id=10001&phone=79999999999",
            headers={"token": "token-a"},
        )

    assert response.json() == {"status": "success", "result": []}


@pytest.mark.parametrize(
    ("token", "path"),
    [
        ("token-a", "/contact/20002"),
        ("token-b", "/contact/20001"),
        ("token-a", "/view/40002"),
        ("token-b", "/view/40001"),
        ("token-a", "/lead/30002"),
        ("token-b", "/lead/30001"),
        ("token-a", "/task/50002"),
        ("token-b", "/task/50001"),
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
        ("token-a", "/task/10002/list"),
        ("token-b", "/task/10001/list"),
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


@pytest.mark.parametrize(
    ("token", "project_id", "own_task", "foreign_project", "foreign_task", "foreign_staff"),
    [
        ("token-a", 10001, 50001, 10002, 50002, 60002),
        ("token-b", 10002, 50002, 10001, 50001, 60001),
    ],
)
def test_one_foreign_task_closes_the_whole_task_list(
    token: str,
    project_id: int,
    own_task: int,
    foreign_project: int,
    foreign_task: int,
    foreign_staff: int,
) -> None:
    client, provider = make_client()
    stored_response = provider.responses[f"/task/{project_id}/list"]
    result = stored_response["result"] if isinstance(stored_response, dict) else stored_response
    assert isinstance(result, list)
    result.append(task_payload(foreign_project, foreign_task, foreign_staff))

    with client:
        response = client.get(f"/task/{project_id}/list", headers={"token": token})

    assert response.json() == not_found()
    assert str(own_task) not in response.text
    assert str(foreign_task) not in response.text


@pytest.mark.parametrize(
    ("token", "project_id", "own_contact", "foreign_project", "foreign_contact"),
    [
        ("token-a", 10001, 20001, 10002, 20002),
        ("token-b", 10002, 20002, 10001, 20001),
    ],
)
def test_one_foreign_contact_closes_the_whole_search_result(
    token: str,
    project_id: int,
    own_contact: int,
    foreign_project: int,
    foreign_contact: int,
) -> None:
    client, provider = make_client()
    response = provider.responses[f"/contact/search:{project_id}"]
    assert isinstance(response, dict)
    result = response["result"]
    assert isinstance(result, list)
    result.append(contact_payload(foreign_project, foreign_contact))

    with client:
        response = client.get(
            f"/contact/search?project_id={project_id}&phone=70000000001",
            headers={"token": token},
        )

    assert response.json() == not_found()
    assert str(own_contact) not in response.text
    assert str(foreign_contact) not in response.text


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
        (
            "/contact/search?project_id=10001&phone=70000000001",
            "/contact/search:10001",
        ),
        ("/contact/20001", "/contact/20001"),
        ("/view/40001", "/view/40001"),
        ("/lead/30001", "/lead/30001"),
        ("/lead/10001/list", "/lead/10001/list"),
        ("/task/50001", "/task/50001"),
        ("/task/10001/list", "/task/10001/list"),
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
        (
            "/contact/search?project_id=10001&phone=70000000001",
            "/contact/search:10001",
        ),
        ("/contact/20001", "/contact/20001"),
        ("/view/40001", "/view/40001"),
        ("/lead/30001", "/lead/30001"),
        ("/lead/10001/list", "/lead/10001/list"),
        ("/task/50001", "/task/50001"),
        ("/task/10001/list", "/task/10001/list"),
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
    item["provider_url"] = "https://provider.invalid/private"
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
    ("path", "response_key"),
    [
        ("/task/50001", "/task/50001"),
        ("/task/10001/list", "/task/10001/list"),
    ],
)
def test_unproven_task_links_staff_data_and_internal_urls_are_redacted(
    path: str,
    response_key: str,
) -> None:
    client, provider = make_client()
    stored_response = provider.responses[response_key]
    target = stored_response["result"] if isinstance(stored_response, dict) else stored_response
    task = target[0] if isinstance(target, list) else target
    assert isinstance(task, dict)
    task["lead_id"] = 39002
    task["owner_id"] = 69002
    task["observers"] = [
        {
            "id": 69003,
            "name": "Foreign project staff",
            "avatar": "https://provider.invalid/foreign-avatar",
        }
    ]
    task["labels"] = [
        {
            "id": 79002,
            "name": "Foreign project label",
            "color": "FFFFFF",
        }
    ]

    with client:
        response = client.get(path, headers={"token": "token-a"})

    payload = response.json()["result"]
    result = payload[0] if isinstance(payload, list) else payload
    assert "lead_id" not in result
    assert "owner_id" not in result
    assert "observers" not in result
    assert "labels" not in result
    for forbidden in (
        "39002",
        "69002",
        "69003",
        "79002",
        "Foreign project staff",
        "Foreign project label",
        "avatar",
        "internal_url",
        "provider.invalid",
    ):
        assert forbidden not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/contact/search?project_id=10001&phone=70000000001",
        "/contact/20001",
        "/view/40001",
        "/lead/30001",
        "/lead/10001/list",
        "/task/50001",
        "/task/10001/list",
    ],
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
    ("path", "response_key", "invalid_response"),
    [
        (
            "/contact/search?project_id=10001&phone=70000000001",
            "/contact/search:10001",
            {"status": "success", "result": {}},
        ),
        ("/view/40001", "/view/40001", {"status": "success", "result": []}),
        ("/task/50001", "/task/50001", {"status": "success", "result": "bad"}),
        ("/task/10001/list", "/task/10001/list", {"status": "success", "result": {}}),
    ],
)
def test_malformed_provider_response_is_neutral(
    path: str,
    response_key: str,
    invalid_response: object,
) -> None:
    client, provider = make_client()
    provider.responses[response_key] = invalid_response

    with client:
        response = client.get(path, headers={"token": "token-a"})

    assert response.json() == {
        "status": "error",
        "errors": [{"code": 503, "message": "Service unavailable"}],
    }
    assert "bad" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/contact/search?project_id=10001&phone=70000000001",
        "/contact/20001",
        "/view/40001",
        "/lead/30001",
        "/lead/10001/list",
        "/task/50001",
        "/task/10001/list",
    ],
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
    application.dependency_overrides[get_detail_ownership_store] = FakeDetailOwnershipStore

    with TestClient(application) as client:
        response = client.get(path, headers={"token": "invalid"})

    assert response.json() == {
        "status": "error",
        "errors": [{"code": 401, "message": "Authentication failed"}],
    }
    assert provider_initializations == 0


@pytest.mark.parametrize(
    "path",
    [
        "/contact/search?project_id=10001",
        "/contact/search?project_id=10001&phone=",
        "/contact/search?project_id=10001&max_user_id=one&max_user_id=two",
    ],
)
def test_contact_search_requires_one_unambiguous_non_empty_filter(path: str) -> None:
    client, provider = make_client()

    with client:
        response = client.get(path, headers={"token": "token-a"})

    assert response.json() == {
        "status": "error",
        "errors": [{"code": 422, "message": "Request is not valid"}],
    }
    assert provider.calls == []


def test_contact_search_forwards_repeated_phone_filters_without_unknown_parameters() -> None:
    client, provider = make_client()

    with client:
        response = client.get(
            "/contact/search?project_id=10001&phone=70000000001&phone=70000000002&ignored=x",
            headers={"token": "token-a"},
        )

    assert response.json()["status"] == "success"
    assert provider.calls == [
        (
            "/contact/search",
            [
                ("project_id", 10001),
                ("phone", "70000000001"),
                ("phone", "70000000002"),
            ],
        )
    ]


@pytest.mark.parametrize(
    ("path", "response_key", "changed_field", "changed_value"),
    [
        ("/view/40001", "/view/40001", "id", 40099),
        ("/view/view-40001", "/view/view-40001", "uuid", "view-40099"),
        ("/task/50001", "/task/50001", "id", 50099),
    ],
)
def test_object_identifier_mismatch_is_neutral(
    path: str,
    response_key: str,
    changed_field: str,
    changed_value: object,
) -> None:
    client, provider = make_client()
    response = provider.responses[response_key]
    assert isinstance(response, dict)
    result = response["result"]
    assert isinstance(result, dict)
    result[changed_field] = changed_value

    with client:
        response = client.get(path, headers={"token": "token-a"})

    assert response.json() == not_found()


@pytest.mark.asyncio
async def test_detail_guard_rechecks_parent_on_every_use() -> None:
    provider = FakeProvider()
    store = FakeDetailOwnershipStore()
    expected = store.add()
    access = AuthenticatedSession("session-a", "client-a", "primary", 10001)

    first = await verify_contact_detail_parent(
        20101,
        access,
        provider,  # type: ignore[arg-type]
        "primary",
        store,  # type: ignore[arg-type]
    )
    second = await verify_contact_detail_parent(
        20101,
        access,
        provider,  # type: ignore[arg-type]
        "primary",
        store,  # type: ignore[arg-type]
    )

    assert first == expected
    assert second == expected
    assert provider.calls == [
        ("/contact/20001", None),
        ("/contact/20001", None),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["unknown", "foreign", "claimed-parent"])
async def test_detail_guard_rejects_untrusted_mapping_before_provider_call(case: str) -> None:
    provider = FakeProvider()
    store = FakeDetailOwnershipStore()
    expected_contact_id: int | None = None
    if case == "foreign":
        store.add(detail_id=20101, contact_id=20002, project_id=10002)
    elif case == "claimed-parent":
        store.add()
        expected_contact_id = 20099
    access = AuthenticatedSession("session-a", "client-a", "primary", 10001)

    with pytest.raises(ReadAccessDenied):
        await verify_contact_detail_parent(
            20101,
            access,
            provider,  # type: ignore[arg-type]
            "primary",
            store,  # type: ignore[arg-type]
            expected_contact_id=expected_contact_id,
        )

    assert provider.calls == []


@pytest.mark.asyncio
async def test_detail_guard_rejects_inactive_mapping_before_provider_call() -> None:
    provider = FakeProvider()
    store = FakeDetailOwnershipStore()
    store.add(active=False)
    access = AuthenticatedSession("session-a", "client-a", "primary", 10001)

    with pytest.raises(ReadAccessDenied):
        await verify_contact_detail_parent(
            20101,
            access,
            provider,  # type: ignore[arg-type]
            "primary",
            store,  # type: ignore[arg-type]
        )

    assert provider.calls == []


@pytest.mark.asyncio
async def test_detail_guard_rejects_incomplete_parent_without_invalidating_history() -> None:
    provider = FakeProvider()
    response = provider.responses["/contact/20001"]
    assert isinstance(response, dict)
    contact = response["result"]
    assert isinstance(contact, dict)
    contact.pop("details")
    store = FakeDetailOwnershipStore()
    expected = store.add()
    access = AuthenticatedSession("session-a", "client-a", "primary", 10001)

    with pytest.raises(ReadAccessDenied):
        await verify_contact_detail_parent(
            20101,
            access,
            provider,  # type: ignore[arg-type]
            "primary",
            store,  # type: ignore[arg-type]
        )

    assert provider.calls == [("/contact/20001", None)]
    assert store.mappings[("primary", "20101")] == expected
    assert all(not path.startswith("/contact/details/") for path, _ in provider.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_kind", ["detail-removed", "contact-moved", "contact-replaced"])
async def test_detail_guard_invalidates_stale_mapping_without_detail_endpoint_call(
    stale_kind: str,
) -> None:
    provider = FakeProvider()
    response = provider.responses["/contact/20001"]
    assert isinstance(response, dict)
    contact = response["result"]
    assert isinstance(contact, dict)
    if stale_kind == "detail-removed":
        contact["details"] = []
    elif stale_kind == "contact-moved":
        contact["project_id"] = 10002
    else:
        contact["id"] = 20099
    store = FakeDetailOwnershipStore()
    store.add()
    access = AuthenticatedSession("session-a", "client-a", "primary", 10001)

    with pytest.raises(ReadAccessDenied):
        await verify_contact_detail_parent(
            20101,
            access,
            provider,  # type: ignore[arg-type]
            "primary",
            store,  # type: ignore[arg-type]
        )

    assert provider.calls == [("/contact/20001", None)]
    assert store.mappings[("primary", "20101")].active is False
    assert all(not path.startswith("/contact/details/") for path, _ in provider.calls)


@pytest.mark.asyncio
async def test_detail_guard_rejects_mapping_changed_during_parent_recheck() -> None:
    class ChangingStore(FakeDetailOwnershipStore):
        async def is_current(self, ownership: DetailOwnership) -> bool:
            return False

    provider = FakeProvider()
    store = ChangingStore()
    store.add()
    access = AuthenticatedSession("session-a", "client-a", "primary", 10001)

    with pytest.raises(ReadAccessDenied):
        await verify_contact_detail_parent(
            20101,
            access,
            provider,  # type: ignore[arg-type]
            "primary",
            store,  # type: ignore[arg-type]
        )

    assert provider.calls == [("/contact/20001", None)]
    assert all(not path.startswith("/contact/details/") for path, _ in provider.calls)


@pytest.mark.asyncio
async def test_detail_guard_fails_closed_when_store_is_unavailable() -> None:
    provider = FakeProvider()
    store = FakeDetailOwnershipStore()
    store.error = DetailOwnershipUnavailable("private database detail")
    access = AuthenticatedSession("session-a", "client-a", "primary", 10001)

    with pytest.raises(DetailOwnershipUnavailable):
        await verify_contact_detail_parent(
            20101,
            access,
            provider,  # type: ignore[arg-type]
            "primary",
            store,  # type: ignore[arg-type]
        )

    assert provider.calls == []


def test_public_detail_routes_remain_closed() -> None:
    application = create_app()
    route_paths = {getattr(route, "path", None) for route in application.routes}

    assert "/contact/details/{detail_id}" not in route_paths
