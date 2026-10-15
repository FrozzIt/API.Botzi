from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine

from proxy_api.auth.routes import get_login_attempt_limiter
from proxy_api.database import AuthClient, AuthSession, ConfigState
from proxy_api.infrastructure import get_provider_adapter
from proxy_api.main import create_app
from proxy_api.projects.routes import get_configured_provider_account
from proxy_api.provider.client import ProviderSendGuard

pytestmark = pytest.mark.integration


class AllowingLimiter:
    async def check(self, *, source: str, login: str) -> None:
        pass


class RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    @staticmethod
    def contact(project_id: int, contact_id: int) -> dict[str, object]:
        return {
            "id": contact_id,
            "project_id": project_id,
            "details": [],
            "fields": [],
            "name": f"Contact {contact_id}",
        }

    @classmethod
    def lead(
        cls,
        project_id: int,
        lead_id: int,
        contact_id: int,
    ) -> dict[str, object]:
        return {
            "id": lead_id,
            "contact_id": contact_id,
            "name": f"Lead {lead_id}",
            "contact": cls.contact(project_id, contact_id),
        }

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | Sequence[tuple[str, str | int]] | None = None,
        deadline_seconds: float | None = None,
        send_guard: ProviderSendGuard | None = None,
    ) -> dict[str, object]:
        del deadline_seconds
        if send_guard is not None:
            async with send_guard():
                return self._get(path, params=params)
        return self._get(path, params=params)

    def _get(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | Sequence[tuple[str, str | int]] | None = None,
    ) -> dict[str, object]:
        del params
        self.calls.append(path)
        if path.startswith("/project/") and path.endswith("/customs"):
            project_id = int(path.split("/")[2])
            return {
                "status": "success",
                "result": [
                    {
                        "id": project_id + 60000,
                        "name": f"Custom {project_id}",
                        "type": "text",
                    }
                ],
            }
        if path.startswith("/project/") and path.endswith("/fields"):
            project_id = int(path.split("/")[2])
            return {
                "status": "success",
                "result": [
                    {
                        "id": project_id + 61000,
                        "name": f"Field {project_id}",
                        "type": "string",
                    }
                ],
            }
        if path.startswith("/project/") and "/funnel/" in path:
            project_id = int(path.split("/")[2])
            funnel_id = int(path.rsplit("/", 1)[1])
            return {
                "status": "success",
                "result": {
                    "id": funnel_id,
                    "name": f"Funnel {project_id}",
                    "notify": False,
                    "in_leads": True,
                    "in_deals": False,
                },
            }
        if path.startswith("/project/") and path.endswith("/funnel"):
            project_id = int(path.split("/")[2])
            return {
                "status": "success",
                "result": [
                    {
                        "id": project_id + 62000,
                        "name": f"Funnel {project_id}",
                        "notify": False,
                        "in_leads": True,
                        "in_deals": False,
                    }
                ],
            }
        if path.startswith("/label/") and path.endswith("/list"):
            project_id = int(path.split("/")[2])
            return {
                "status": "success",
                "result": [
                    {
                        "id": project_id + 63000,
                        "name": f"Label {project_id}",
                        "color": "F7F0C5",
                    }
                ],
            }
        if path.startswith("/contact/"):
            contact_id = int(path.rsplit("/", 1)[1])
            project_id = 10001 if contact_id == 20001 else 10002
            return {
                "status": "success",
                "result": self.contact(project_id, contact_id),
            }
        if path.startswith("/lead/") and path.endswith("/list"):
            project_id = int(path.split("/")[2])
            lead_id = 30001 if project_id == 10001 else 30002
            contact_id = 20001 if project_id == 10001 else 20002
            return {
                "status": "success",
                "result": [self.lead(project_id, lead_id, contact_id)],
            }
        if path.startswith("/lead/"):
            lead_id = int(path.rsplit("/", 1)[1])
            project_id = 10001 if lead_id == 30001 else 10002
            contact_id = 20001 if project_id == 10001 else 20002
            return {
                "status": "success",
                "result": self.lead(project_id, lead_id, contact_id),
            }

        project_id = int(path.rsplit("/", 1)[1])
        return {
            "status": "success",
            "result": {
                "id": project_id,
                "name": f"Project {project_id}",
                "page": f"project-{project_id}",
                "domain": f"project-{project_id}.example",
            },
        }


def require_database_url() -> str:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("RUN_INTEGRATION_TESTS=1 is required")
    value = os.getenv("DATABASE_URL")
    if value is None:
        raise RuntimeError("DATABASE_URL is required for integration tests")
    return value


