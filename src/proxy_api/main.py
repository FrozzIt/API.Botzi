from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, status
from fastapi.responses import JSONResponse

from proxy_api.infrastructure import (
    close_infrastructure,
    database_is_ready,
    redis_is_ready,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await close_infrastructure()


def create_app() -> FastAPI:
    application = FastAPI(
        title="ProxyAPI",
        docs_url=None,
        openapi_url=None,
        redoc_url=None,
        lifespan=lifespan,
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
