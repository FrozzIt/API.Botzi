from __future__ import annotations

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from proxy_api.config import get_settings

_database_engine: AsyncEngine | None = None
_redis_client: Redis | None = None


def get_database_engine() -> AsyncEngine:
    global _database_engine
    if _database_engine is None:
        _database_engine = create_async_engine(
            get_settings().database_url,
            pool_pre_ping=True,
        )
    return _database_engine


def get_redis_client() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(
            get_settings().redis_url,
            decode_responses=True,
        )
    return _redis_client


async def database_is_ready() -> bool:
    try:
        async with get_database_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


async def redis_is_ready() -> bool:
    try:
        return bool(await get_redis_client().ping())
    except Exception:
        return False


async def close_infrastructure() -> None:
    global _database_engine, _redis_client
    if _database_engine is not None:
        await _database_engine.dispose()
        _database_engine = None
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
