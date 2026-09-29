from __future__ import annotations

import hashlib
import secrets

from redis.asyncio import Redis
from redis.exceptions import RedisError

LOGIN_ATTEMPT_SCRIPT = """
local now = redis.call('TIME')
local now_ms = (now[1] * 1000) + math.floor(now[2] / 1000)
local cutoff = now_ms - tonumber(ARGV[1])
local limit = tonumber(ARGV[2])

for _, key in ipairs(KEYS) do
  redis.call('ZREMRANGEBYSCORE', key, 0, cutoff)
  if redis.call('ZCARD', key) >= limit then
    return 0
  end
end

for _, key in ipairs(KEYS) do
  redis.call('ZADD', key, now_ms, ARGV[3])
  redis.call('PEXPIRE', key, ARGV[1])
end
return 1
"""


class LoginAttemptRejected(ValueError):
    """Raised when a login attempt must fail closed before password hashing."""


class LoginAttemptLimiter:
    def __init__(
        self,
        redis_client: Redis,
        *,
        attempt_limit: int,
        window_seconds: int,
    ) -> None:
        self._redis = redis_client
        self._attempt_limit = attempt_limit
        self._window_milliseconds = window_seconds * 1000

    async def check(self, *, source: str, login: str) -> None:
        member = secrets.token_hex(16)
        try:
            allowed = await self._redis.eval(
                LOGIN_ATTEMPT_SCRIPT,
                2,
                self.source_key(source),
                self.identity_key(login),
                self._window_milliseconds,
                self._attempt_limit,
                member,
            )
        except RedisError as exc:
            raise LoginAttemptRejected("Login attempt cannot be accepted") from exc
        if allowed != 1:
            raise LoginAttemptRejected("Login attempt cannot be accepted")

    @staticmethod
    def source_key(source: str) -> str:
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        return f"proxyapi:login:source:{digest}"

    @staticmethod
    def identity_key(login: str) -> str:
        digest = hashlib.sha256(login.casefold().encode("utf-8")).hexdigest()
        return f"proxyapi:login:identity:{digest}"
