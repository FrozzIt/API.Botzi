from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from threading import Event

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import Request
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from proxy_api.auth.routes import get_auth_service, get_login_attempt_limiter
from proxy_api.auth.service import AuthenticatedSession, AuthService
from proxy_api.contacts.routes import get_contact_mutation_writer, get_contact_writer
from proxy_api.contacts.service import ContactMutationWriter, ContactWriter
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


class GuardFailureControl:
    def __init__(self) -> None:
        self.fail_after_exit = False


class ControlledGuardAuthService:
    def __init__(self, delegate: AuthService, control: GuardFailureControl) -> None:
        self._delegate = delegate
        self._control = control

    async def login(self, **kwargs: str) -> str:
        return await self._delegate.login(**kwargs)

    async def authenticate(self, token: str) -> AuthenticatedSession:
        return await self._delegate.authenticate(token)

    async def logout(self, token: str) -> None:
        await self._delegate.logout(token)

    @asynccontextmanager
    async def provider_request_guard(
        self,
        access: AuthenticatedSession,
    ) -> AsyncIterator[None]:
        async with self._delegate.provider_request_guard(access):
            yield
        if self._control.fail_after_exit:
            self._control.fail_after_exit = False
            raise SQLAlchemyError("synthetic guard completion failure")


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


async def operation_count() -> int:
    engine = create_async_engine(require_database_url())
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(select(func.count()).select_from(OperationJournal))
            return int(value or 0)
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
    digest_secret = b"contact-route-integration-secret"
    contact_posts = 0
    detail_puts = 0
    field_deletes = 0
    detail_value = "contact@example.invalid"
    guard_failure = GuardFailureControl()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal contact_posts, detail_puts, field_deletes, detail_value
        if request.url.path == "/login":
            return httpx.Response(
                200,
                json={"status": "success", "result": {"token": "synthetic-provider-token"}},
            )
        if request.method == "GET" and request.url.path == "/contact/20001":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "result": {
                        "id": 20001,
                        "project_id": 10001,
                        "name": "Synthetic Contact",
                        "details": [{"id": 21001, "type": "email", "data": detail_value}],
                        "fields": [],
                    },
                },
            )
        if request.method == "GET" and request.url.path == "/contact/details/21001":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "result": {"id": 21001, "type": "email", "data": detail_value},
                },
            )
        if request.method == "PUT" and request.url.path == "/contact/details/21001":
            detail_puts += 1
            detail_value = json.loads(request.content)["value"]
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "result": {"id": 21001, "type": "email", "data": detail_value},
                },
            )
        if request.method == "GET" and request.url.path == "/contact/20001/leads":
            return httpx.Response(200, json={"status": "success", "result": []})
        if request.method == "GET" and request.url.path == "/project/10001/fields":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "result": [{"id": 31001, "name": "Safe field", "type": "string"}],
                },
            )
        if request.method == "GET" and request.url.path == "/contact/20001/field/31001":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "result": {
                        "id": 31001,
                        "name": "Safe field",
                        "type": "string",
                        "value": "safe",
                    },
                },
            )
        if request.method == "DELETE" and request.url.path == "/contact/20001/field/31001":
            field_deletes += 1
            return httpx.Response(200, json={"status": "success"})
        if request.method != "POST" or request.url.path != "/contact":
            raise AssertionError(f"Unexpected provider request: {request.method}")
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
                digest_secret=digest_secret,
                pending_lease_seconds=3,
            ),
            DetailOwnershipStore(session_factory),
        )

    def mutation_writer() -> ContactMutationWriter:
        return ContactMutationWriter(
            OperationJournalService(
                get_session_factory(),
                digest_secret=digest_secret,
                pending_lease_seconds=3,
            )
        )

    application = create_app()

    def controlled_auth_service(request: Request) -> ControlledGuardAuthService:
        return ControlledGuardAuthService(get_auth_service(request), guard_failure)

    application.dependency_overrides[get_auth_service] = controlled_auth_service
    application.dependency_overrides[get_login_attempt_limiter] = AllowingLimiter
    application.dependency_overrides[get_provider_adapter] = lambda: adapter
    application.dependency_overrides[get_configured_provider_account] = lambda: "primary"
    application.dependency_overrides[get_contact_writer] = writer
    application.dependency_overrides[get_contact_mutation_writer] = mutation_writer

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
            blank = client.post(
                "/contact",
                headers={"token": token, "Idempotency-Key": "   "},
                json=contact_body,
            )
            too_long = client.post(
                "/contact",
                headers={"token": token, "Idempotency-Key": "x" * 129},
                json=contact_body,
            )

            assert blank.json()["errors"][0]["code"] == 422
            assert too_long.json()["errors"][0]["code"] == 422
            assert contact_posts == 0
            assert asyncio.run(operation_count()) == 0

            foreign_query = client.post(
                "/contact?project_id=10002",
                headers={"token": token, "Idempotency-Key": "query-foreign-key"},
                json=contact_body,
            )
            conflicting_query = client.post(
                "/contact?project_id=10002",
                headers={"token": token, "Idempotency-Key": "query-conflict-key"},
                json={**contact_body, "project_id": 10001},
            )
            repeated_query = client.post(
                "/contact",
                params=[("project_id", "10001"), ("project_id", "10001")],
                headers={"token": token, "Idempotency-Key": "query-repeat-key"},
                json=contact_body,
            )

            assert foreign_query.json()["errors"][0]["code"] == 404
            assert conflicting_query.json()["errors"][0]["code"] == 404
            assert repeated_query.json()["errors"][0]["code"] == 404
            assert contact_posts == 0
            assert asyncio.run(operation_count()) == 0

            pii_like_key = "person@example.invalid bearer-secret-sentinel"
            headers = {"token": token, "Idempotency-Key": pii_like_key}

            first = client.post("/contact", headers=headers, json=contact_body)
            replay = client.post("/contact", headers=headers, json=contact_body)

            assert first.json()["status"] == "success"
            assert replay.json() == first.json()
            assert replay.headers["X-Request-ID"] == first.headers["X-Request-ID"]
            assert contact_posts == 1

            detail = client.get("/contact/details/21001", headers={"token": token})
            detail_headers = {
                "token": token,
                "Idempotency-Key": "route-detail-edit-key",
            }
            detail_edit = client.put(
                "/contact/details/21001",
                headers=detail_headers,
                json={"value": "changed@example.invalid"},
            )
            detail_replay = client.put(
                "/contact/details/21001",
                headers=detail_headers,
                json={"value": "changed@example.invalid"},
            )
            leads = client.get("/contact/20001/leads", headers={"token": token})
            field = client.get("/contact/20001/field/31001", headers={"token": token})
            field_headers = {
                "token": token,
                "Idempotency-Key": "route-field-clear-key",
            }
            field_clear = client.delete(
                "/contact/20001/field/31001",
                headers=field_headers,
            )
            field_replay = client.delete(
                "/contact/20001/field/31001",
                headers=field_headers,
            )
            provider_calls_before_deferred = contact_posts + detail_puts + field_deletes
            deferred = client.delete(
                "/contact/details/21001",
                headers={"token": token},
            )

            assert detail.json()["result"]["data"] == "contact@example.invalid"
            assert detail_edit.json()["result"]["data"] == "changed@example.invalid"
            assert detail_replay.json() == detail_edit.json()
            assert detail_replay.headers["X-Request-ID"] == detail_edit.headers["X-Request-ID"]
            assert detail_puts == 1
            assert leads.json() == {"status": "success", "result": []}
            assert field.json()["result"]["value"] == "safe"
            assert field_clear.json() == {"status": "success"}
            assert field_replay.json() == field_clear.json()
            assert field_replay.headers["X-Request-ID"] == field_clear.headers["X-Request-ID"]
            assert field_deletes == 1
            assert deferred.json()["errors"][0]["code"] == 501
            assert contact_posts + detail_puts + field_deletes == provider_calls_before_deferred

            digest_secret = b"rotated-contact-route-integration-secret"
            rejected_rotated_success = client.post(
                "/contact",
                headers=headers,
                json=contact_body,
            )
            digest_secret = b"contact-route-integration-secret"

            assert rejected_rotated_success.json()["errors"][0]["code"] == 503
            assert contact_posts == 1

            mode = "timeout"
            unknown_headers = {"token": token, "Idempotency-Key": "route-unknown-key"}
            unknown = client.post("/contact", headers=unknown_headers, json=contact_body)
            mode = "success"
            digest_secret = b"rotated-contact-route-integration-secret"
            rejected_rotated_unknown = client.post(
                "/contact",
                headers=unknown_headers,
                json=contact_body,
            )
            digest_secret = b"contact-route-integration-secret"
            unknown_replay = client.post(
                "/contact",
                headers=unknown_headers,
                json=contact_body,
            )

            assert unknown.json()["errors"][0]["code"] == 520
            assert rejected_rotated_unknown.json()["errors"][0]["code"] == 503
            assert unknown_replay.json() == unknown.json()
            assert unknown_replay.headers["X-Request-ID"] == unknown.headers["X-Request-ID"]
            assert contact_posts == 2

            guard_failure.fail_after_exit = True
            guard_headers = {"token": token, "Idempotency-Key": "route-guard-exit-key"}
            guard_failed = client.post(
                "/contact",
                headers=guard_headers,
                json=contact_body,
            )
            guard_replay = client.post(
                "/contact",
                headers=guard_headers,
                json=contact_body,
            )

            assert guard_failed.json()["errors"][0]["code"] == 520
            assert guard_replay.json() == guard_failed.json()
            assert guard_replay.headers["X-Request-ID"] == guard_failed.headers["X-Request-ID"]
            assert contact_posts == 3

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
            assert contact_posts == 3

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
            assert contact_posts == 3
            assert pii_like_key not in caplog.text

            client.portal.call(adapter.aclose)
            client.portal.call(redis_client.aclose)
    finally:
        asyncio.run(set_client_project(10001))
        asyncio.run(reset_database())
