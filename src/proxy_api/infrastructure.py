from __future__ import annotations

import httpx
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from proxy_api.config import get_settings
from proxy_api.provider import DistributedQuotaLimiter, DistributedTokenManager, LPTrackerAdapter

_database_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_redis_client: Redis | None = None
_provider_adapter: LPTrackerAdapter | None = None


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


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_database_engine(),
            expire_on_commit=False,
        )
    return _session_factory


def get_provider_adapter() -> LPTrackerAdapter:
    global _provider_adapter
    if _provider_adapter is None:
        settings = get_settings()
        credentials = (
            settings.lptracker_login,
            settings.lptracker_password,
            settings.lptracker_service,
            settings.provider_token_encryption_key,
        )
        if any(value is None for value in credentials):
            raise RuntimeError("Provider credentials are not configured")
        login, password, service, encryption_key = credentials
        assert login is not None
        assert password is not None
        assert service is not None
        assert encryption_key is not None
        try:
            base_url = httpx.URL(settings.lptracker_base_url)
        except (TypeError, ValueError):
            raise RuntimeError("Provider endpoint is not valid") from None
        if (
            base_url.scheme != "https"
            or not base_url.host
            or base_url.userinfo
            or base_url.query
            or base_url.fragment
            or base_url.path not in ("", "/")
        ):
            raise RuntimeError("Provider endpoint is not valid")
        redis_client = get_redis_client()
        _provider_adapter = LPTrackerAdapter(
            httpx.AsyncClient(
                base_url=base_url,
                headers={"Accept": "application/json"},
                follow_redirects=False,
                timeout=None,
                trust_env=False,
            ),
            DistributedQuotaLimiter(
                redis_client,
                limit=settings.provider_quota_limit,
            ),
            DistributedTokenManager(
                redis_client,
                account=settings.lptracker_account,
                encryption_key=encryption_key.get_secret_value(),
                token_ttl_seconds=settings.provider_token_ttl_seconds,
                refresh_lock_seconds=settings.provider_deadline_seconds + 1,
            ),
            login=login.get_secret_value(),
            password=password.get_secret_value(),
            service=service.get_secret_value(),
            version=settings.lptracker_version,
            default_deadline_seconds=settings.provider_deadline_seconds,
            http_timeout_seconds=settings.provider_http_timeout_seconds,
        )
    return _provider_adapter


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
    global _database_engine, _provider_adapter, _redis_client, _session_factory
    if _provider_adapter is not None:
        await _provider_adapter.aclose()
        _provider_adapter = None
    if _database_engine is not None:
        await _database_engine.dispose()
        _database_engine = None
        _session_factory = None
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
