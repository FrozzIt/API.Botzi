from __future__ import annotations

import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine

from proxy_api.auth.routes import get_login_attempt_limiter
from proxy_api.database import AuthClient, AuthSession, ConfigState
from proxy_api.infrastructure import get_provider_adapter
from proxy_api.main import create_app
from proxy_api.projects.routes import get_configured_provider_account

pytestmark = pytest.mark.integration


class AllowingLimiter:
    async def check(self, *, source: str, login: str) -> None:
        pass


class RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get(self, path: str) -> dict[str, object]:
        self.calls.append(path)
        project_id = int(path.rsplit("/", 1)[1])
        return {
            "status": "success",
            "result": {"id": project_id, "name": f"Project {project_id}"},
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
                {"id": 10001, "name": "Project 10001"}
            ]
            assert client.get("/projects", headers={"token": token_b}).json()["result"] == [
                {"id": 10002, "name": "Project 10002"}
            ]

            calls_before_rejections = list(provider.calls)
            assert client.get("/project/10002", headers={"token": token_a}).json() == {
                "status": "error",
                "errors": [{"code": 404, "message": "Project not found"}],
            }
            assert client.get("/projects", headers={"token": "invalid-token"}).json() == {
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
            }
    finally:
        asyncio.run(reset_database())
