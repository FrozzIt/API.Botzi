import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from proxy_api.database import AuthClient, AuthSession, ConfigState
from proxy_api.infrastructure import get_database_engine
from proxy_api.main import create_app

pytestmark = pytest.mark.integration


def require_integration_services() -> None:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("RUN_INTEGRATION_TESTS=1 is required")


def test_healthcheck_with_postgresql_and_redis() -> None:
    require_integration_services()

    async def reset_config() -> None:
        database_url = os.environ["DATABASE_URL"]
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(delete(AuthSession))
                await connection.execute(delete(AuthClient))
                await connection.execute(delete(ConfigState))
        finally:
            await engine.dispose()

    asyncio.run(reset_config())

    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_initial_migration_is_applied() -> None:
    require_integration_services()

    async with get_database_engine().connect() as connection:
        table_names = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_table_names()
        )

    assert "alembic_version" in table_names
    assert "auth_clients" in table_names
    assert "auth_sessions" in table_names
    assert "config_state" in table_names
    assert "service_state" in table_names
