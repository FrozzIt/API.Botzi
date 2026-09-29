from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from proxy_api.infrastructure import database_is_ready, redis_is_ready
from proxy_api.main import config_revision_is_current, create_app


@asynccontextmanager
async def no_op_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


@pytest.fixture
def app() -> FastAPI:
    application = create_app()
    application.router.lifespan_context = no_op_lifespan
    return application


def set_readiness(app: FastAPI, *, database: bool, redis: bool) -> None:
    async def database_check() -> bool:
        return database

    async def redis_check() -> bool:
        return redis

    async def config_check() -> bool:
        return True

    app.dependency_overrides[database_is_ready] = database_check
    app.dependency_overrides[redis_is_ready] = redis_check
    app.dependency_overrides[config_revision_is_current] = config_check


def test_healthcheck_is_neutral_when_dependencies_are_ready(app: FastAPI) -> None:
    set_readiness(app, database=True, redis=True)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "postgres" not in response.text.lower()
    assert "redis" not in response.text.lower()


@pytest.mark.parametrize(
    ("database", "redis"),
    [(False, True), (True, False), (False, False)],
)
def test_healthcheck_is_neutral_when_a_dependency_is_unavailable(
    app: FastAPI,
    database: bool,
    redis: bool,
) -> None:
    set_readiness(app, database=database, redis=redis)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "postgres" not in response.text.lower()
    assert "redis" not in response.text.lower()
    assert "exception" not in response.text.lower()


def test_public_api_schema_is_not_published_in_task_1_1(app: FastAPI) -> None:
    set_readiness(app, database=True, redis=True)

    with TestClient(app) as client:
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
