from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from proxy_api.auth.rate_limit import LoginAttemptLimiter, LoginAttemptRejected
from proxy_api.auth.routes import get_auth_service, get_login_attempt_limiter
from proxy_api.main import create_app


@asynccontextmanager
async def no_op_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


class RejectingLimiter:
    async def check(self, *, source: str, login: str) -> None:
        raise LoginAttemptRejected("Login attempt cannot be accepted")


class UnavailableRedis:
    async def eval(self, *_: object) -> int:
        raise RedisError("synthetic unavailable storage")


class UnexpectedAuthService:
    def __init__(self) -> None:
        self.login_calls = 0

    async def login(self, **_: str) -> str:
        self.login_calls += 1
        raise AssertionError("Password verification must not run after rate-limit rejection")


def test_rate_limit_rejection_happens_before_password_verification() -> None:
    application = create_app()
    application.router.lifespan_context = no_op_lifespan
    auth_service = UnexpectedAuthService()
    application.dependency_overrides[get_auth_service] = lambda: auth_service
    application.dependency_overrides[get_login_attempt_limiter] = RejectingLimiter

    with TestClient(application) as client:
        responses = [
            client.post(
                "/login",
                json={
                    "login": login,
                    "password": "synthetic-password-never-verified",
                    "service": "synthetic-service",
                    "version": "1",
                },
            )
            for login in ("known-login", "unknown-login")
        ]

    assert auth_service.login_calls == 0
    assert (
        responses[0].json()
        == responses[1].json()
        == {
            "status": "error",
            "errors": [{"code": 401, "message": "Authentication failed"}],
        }
    )


def test_limiter_fails_closed_when_redis_is_unavailable() -> None:
    limiter = LoginAttemptLimiter(
        UnavailableRedis(),  # type: ignore[arg-type]
        attempt_limit=5,
        window_seconds=60,
    )
    application = create_app()
    application.router.lifespan_context = no_op_lifespan
    auth_service = UnexpectedAuthService()
    application.dependency_overrides[get_auth_service] = lambda: auth_service
    application.dependency_overrides[get_login_attempt_limiter] = lambda: limiter

    with TestClient(application) as client:
        responses = [
            client.post(
                "/login",
                json={
                    "login": login,
                    "password": "synthetic-password-never-verified",
                    "service": "synthetic-service",
                    "version": "1",
                },
            )
            for login in ("known-login", "unknown-login")
        ]

    assert auth_service.login_calls == 0
    assert (
        responses[0].json()
        == responses[1].json()
        == {
            "status": "error",
            "errors": [{"code": 401, "message": "Authentication failed"}],
        }
    )
