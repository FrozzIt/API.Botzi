from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from proxy_api.auth.service import AuthenticationFailed, AuthService
from proxy_api.config import get_settings
from proxy_api.infrastructure import get_session_factory

router = APIRouter()


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    login: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=512)
    service: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)


def get_auth_service() -> AuthService:
    return AuthService(
        get_session_factory(),
        get_settings().session_idle_timeout_seconds,
    )


AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]


def authentication_error() -> JSONResponse:
    return JSONResponse(
        {
            "status": "error",
            "errors": [{"code": 401, "message": "Authentication failed"}],
        }
    )


@router.post("/login", include_in_schema=False)
async def login(
    payload: LoginRequest,
    auth_service: AuthServiceDependency,
) -> JSONResponse:
    try:
        token = await auth_service.login(
            login=payload.login,
            password=payload.password.get_secret_value(),
            service=payload.service,
            version=payload.version,
        )
    except AuthenticationFailed:
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
