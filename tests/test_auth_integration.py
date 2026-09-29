from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from proxy_api.auth.rate_limit import LoginAttemptLimiter, LoginAttemptRejected
from proxy_api.auth.service import AuthenticationFailed, AuthService, hash_password, hash_token
from proxy_api.configuration.loader import ClientConfigError, load_client_config
from proxy_api.configuration.service import ConfigRevisionError, ConfigService
from proxy_api.database import AuthClient, AuthSession, ConfigState
from proxy_api.main import create_app

pytestmark = pytest.mark.integration


def require_integration_services() -> None:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("RUN_INTEGRATION_TESTS=1 is required")


def database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if value is None:
        raise RuntimeError("DATABASE_URL is required for integration tests")
    return value


def redis_url() -> str:
    value = os.getenv("REDIS_URL")
    if value is None:
        raise RuntimeError("REDIS_URL is required for integration tests")
    return value


def unused_local_port() -> int:
    with socket.socket() as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])


def start_app_process(config_path: Path, port: int) -> subprocess.Popen[str]:
    environment = os.environ.copy()
    environment["CLIENT_CONFIG_PATH"] = str(config_path)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "proxy_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
            "--no-access-log",
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def wait_for_app(process: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("Application process stopped before becoming ready")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.05)
    raise AssertionError("Application process did not become ready")


def stop_app_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def reset_auth_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(delete(AuthSession))
        await connection.execute(delete(AuthClient))
        await connection.execute(delete(ConfigState))


def client_document(
    *,
    revision: int,
    password_a_hash: str,
    password_b_hash: str,
    project_a: int = 10001,
    enabled_b: bool = True,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "revision": revision,
        "clients": {
            "client_a": {
                "enabled": True,
                "login": "client_a",
                "password_hash": password_a_hash,
                "provider_account": "primary",
                "project_id": project_a,
                "allowed_staff_ids": [],
                "main_owner_display_name": "Owner A",
            },
            "client_b": {
                "enabled": enabled_b,
                "login": "client_b",
                "password_hash": password_b_hash,
                "provider_account": "primary",
                "project_id": 10002,
                "allowed_staff_ids": [],
                "main_owner_display_name": "Owner B",
            },
        },
    }


