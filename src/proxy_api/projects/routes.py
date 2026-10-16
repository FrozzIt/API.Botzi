from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from proxy_api.auth.routes import AuthServiceDependency
from proxy_api.auth.service import AuthenticatedSession, AuthenticationFailed, AuthService
from proxy_api.config import get_settings
from proxy_api.infrastructure import get_provider_adapter
from proxy_api.provider import LPTrackerAdapter, ProviderError, ProviderProtocolError

router = APIRouter()

RawProviderDependency = Annotated[LPTrackerAdapter, Depends(get_provider_adapter)]


class ClientAuthenticationError(Exception):
    """Stops request dependency resolution after a neutral client auth failure."""


def get_configured_provider_account() -> str:
    return get_settings().lptracker_account


ProviderAccountDependency = Annotated[str, Depends(get_configured_provider_account)]


async def get_authenticated_session(
    auth_service: AuthServiceDependency,
    token: Annotated[str | None, Header(alias="token")] = None,
) -> AuthenticatedSession:
    if not token:
        raise ClientAuthenticationError
    try:
        return await auth_service.authenticate(token)
    except (AuthenticationFailed, SQLAlchemyError):
        raise ClientAuthenticationError from None


AuthenticatedSessionDependency = Annotated[
    AuthenticatedSession,
    Depends(get_authenticated_session),
]


class AuthorizedProvider:
    def __init__(
        self,
        provider: LPTrackerAdapter,
        auth_service: AuthService,
        access: AuthenticatedSession,
    ) -> None:
        self._provider = provider
        self._auth_service = auth_service
        self._access = access

    @asynccontextmanager
    async def _send_guard(self) -> AsyncIterator[None]:
        try:
            async with self._auth_service.provider_request_guard(self._access):
                yield
        except (AuthenticationFailed, SQLAlchemyError):
            raise ClientAuthenticationError from None

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | Sequence[tuple[str, str | int]] | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, Any] | list[Any]:
        return await self._provider.get(
            path,
            params=params,
            deadline_seconds=deadline_seconds,
            send_guard=self._send_guard,
        )

    async def post_once(
        self,
        path: str,
        *,
        json_body: Mapping[str, object],
        before_send: Callable[[], Awaitable[None]],
        deadline_seconds: float | None = None,
    ) -> dict[str, Any] | list[Any]:
        return await self._provider.post_once(
            path,
            json_body=json_body,
            before_send=before_send,
            deadline_seconds=deadline_seconds,
            send_guard=self._send_guard,
        )

    async def recheck_access(self) -> None:
        async with self._send_guard():
            pass


def get_authorized_provider(
    access: AuthenticatedSessionDependency,
    auth_service: AuthServiceDependency,
    provider: RawProviderDependency,
) -> AuthorizedProvider:
    return AuthorizedProvider(provider, auth_service, access)


ProviderDependency = Annotated[AuthorizedProvider, Depends(get_authorized_provider)]


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


def _validate_project_query(
    request: Request,
    expected_project_id: int | None,
) -> None:
    query_projects = request.query_params.getlist("project_id")
    if not query_projects:
        return
    if expected_project_id is None or len(query_projects) != 1:
        raise LookupError("Project query is outside the authenticated client scope")
    raw_project_id = query_projects[0]
    try:
        project_id = int(raw_project_id)
    except ValueError:
        raise LookupError("Project query is invalid") from None
    if str(project_id) != raw_project_id or project_id != expected_project_id:
        raise LookupError("Project query is outside the authenticated client scope")


def _validate_project_links(value: object, expected_project_id: int) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"project_id", "last_project_id"} and nested is not None:
                if type(nested) is not int or nested != expected_project_id:
                    raise ProviderProtocolError("Provider response is invalid")
            _validate_project_links(nested, expected_project_id)
    elif isinstance(value, list):
        for nested in value:
            _validate_project_links(nested, expected_project_id)


async def read_allowed_project(
    access: AuthenticatedSession,
    provider: AuthorizedProvider,
    provider_account: str,
    project_id: int,
) -> dict[str, object]:
    if project_id != access.project_id:
        raise LookupError("Project is outside the authenticated client scope")
    if access.provider_account != provider_account:
        raise ProviderError("Provider account is unavailable")

    response = await provider.get(f"/project/{project_id}")
    if not isinstance(response, dict) or response.get("status") != "success":
        raise ProviderProtocolError("Provider response is invalid")
    _validate_project_links(response, project_id)
    result = response.get("result")
    if not isinstance(result, dict):
        raise ProviderProtocolError("Provider response is invalid")
    received_project_id = result.get("id")
    if type(received_project_id) is not int or received_project_id != project_id:
        raise ProviderProtocolError("Provider response is invalid")
    public_project: dict[str, object] = {"id": received_project_id}
    for field in ("name", "page", "domain"):
        value = result.get(field)
        if not isinstance(value, str):
            raise ProviderProtocolError("Provider response is invalid")
        public_project[field] = value
    return public_project


@router.get("/projects", include_in_schema=False)
async def projects(
    request: Request,
    access: AuthenticatedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
) -> JSONResponse:
    try:
        _validate_project_query(request, None)
        project = await read_allowed_project(
            access,
            provider,
            provider_account,
            access.project_id,
        )
    except LookupError:
        return project_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": [project]})


@router.get("/project/{project_id}", include_in_schema=False)
async def project(
    request: Request,
    project_id: int,
    access: AuthenticatedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
) -> JSONResponse:
    try:
        _validate_project_query(request, project_id)
        result = await read_allowed_project(access, provider, provider_account, project_id)
    except LookupError:
        return project_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})
