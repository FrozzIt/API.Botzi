from __future__ import annotations

import asyncio
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import create_async_engine

from proxy_api.auth.routes import get_login_attempt_limiter
from proxy_api.contacts.routes import get_contact_writer
from proxy_api.contacts.service import ContactWriter
from proxy_api.database import (
    AuthClient,
    AuthSession,
    ConfigState,
    ContactDetailOwnership,
    OperationJournal,
)
from proxy_api.infrastructure import get_provider_adapter, get_session_factory
from proxy_api.main import create_app
from proxy_api.operations import OperationJournalService
from proxy_api.projects.routes import get_configured_provider_account
from proxy_api.provider import DistributedQuotaLimiter, DistributedTokenManager, LPTrackerAdapter
from proxy_api.reads.detail_ownership import DetailOwnershipStore

pytestmark = pytest.mark.integration


class AllowingLimiter:
    async def check(self, *, source: str, login: str) -> None:
        pass


class ControlledQuota:
    def __init__(self, delegate: DistributedQuotaLimiter) -> None:
        self._delegate = delegate
        self._wait_started: Event | None = None
        self._release_wait: Event | None = None

    def block_next(self) -> tuple[Event, Event]:
        self._wait_started = Event()
        self._release_wait = Event()
        return self._wait_started, self._release_wait

    async def acquire(self, deadline: float) -> None:
        if self._wait_started is not None and self._release_wait is not None:
            wait_started = self._wait_started
            release_wait = self._release_wait
            self._wait_started = None
            self._release_wait = None
            wait_started.set()
            if not await asyncio.to_thread(release_wait.wait, 1):
                raise AssertionError("Controlled quota wait was not released")
        await self._delegate.acquire(deadline)


def require_database_url() -> str:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("RUN_INTEGRATION_TESTS=1 is required")
    value = os.getenv("DATABASE_URL")
    if value is None:
        raise RuntimeError("DATABASE_URL is required for integration tests")
    return value


def require_redis_url() -> str:
    value = os.getenv("REDIS_URL")
    if value is None:
        raise RuntimeError("REDIS_URL is required for integration tests")
    return value


async def reset_database() -> None:
    engine = create_async_engine(require_database_url())
    try:
        async with engine.begin() as connection:
            await connection.execute(delete(OperationJournal))
            await connection.execute(delete(ContactDetailOwnership))
            await connection.execute(delete(AuthSession))
            await connection.execute(delete(AuthClient))
            await connection.execute(delete(ConfigState))
    finally:
        await engine.dispose()


async def set_client_project(project_id: int) -> None:
    engine = create_async_engine(require_database_url())
    try:
        async with engine.begin() as connection:
            result = await connection.execute(
                update(AuthClient)
                .where(AuthClient.client_key == "synthetic_a")
                .values(project_id=project_id)
            )
            if result.rowcount != 1:
                raise AssertionError("Synthetic client project was not updated")
    finally:
        await engine.dispose()