def test_login_own_project_and_logout_are_isolated() -> None:
    async def reset_database() -> None:
        engine = create_async_engine(require_database_url())
        try:
            async with engine.begin() as connection:
                await connection.execute(delete(AuthSession))
                await connection.execute(delete(AuthClient))
                await connection.execute(delete(ConfigState))
        finally:
            await engine.dispose()

    asyncio.run(reset_database())
    provider = RecordingProvider()
    application = create_app()
    application.dependency_overrides[get_login_attempt_limiter] = AllowingLimiter
    application.dependency_overrides[get_provider_adapter] = lambda: provider
    application.dependency_overrides[get_configured_provider_account] = lambda: "primary"

    try:
        login_payload = {
            "service": "integration-test",
            "version": "1",
        }
        with TestClient(application) as client:
            token_a = client.post(
                "/login",
                json={
                    **login_payload,
                    "login": "synthetic_a",
                    "password": "synthetic-client-a-password",
                },
            ).json()["result"]["token"]
            token_b = client.post(
                "/login",
                json={
                    **login_payload,
                    "login": "synthetic_b",
                    "password": "synthetic-client-b-password",
                },
            ).json()["result"]["token"]

            assert client.get("/projects", headers={"token": token_a}).json()["result"] == [
                {
                    "id": 10001,
                    "name": "Project 10001",
                    "page": "project-10001",
                    "domain": "project-10001.example",
                }
            ]
            assert client.get("/projects", headers={"token": token_b}).json()["result"] == [
                {
                    "id": 10002,
                    "name": "Project 10002",
                    "page": "project-10002",
                    "domain": "project-10002.example",
                }
            ]

            assert (
                client.get("/contact/20001", headers={"token": token_a}).json()["result"][
                    "project_id"
                ]
                == 10001
            )
            assert (
                client.get("/contact/20002", headers={"token": token_b}).json()["result"][
                    "project_id"
                ]
                == 10002
            )
            assert (
                client.get("/lead/30001", headers={"token": token_a}).json()["result"]["contact"][
                    "project_id"
                ]
                == 10001
            )
            assert (
                client.get("/lead/30002", headers={"token": token_b}).json()["result"]["contact"][
                    "project_id"
                ]
                == 10002
            )
            assert (
                client.get("/lead/10001/list", headers={"token": token_a}).json()["result"][0]["id"]
                == 30001
            )
            assert (
                client.get("/lead/10002/list", headers={"token": token_b}).json()["result"][0]["id"]
                == 30002
            )
            assert (
                client.get("/project/10001/customs", headers={"token": token_a}).json()["result"][
                    0
                ]["id"]
                == 70001
            )
            assert (
                client.get("/project/10002/fields", headers={"token": token_b}).json()["result"][0][
                    "id"
                ]
                == 71002
            )
            assert (
                client.get("/project/10001/funnel", headers={"token": token_a}).json()["result"][0][
                    "id"
                ]
                == 72001
            )
            assert (
                client.get(
                    "/project/10002/funnel/72002",
                    headers={"token": token_b},
                ).json()["result"]["id"]
                == 72002
            )
            assert (
                client.get("/label/10001/list", headers={"token": token_a}).json()["result"][0][
                    "id"
                ]
                == 73001
            )

            calls_before_rejections = list(provider.calls)
            assert client.get("/project/10002", headers={"token": token_a}).json() == {
                "status": "error",
                "errors": [{"code": 404, "message": "Project not found"}],
            }
            assert client.get("/projects", headers={"token": "invalid-token"}).json() == {
                "status": "error",
                "errors": [{"code": 401, "message": "Authentication failed"}],
            }
            assert client.get("/project/10002/funnel", headers={"token": token_a}).json() == {
                "status": "error",
                "errors": [{"code": 404, "message": "Object not found"}],
            }
            assert client.get("/label/10001/list", headers={"token": "invalid-token"}).json() == {
                "status": "error",
                "errors": [{"code": 401, "message": "Authentication failed"}],
            }
            assert provider.calls == calls_before_rejections

            assert client.post("/logout", headers={"token": token_a}).json() == {
                "status": "success",
                "result": {},
            }
            assert client.get("/projects", headers={"token": token_a}).json() == {
                "status": "error",
                "errors": [{"code": 401, "message": "Authentication failed"}],
            }
            assert client.get("/project/10002", headers={"token": token_b}).json()["result"] == {
                "id": 10002,
                "name": "Project 10002",
                "page": "project-10002",
                "domain": "project-10002.example",
            }
    finally:
        asyncio.run(reset_database())
