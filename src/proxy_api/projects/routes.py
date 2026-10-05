from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse

from proxy_api.auth.routes import AuthServiceDependency, authentication_error
from proxy_api.auth.service import AuthenticatedSession, AuthenticationFailed
from proxy_api.config import get_settings
from proxy_api.infrastructure import get_provider_adapter
from proxy_api.provider import LPTrackerAdapter, ProviderError, ProviderProtocolError

router = APIRouter()

ProviderDependency = Annotated[LPTrackerAdapter, Depends(get_provider_adapter)]


def get_configured_provider_account() -> str:
    return get_settings().lptracker_account


ProviderAccountDependency = Annotated[str, Depends(get_configured_provider_account)]


async def get_authenticated_session(
    auth_service: AuthServiceDependency,
    token: Annotated[str | None, Header(alias="token")] = None,
) -> AuthenticatedSession | JSONResponse:
    if not token:
        return authentication_error()
    try:
        return await auth_service.authenticate(token)
    except AuthenticationFailed:
        return authentication_error()


AuthenticatedSessionDependency = Annotated[
    AuthenticatedSession | JSONResponse,
    Depends(get_authenticated_session),
]


def project_not_found_error() -> JSONResponse:
    return JSONResponse(
        {
            "status": "error",
            "errors": [{"code": 404, "message": "Project not found"}],
        }
    )


def provider_unavailable_error() -> JSONResponse:
    return JSONResponse(
        {
            "status": "error",
            "errors": [{"code": 503, "message": "Service unavailable"}],
        }
    )


async def read_allowed_project(
    access: AuthenticatedSession,
    provider: LPTrackerAdapter,
    provider_account: str,
    project_id: int,
) -> dict[str, object]:
    if project_id != access.project_id:
        raise LookupError("Project is outside the authenticated client scope")
    if access.provider_account != provider_account:
        raise ProviderError("Provider account is unavailable")

    response = await provider.get(f"/project/{project_id}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise ProviderProtocolError("Provider response is invalid")
    received_project_id = result.get("id")
    if type(received_project_id) is not int or received_project_id != project_id:
        raise ProviderProtocolError("Provider response is invalid")
    return result


@router.get("/projects", include_in_schema=False)
async def projects(
    access: AuthenticatedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
) -> JSONResponse:
    if isinstance(access, JSONResponse):
        return access
    try:
        project = await read_allowed_project(
            access,
            provider,
            provider_account,
            access.project_id,
        )
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": [project]})


@router.get("/project/{project_id}", include_in_schema=False)
async def project(
    project_id: int,
    access: AuthenticatedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
) -> JSONResponse:
    if isinstance(access, JSONResponse):
        return access
    try:
        result = await read_allowed_project(access, provider, provider_account, project_id)
    except LookupError:
        return project_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})
