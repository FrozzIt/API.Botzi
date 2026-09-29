from __future__ import annotations

import asyncio
import secrets

from redis.asyncio import Redis
from redis.exceptions import RedisError

from proxy_api.provider.errors import ProviderDeadlineExceeded, ProviderUnavailable

QUOTA_SCRIPT = """
local now = redis.call('TIME')
local now_ms = (now[1] * 1000) + math.floor(now[2] / 1000)
local window_ms = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local cutoff = now_ms - window_ms

redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, cutoff)
if redis.call('ZCARD', KEYS[1]) < limit then
  redis.call('ZADD', KEYS[1], now_ms, ARGV[3])
  redis.call('PEXPIRE', KEYS[1], window_ms)
  return {1, 0}
end

local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
local wait_ms = math.max(1, tonumber(oldest[2]) + window_ms - now_ms)
return {0, wait_ms}
"""


class DistributedQuotaLimiter:
    def __init__(
        self,
        redis_client: Redis,
        *,
        limit: int = 3,
        window_seconds: float = 1.0,
        key: str = "proxyapi:provider:quota",
    ) -> None:
        if limit < 1 or limit > 3:
            raise ValueError("Provider quota limit must be between 1 and 3")
        if window_seconds <= 0:
            raise ValueError("Provider quota window must be positive")
        self._redis = redis_client
        self._limit = limit
        self._window_milliseconds = max(1, round(window_seconds * 1000))
        self._key = key

    async def acquire(self, deadline: float) -> None:
        loop = asyncio.get_running_loop()
        while True:
            if loop.time() >= deadline:
                raise ProviderDeadlineExceeded("Provider deadline exceeded")
            try:
                allowed, wait_milliseconds = await self._redis.eval(
                    QUOTA_SCRIPT,
                    1,
                    self._key,
                    self._window_milliseconds,
                    self._limit,
                    secrets.token_hex(16),
                )
            except RedisError:
                raise ProviderUnavailable("Provider unavailable") from None
            if allowed == 1:
                return

            remaining = deadline - loop.time()
            wait_seconds = int(wait_milliseconds) / 1000
            if wait_seconds >= remaining:
                raise ProviderDeadlineExceeded("Provider deadline exceeded")
            await asyncio.sleep(wait_seconds)
