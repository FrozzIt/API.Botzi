from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from proxy_api.auth.rate_limit import LoginAttemptLimiter, LoginAttemptRejected
from proxy_api.auth.service import AuthenticationFailed, AuthService
from proxy_api.config import get_settings
from proxy_api.infrastructure import get_redis_client, get_session_factory

router = APIRouter()


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    login: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=512)
    service: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)


def get_auth_service(request: Request) -> AuthService:
    return AuthService(
        get_session_factory(),
        get_settings().session_idle_timeout_seconds,
        getattr(request.app.state, "config_revision", -1),
    )


AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]


def get_login_attempt_limiter() -> LoginAttemptLimiter:
    settings = get_settings()
    return LoginAttemptLimiter(
        get_redis_client(),
        attempt_limit=settings.login_attempt_limit,
        window_seconds=settings.login_attempt_window_seconds,
    )


LoginAttemptLimiterDependency = Annotated[
    LoginAttemptLimiter,
    Depends(get_login_attempt_limiter),
]


def authentication_error() -> JSONResponse:
    return JSONResponse(
        {
            "status": "error",
            "errors": [{"code": 401, "message": "Authentication failed"}],
        }
    )


@router.post("/login", include_in_schema=False)
async def login(
    request: Request,
    payload: LoginRequest,
    auth_service: AuthServiceDependency,
    attempt_limiter: LoginAttemptLimiterDependency,
) -> JSONResponse:
    try:
        source = request.client.host if request.client is not None else "unknown"
        await attempt_limiter.check(source=source, login=payload.login)
        token = await auth_service.login(
            login=payload.login,
            password=payload.password.get_secret_value(),
            service=payload.service,
            version=payload.version,
        )
    except (AuthenticationFailed, LoginAttemptRejected):
        return authentication_error()
    return JSONResponse({"status": "success", "result": {"token": token}})


@router.post("/logout", include_in_schema=False)
async def logout(
    auth_service: AuthServiceDependency,
    token: Annotated[str | None, Header(alias="token")] = None,
) -> JSONResponse:
    if not token:
        return authentication_error()
    try:
        await auth_service.logout(token)
    except AuthenticationFailed:
        return authentication_error()
    return JSONResponse({"status": "success", "result": {}})