def test_full_contact_route_is_idempotent_unknown_safe_and_guarded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    asyncio.run(reset_database())
    redis_client = Redis.from_url(require_redis_url(), decode_responses=True)
    namespace = uuid.uuid4().hex
    quota = ControlledQuota(
        DistributedQuotaLimiter(
            redis_client,
            limit=3,
            window_seconds=0.05,
            key=f"proxyapi:test:contact-quota:{namespace}",
        )
    )
    mode = "success"
    contact_posts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal contact_posts
        if request.url.path == "/login":
            return httpx.Response(
                200,
                json={"status": "success", "result": {"token": "synthetic-provider-token"}},
            )
        if request.url.path != "/contact":
            raise AssertionError("Unexpected provider path")
        contact_posts += 1
        if mode == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "result": {
                    "id": 20000 + contact_posts,
                    "project_id": payload["project_id"],
                    "name": payload["name"],
                    "details": [
                        {
                            "id": 21000 + contact_posts,
                            "type": payload["details"][0]["type"],
                            "data": payload["details"][0]["data"],
                        }
                    ],
                },
            },
        )

    adapter = LPTrackerAdapter(
        httpx.AsyncClient(
            base_url="https://provider.example",
            transport=httpx.MockTransport(handler),
            timeout=None,
        ),
        quota,  # type: ignore[arg-type]
        DistributedTokenManager(
            redis_client,
            account=f"contact-integration-{namespace}",
            encryption_key=Fernet.generate_key().decode("ascii"),
            token_ttl_seconds=60,
            refresh_lock_seconds=2,
        ),
        login="synthetic-login",
        password="synthetic-password",
        service="synthetic-service",
        version="1",
        default_deadline_seconds=2,
        http_timeout_seconds=0.05,
    )

    def writer() -> ContactWriter:
        session_factory = get_session_factory()
        return ContactWriter(
            OperationJournalService(
                session_factory,
                digest_secret=b"contact-route-integration-secret",
                pending_lease_seconds=3,
            ),
            DetailOwnershipStore(session_factory),
        )

    application = create_app()
    application.dependency_overrides[get_login_attempt_limiter] = AllowingLimiter
    application.dependency_overrides[get_provider_adapter] = lambda: adapter
    application.dependency_overrides[get_configured_provider_account] = lambda: "primary"
    application.dependency_overrides[get_contact_writer] = writer

    login_body = {
        "login": "synthetic_a",
        "password": "synthetic-client-a-password",
        "service": "integration-test",
        "version": "1",
    }
    contact_body = {
        "name": "Synthetic Contact",
        "details": [{"type": "email", "data": "contact@example.invalid"}],
    }

    try:
        with TestClient(application) as client:
            token = client.post("/login", json=login_body).json()["result"]["token"]
            pii_like_key = "person@example.invalid bearer-secret-sentinel"
            headers = {"token": token, "Idempotency-Key": pii_like_key}

            first = client.post("/contact", headers=headers, json=contact_body)
            replay = client.post("/contact", headers=headers, json=contact_body)

            assert first.json()["status"] == "success"
            assert replay.json() == first.json()
            assert replay.headers["X-Request-ID"] == first.headers["X-Request-ID"]
            assert contact_posts == 1

            mode = "timeout"
            unknown_headers = {"token": token, "Idempotency-Key": "route-unknown-key"}
            unknown = client.post("/contact", headers=unknown_headers, json=contact_body)
            mode = "success"
            unknown_replay = client.post(
                "/contact",
                headers=unknown_headers,
                json=contact_body,
            )

            assert unknown.json()["errors"][0]["code"] == 520
            assert unknown_replay.json() == unknown.json()
            assert unknown_replay.headers["X-Request-ID"] == unknown.headers["X-Request-ID"]
            assert contact_posts == 2

            wait_started, release_wait = quota.block_next()
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(
                    client.post,
                    "/contact",
                    headers={"token": token, "Idempotency-Key": "route-logout-key"},
                    json=contact_body,
                )
                assert wait_started.wait(1)
                assert (
                    client.post("/logout", headers={"token": token}).json()["status"] == "success"
                )
                release_wait.set()
                rejected_after_logout = pending.result(timeout=2)

            assert rejected_after_logout.json()["errors"][0]["code"] == 401
            assert contact_posts == 2

            token = client.post("/login", json=login_body).json()["result"]["token"]
            wait_started, release_wait = quota.block_next()
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(
                    client.post,
                    "/contact",
                    headers={"token": token, "Idempotency-Key": "route-reassign-key"},
                    json=contact_body,
                )
                assert wait_started.wait(1)
                asyncio.run(set_client_project(10002))
                release_wait.set()
                rejected_after_reassignment = pending.result(timeout=2)

            assert rejected_after_reassignment.json()["errors"][0]["code"] == 401
            assert contact_posts == 2
            assert pii_like_key not in caplog.text

            client.portal.call(adapter.aclose)
            client.portal.call(redis_client.aclose)
    finally:
        asyncio.run(set_client_project(10001))
        asyncio.run(reset_database())
