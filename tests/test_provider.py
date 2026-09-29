from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

import httpx
import pytest
from redis.exceptions import RedisError

from proxy_api.provider.client import LPTrackerAdapter
from proxy_api.provider.errors import (
    ProviderAuthenticationError,
    ProviderDeadlineExceeded,
    ProviderProtocolError,
    ProviderUnavailable,
)
from proxy_api.provider.quota import DistributedQuotaLimiter
from proxy_api.provider.token import ProviderToken


class RecordingQuota:
    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self, deadline: float) -> None:
        self.calls += 1


class CallbackTokenManager:
    def __init__(self) -> None:
        self.current: ProviderToken | None = None
        self.refresh_calls = 0

    async def current_or_refresh(
        self,
        login: Callable[[float], Awaitable[str]],
        deadline: float,
    ) -> ProviderToken:
        if self.current is None:
            self.current = ProviderToken(await login(deadline), "generation-1")
        return self.current

    async def refresh_after_unauthorized(
        self,
        stale_generation: str,
        login: Callable[[float], Awaitable[str]],
        deadline: float,
    ) -> ProviderToken:
        self.refresh_calls += 1
        self.current = ProviderToken(await login(deadline), "generation-2")
        return self.current


class BlockingQuota:
    async def acquire(self, deadline: float) -> None:
        await asyncio.sleep(1)


class UnavailableRedis:
    async def eval(self, *_: object) -> object:
        raise RedisError("synthetic Redis detail must stay private")


def test_internal_token_repr_is_redacted() -> None:
    token = ProviderToken("internal-token-secret-marker", "generation")

    assert "internal-token-secret-marker" not in repr(token)


def make_adapter(
    handler: httpx.AsyncBaseTransport,
    quota: object,
    tokens: object,
    *,
    deadline: float = 1,
    http_timeout: float = 1,
) -> LPTrackerAdapter:
    return LPTrackerAdapter(
        httpx.AsyncClient(base_url="https://provider.invalid", transport=handler),
        quota,  # type: ignore[arg-type]
        tokens,  # type: ignore[arg-type]
        login="server-login-marker",
        password="server-password-marker",
        service="server-service-marker",
        version="server-version-marker",
        default_deadline_seconds=deadline,
        http_timeout_seconds=http_timeout,
    )


@pytest.mark.asyncio
async def test_login_401_refresh_and_retry_share_quota_and_deadline() -> None:
    requests: list[httpx.Request] = []
    login_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count
        requests.append(request)
        if request.url.path == "/login":
            login_count += 1
            assert json.loads(request.content) == {
                "login": "server-login-marker",
                "password": "server-password-marker",
                "service": "server-service-marker",
                "version": "server-version-marker",
            }
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "result": {"token": f"internal-token-{login_count}"},
                },
            )
        if request.headers["token"] == "internal-token-1":
            return httpx.Response(
                200,
                json={"status": "error", "errors": [{"code": 401, "message": "raw"}]},
            )
        return httpx.Response(200, json={"status": "success", "result": {"ok": True}})

    quota = RecordingQuota()
    tokens = CallbackTokenManager()
    adapter = make_adapter(httpx.MockTransport(handler), quota, tokens)
    try:
        response = await adapter.get("/safe-read")
    finally:
        await adapter.aclose()

    assert response == {"status": "success", "result": {"ok": True}}
    assert login_count == 2
    assert tokens.refresh_calls == 1
    assert quota.calls == 4
    assert [request.url.path for request in requests] == [
        "/login",
        "/safe-read",
        "/login",
        "/safe-read",
    ]


@pytest.mark.asyncio
async def test_second_401_is_not_retried_or_exposed() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.path == "/login":
            return httpx.Response(
                200,
                json={"status": "success", "result": {"token": f"secret-{calls}"}},
            )
        return httpx.Response(
            200,
            json={
                "status": "error",
                "errors": [{"code": 401, "message": "raw-provider-secret-marker"}],
            },
        )

    adapter = make_adapter(
        httpx.MockTransport(handler),
        RecordingQuota(),
        CallbackTokenManager(),
    )
    try:
        with pytest.raises(ProviderAuthenticationError) as captured:
            await adapter.get("/safe-read")
    finally:
        await adapter.aclose()

    assert calls == 4
    assert "raw-provider-secret-marker" not in str(captured.value)
    assert "secret-" not in str(captured.value)


@pytest.mark.asyncio
async def test_quota_wait_and_http_call_are_bounded_by_deadline() -> None:
    async def unexpected_request(_: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP must not start after quota deadline")

    adapter = make_adapter(
        httpx.MockTransport(unexpected_request),
        BlockingQuota(),
        CallbackTokenManager(),
        deadline=0.02,
    )
    try:
        with pytest.raises(ProviderDeadlineExceeded):
            await adapter.get("/safe-read")
    finally:
        await adapter.aclose()

    async def slow_request(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json={"status": "success"})

    adapter = make_adapter(
        httpx.MockTransport(slow_request),
        RecordingQuota(),
        CallbackTokenManager(),
        deadline=0.05,
        http_timeout=0.02,
    )
    try:
        with pytest.raises(ProviderDeadlineExceeded):
            await adapter.get("/safe-read")
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_redis_failure_does_not_bypass_quota() -> None:
    limiter = DistributedQuotaLimiter(UnavailableRedis())  # type: ignore[arg-type]
    deadline = asyncio.get_running_loop().time() + 1

    with pytest.raises(ProviderUnavailable) as captured:
        await limiter.acquire(deadline)

    assert str(captured.value) == "Provider unavailable"
    assert "Redis" not in str(captured.value)


@pytest.mark.asyncio
async def test_raw_invalid_provider_response_is_not_exposed() -> None:
    marker = "raw-provider-body-secret-marker"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=marker)

    tokens = CallbackTokenManager()
    tokens.current = ProviderToken("internal-token", "generation")
    adapter = make_adapter(httpx.MockTransport(handler), RecordingQuota(), tokens)
    try:
        with pytest.raises(ProviderProtocolError) as captured:
            await adapter.get("/safe-read")
    finally:
        await adapter.aclose()

    assert marker not in str(captured.value)


@pytest.mark.asyncio
async def test_request_path_cannot_override_configured_provider_host() -> None:
    async def unexpected_request(_: httpx.Request) -> httpx.Response:
        raise AssertionError("Untrusted absolute provider URL must not be requested")

    adapter = make_adapter(
        httpx.MockTransport(unexpected_request),
        RecordingQuota(),
        CallbackTokenManager(),
    )
    try:
        with pytest.raises(ValueError):
            await adapter.get("https://attacker.invalid/projects")
        with pytest.raises(ValueError):
            await adapter.get("//attacker.invalid/projects")
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_redirect_is_not_followed_or_exposed() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            302,
            headers={"Location": "https://internal-provider.invalid/secret-marker"},
        )

    tokens = CallbackTokenManager()
    tokens.current = ProviderToken("internal-token", "generation")
    adapter = make_adapter(httpx.MockTransport(handler), RecordingQuota(), tokens)
    try:
        with pytest.raises(ProviderUnavailable) as captured:
            await adapter.get("/safe-read")
    finally:
        await adapter.aclose()

    assert calls == 1
    assert "internal-provider" not in str(captured.value)
    assert "secret-marker" not in str(captured.value)
