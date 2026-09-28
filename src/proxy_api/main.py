from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from proxy_api.auth.routes import router as auth_router
from proxy_api.config import get_settings
from proxy_api.configuration.service import ConfigService
from proxy_api.infrastructure import (
    close_infrastructure,
    database_is_ready,
    get_session_factory,
    redis_is_ready,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    try:
        settings = get_settings()
        await ConfigService(get_session_factory()).apply_file(
            settings.client_config_path,
            settings.allowed_provider_accounts,
        )
        yield
    finally:
        await close_infrastructure()


def create_app() -> FastAPI:
    application = FastAPI(
        title="ProxyAPI",
        docs_url=None,
        openapi_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.include_router(auth_router)

    @application.exception_handler(RequestValidationError)
    async def request_validation_error(
        _: Request,
        __: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            {
                "status": "error",
                "errors": [{"code": 422, "message": "Request is not valid"}],
            }
        )

    @application.get("/health", include_in_schema=False)
    async def healthcheck(
        database_ready: bool = Depends(database_is_ready),
        redis_ready: bool = Depends(redis_is_ready),
    ) -> JSONResponse:
        if database_ready and redis_ready:
            return JSONResponse({"status": "ok"})
        return JSONResponse(
            {"status": "unavailable"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return application


app = create_app()