def write_document(path: Path, document: dict[str, object]) -> Path:
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_config_apply_and_sessions_are_shared_between_processes(tmp_path: Path) -> None:
    require_integration_services()
    password_a_v1 = "synthetic-password-a-v1"
    password_a_v2 = "synthetic-password-a-v2"
    password_b = "synthetic-password-b-v1"
    hash_a_v1 = hash_password(password_a_v1)
    hash_a_v2 = hash_password(password_a_v2)
    hash_b = hash_password(password_b)

    engine_one = create_async_engine(database_url())
    engine_two = create_async_engine(database_url())
    sessions_one = async_sessionmaker(engine_one, expire_on_commit=False)
    sessions_two = async_sessionmaker(engine_two, expire_on_commit=False)
    await reset_auth_database(engine_one)

    try:
        revision_one_path = write_document(
            tmp_path / "revision-1.yaml",
            client_document(
                revision=1,
                password_a_hash=hash_a_v1,
                password_b_hash=hash_b,
            ),
        )
        loaded_revision_one = load_client_config(
            revision_one_path,
            frozenset({"primary"}),
        )
        apply_one = await ConfigService(sessions_one).apply(loaded_revision_one)
        apply_one_from_second_process = await ConfigService(sessions_two).apply(loaded_revision_one)
        assert apply_one.changed is True
        assert apply_one_from_second_process.changed is False

        auth_one = AuthService(
            sessions_one,
            idle_timeout_seconds=86_400,
            expected_config_revision=1,
        )
        auth_two = AuthService(
            sessions_two,
            idle_timeout_seconds=86_400,
            expected_config_revision=1,
        )
        token_a_v1 = await auth_one.login(
            login="client_a",
            password=password_a_v1,
            service="synthetic-service-a",
            version="1",
        )
        token_b = await auth_one.login(
            login="client_b",
            password=password_b,
            service="synthetic-service-b",
            version="1",
        )

        assert (await auth_two.authenticate(token_a_v1)).client_key == "client_a"
        assert (await auth_two.authenticate(token_b)).client_key == "client_b"

        async with sessions_two() as session:
            stored_sessions = (await session.scalars(select(AuthSession))).all()
            assert {stored.token_hash for stored in stored_sessions} == {
                hash_token(token_a_v1),
                hash_token(token_b),
            }
            assert all(token_a_v1.encode() != stored.token_hash for stored in stored_sessions)
            assert all(token_b.encode() != stored.token_hash for stored in stored_sessions)
            client_a = await session.get(AuthClient, "client_a")
            client_b = await session.get(AuthClient, "client_b")
            assert client_a is not None
            assert client_b is not None
            assert client_a.password_hash != password_a_v1
            assert client_b.password_hash != password_b

        same_revision_changed = client_document(
            revision=1,
            password_a_hash=hash_a_v1,
            password_b_hash=hash_b,
        )
        same_revision_changed["clients"]["client_a"]["main_owner_display_name"] = "Changed"
        conflicting_path = write_document(tmp_path / "conflicting.yaml", same_revision_changed)
        with pytest.raises(ConfigRevisionError):
            await ConfigService(sessions_two).apply(
                load_client_config(conflicting_path, frozenset({"primary"}))
            )

        async with sessions_one() as session:
            state = await session.get(ConfigState, 1)
            assert state is not None
            assert state.revision == 1
        assert (await auth_two.authenticate(token_a_v1)).client_key == "client_a"
        assert (await auth_two.authenticate(token_b)).client_key == "client_b"

        invalid_revision = client_document(
            revision=2,
            password_a_hash=hash_a_v1,
            password_b_hash=hash_b,
        )
        invalid_revision["clients"]["client_a"]["provider_account"] = "unknown"
        invalid_path = write_document(tmp_path / "invalid-revision-2.yaml", invalid_revision)
        with pytest.raises(ClientConfigError):
            await ConfigService(sessions_two).apply_file(
                invalid_path,
                frozenset({"primary"}),
            )

        async with sessions_one() as session:
            state = await session.get(ConfigState, 1)
            assert state is not None
            assert state.revision == 1
        assert (await auth_two.authenticate(token_a_v1)).client_key == "client_a"
        assert (await auth_two.authenticate(token_b)).client_key == "client_b"

        revision_two = load_client_config(
            write_document(
                tmp_path / "revision-2.yaml",
                client_document(
                    revision=2,
                    password_a_hash=hash_a_v2,
                    password_b_hash=hash_b,
                ),
            ),
            frozenset({"primary"}),
        )
        apply_two = await ConfigService(sessions_two).apply(revision_two)
        assert apply_two.revoked_client_keys == frozenset({"client_a"})
        with pytest.raises(AuthenticationFailed):
            await auth_one.authenticate(token_a_v1)
        with pytest.raises(AuthenticationFailed):
            await auth_one.authenticate(token_b)
        with pytest.raises(AuthenticationFailed):
            await auth_one.login(
                login="client_b",
                password=password_b,
                service="synthetic-service-b",
                version="2",
            )

        auth_revision_two = AuthService(
            sessions_two,
            idle_timeout_seconds=86_400,
            expected_config_revision=2,
        )
        assert (await auth_revision_two.authenticate(token_b)).client_key == "client_b"
        token_a_v2 = await auth_revision_two.login(
            login="client_a",
            password=password_a_v2,
            service="synthetic-service-a",
            version="2",
        )
        revision_three = load_client_config(
            write_document(
                tmp_path / "revision-3.yaml",
                client_document(
                    revision=3,
                    password_a_hash=hash_a_v2,
                    password_b_hash=hash_b,
                    project_a=10003,
                    enabled_b=False,
                ),
            ),
            frozenset({"primary"}),
        )
        apply_three = await ConfigService(sessions_one).apply(revision_three)
        assert apply_three.revoked_client_keys == frozenset({"client_a", "client_b"})
        with pytest.raises(AuthenticationFailed):
            await auth_revision_two.authenticate(token_a_v2)
        with pytest.raises(AuthenticationFailed):
            await auth_revision_two.authenticate(token_b)
        with pytest.raises(AuthenticationFailed):
            await auth_revision_two.login(
                login="client_b",
                password=password_b,
                service="synthetic-service-b",
                version="2",
            )

        with pytest.raises(ConfigRevisionError):
            await ConfigService(sessions_two).apply(revision_two)
        async with sessions_one() as session:
            state = await session.get(ConfigState, 1)
            assert state is not None
            assert state.revision == 3
    finally:
        await engine_one.dispose()
        await engine_two.dispose()


def test_two_app_instances_fail_closed_across_config_revisions(tmp_path: Path) -> None:
    require_integration_services()

    async def reset_database_and_limiter() -> None:
        engine = create_async_engine(database_url())
        redis_client = Redis.from_url(redis_url(), decode_responses=True)
        try:
            await reset_auth_database(engine)
            await redis_client.delete(
                LoginAttemptLimiter.source_key("127.0.0.1"),
                LoginAttemptLimiter.identity_key("synthetic_b"),
            )
        finally:
            await engine.dispose()
            await redis_client.aclose()

    asyncio.run(reset_database_and_limiter())
    config_one = yaml.safe_load(Path("tests/fixtures/clients.compose.yaml").read_text())
    config_two = yaml.safe_load(Path("tests/fixtures/clients.compose.yaml").read_text())
    config_two["revision"] = 2
    config_two["clients"]["synthetic_a"]["main_owner_display_name"] = "Revision 2 owner"
    revision_one_path = write_document(tmp_path / "app-revision-1.yaml", config_one)
    revision_two_path = write_document(tmp_path / "app-revision-2.yaml", config_two)

    port_one = unused_local_port()
    port_two = unused_local_port()
    while port_two == port_one:
        port_two = unused_local_port()
    process_one = start_app_process(revision_one_path, port_one)
    process_two: subprocess.Popen[str] | None = None
    try:
        wait_for_app(process_one, port_one)
        token = httpx.post(
            f"http://127.0.0.1:{port_one}/login",
            json={
                "login": "synthetic_b",
                "password": "synthetic-client-b-password",
                "service": "synthetic-service",
                "version": "1",
            },
        ).json()["result"]["token"]

        process_two = start_app_process(revision_two_path, port_two)
        wait_for_app(process_two, port_two)
        neutral_error = {
            "status": "error",
            "errors": [{"code": 401, "message": "Authentication failed"}],
        }
        assert httpx.get(f"http://127.0.0.1:{port_one}/health").status_code == 503
        assert (
            httpx.post(
                f"http://127.0.0.1:{port_one}/login",
                json={
                    "login": "synthetic_b",
                    "password": "synthetic-client-b-password",
                    "service": "synthetic-service",
                    "version": "2",
                },
            ).json()
            == neutral_error
        )
        assert (
            httpx.post(
                f"http://127.0.0.1:{port_one}/logout",
                headers={"token": token},
            ).json()
            == neutral_error
        )

        assert httpx.get(f"http://127.0.0.1:{port_two}/health").json() == {"status": "ok"}
        assert httpx.post(
            f"http://127.0.0.1:{port_two}/logout",
            headers={"token": token},
        ).json() == {"status": "success", "result": {}}
        assert (
            "token"
            in httpx.post(
                f"http://127.0.0.1:{port_two}/login",
                json={
                    "login": "synthetic_b",
                    "password": "synthetic-client-b-password",
                    "service": "synthetic-service",
                    "version": "2",
                },
            ).json()["result"]
        )

        stop_app_process(process_one)
        process_one = start_app_process(revision_two_path, port_one)
        wait_for_app(process_one, port_one)

        async def clear_rate_limit_keys(*logins: str) -> None:
            redis_client = Redis.from_url(redis_url(), decode_responses=True)
            try:
                await redis_client.delete(
                    LoginAttemptLimiter.source_key("127.0.0.1"),
                    *(LoginAttemptLimiter.identity_key(login) for login in logins),
                )
            finally:
                await redis_client.aclose()

        asyncio.run(clear_rate_limit_keys("synthetic_a", "unknown"))
        for index in range(5):
            port = port_one if index % 2 == 0 else port_two
            assert (
                httpx.post(
                    f"http://127.0.0.1:{port}/login",
                    json={
                        "login": "synthetic_a",
                        "password": "wrong-synthetic-password",
                        "service": "synthetic-service",
                        "version": "2",
                    },
                ).json()
                == neutral_error
            )
        assert (
            httpx.post(
                f"http://127.0.0.1:{port_two}/login",
                json={
                    "login": "synthetic_a",
                    "password": "synthetic-client-a-password",
                    "service": "synthetic-service",
                    "version": "2",
                },
            ).json()
            == neutral_error
        )

        asyncio.run(clear_rate_limit_keys("synthetic_a", "unknown"))
        for index in range(5):
            port = port_one if index % 2 == 0 else port_two
            assert (
                httpx.post(
                    f"http://127.0.0.1:{port}/login",
                    json={
                        "login": "unknown",
                        "password": "wrong-synthetic-password",
                        "service": "synthetic-service",
                        "version": "2",
                    },
                ).json()
                == neutral_error
            )
        assert (
            httpx.post(
                f"http://127.0.0.1:{port_one}/login",
                json={
                    "login": "synthetic_a",
                    "password": "synthetic-client-a-password",
                    "service": "synthetic-service",
                    "version": "2",
                },
            ).json()
            == neutral_error
        )
    finally:
        if process_two is not None:
            stop_app_process(process_two)
        stop_app_process(process_one)


@pytest.mark.asyncio
async def test_login_limiter_is_atomic_shared_and_contains_no_credentials() -> None:
    require_integration_services()
    source = "synthetic-source-rate-limit"
    login = "synthetic-login-rate-limit"
    password_marker = "synthetic-password-must-not-reach-redis"
    token_marker = "synthetic-token-must-not-reach-redis"
    clients = [Redis.from_url(redis_url(), decode_responses=True) for _ in range(2)]
    limiters = [
        LoginAttemptLimiter(client, attempt_limit=5, window_seconds=60) for client in clients
    ]
    source_key = LoginAttemptLimiter.source_key(source)
    identity_key = LoginAttemptLimiter.identity_key(login)
    await clients[0].delete(source_key, identity_key)

    try:
        results = await asyncio.gather(
            *(limiters[index % 2].check(source=source, login=login) for index in range(12)),
            return_exceptions=True,
        )
        assert sum(result is None for result in results) == 5
        assert sum(isinstance(result, LoginAttemptRejected) for result in results) == 7

        stored = " ".join(
            [
                source_key,
                identity_key,
                *(await clients[0].zrange(source_key, 0, -1)),
                *(await clients[0].zrange(identity_key, 0, -1)),
            ]
        )
        for secret in (source, login, password_marker, token_marker):
            assert secret not in stored
    finally:
        await clients[0].delete(source_key, identity_key)
        await asyncio.gather(*(client.aclose() for client in clients))


def test_login_logout_routes_are_neutral_and_isolated() -> None:
    require_integration_services()

    async def reset_database() -> None:
        engine = create_async_engine(database_url())
        try:
            await reset_auth_database(engine)
        finally:
            await engine.dispose()

    asyncio.run(reset_database())

    with TestClient(create_app()) as client:
        response_a = client.post(
            "/login",
            json={
                "login": "synthetic_a",
                "password": "synthetic-client-a-password",
                "service": "synthetic-service-a",
                "version": "1",
            },
        )
        response_b = client.post(
            "/login",
            json={
                "login": "synthetic_b",
                "password": "synthetic-client-b-password",
                "service": "synthetic-service-b",
                "version": "1",
            },
        )
        assert response_a.status_code == 200
        assert response_b.status_code == 200
        token_a = response_a.json()["result"]["token"]
        token_b = response_b.json()["result"]["token"]
        assert token_a != token_b

        neutral_error = {
            "status": "error",
            "errors": [{"code": 401, "message": "Authentication failed"}],
        }
        for login, password in [
            ("synthetic_a", "wrong-synthetic-password"),
            ("synthetic_disabled", "synthetic-client-a-password"),
            ("unknown", "wrong-synthetic-password"),
        ]:
            assert (
                client.post(
                    "/login",
                    json={
                        "login": login,
                        "password": password,
                        "service": "synthetic-service",
                        "version": "1",
                    },
                ).json()
                == neutral_error
            )

        assert client.post("/logout", headers={"token": "foreign-token"}).json() == neutral_error
        assert client.post("/logout", headers={"token": token_a}).json() == {
            "status": "success",
            "result": {},
        }
        assert client.post("/logout", headers={"token": token_a}).json() == neutral_error
        assert client.post("/logout", headers={"token": token_b}).json() == {
            "status": "success",
            "result": {},
        }

        leaked_password = "x" * 513
        invalid_response = client.post(
            "/login",
            json={
                "login": "synthetic_a",
                "password": leaked_password,
                "service": "synthetic-service",
                "version": "1",
            },
        )
        assert invalid_response.json() == {
            "status": "error",
            "errors": [{"code": 422, "message": "Request is not valid"}],
        }
        assert leaked_password not in invalid_response.text
