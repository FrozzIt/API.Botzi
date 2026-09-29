from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken
from redis.asyncio import Redis
from redis.exceptions import RedisError

from proxy_api.provider.errors import ProviderDeadlineExceeded, ProviderUnavailable

RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

STORE_TOKEN_SCRIPT = """
local current = redis.call('HGET', KEYS[1], 'generation')
if current then
  if ARGV[1] == '' or current ~= ARGV[1] then
    return 0
  end
end

redis.call('HSET', KEYS[1], 'token', ARGV[2], 'generation', ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[4])
return 1
"""

LoginCallback = Callable[[float], Awaitable[str]]


@dataclass(frozen=True)
class ProviderToken:
    value: str = field(repr=False)
    generation: str


class DistributedTokenManager:
    def __init__(
        self,
        redis_client: Redis,
        *,
        account: str,
        encryption_key: str,
        token_ttl_seconds: int,
        refresh_lock_seconds: float,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        if token_ttl_seconds <= 0 or refresh_lock_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("Token manager timeouts must be positive")
        account_key = account.encode("utf-8").hex()
        self._redis = redis_client
        try:
            self._cipher = Fernet(encryption_key.encode("ascii"))
        except (UnicodeEncodeError, ValueError):
            raise ValueError("Provider token encryption key is invalid") from None
        self._token_key = f"proxyapi:provider:token:{account_key}"
        self._lock_key = f"proxyapi:provider:refresh-lock:{account_key}"
        self._token_ttl_seconds = token_ttl_seconds
        self._refresh_lock_milliseconds = max(1, round(refresh_lock_seconds * 1000))
        self._poll_interval_seconds = poll_interval_seconds

    async def current_or_refresh(
        self,
        login: LoginCallback,
        deadline: float,
    ) -> ProviderToken:
        current = await self._read_token()
        if current is not None:
            return current
        return await self._refresh(expected_generation=None, login=login, deadline=deadline)

    async def refresh_after_unauthorized(
        self,
        stale_generation: str,
        login: LoginCallback,
        deadline: float,
    ) -> ProviderToken:
        return await self._refresh(
            expected_generation=stale_generation,
            login=login,
            deadline=deadline,
        )

    async def _refresh(
        self,
        *,
        expected_generation: str | None,
        login: LoginCallback,
        deadline: float,
    ) -> ProviderToken:
        loop = asyncio.get_running_loop()
        owner = secrets.token_hex(16)
        while True:
            current = await self._read_token()
            if current is not None and (
                expected_generation is None or current.generation != expected_generation
            ):
                return current
            if loop.time() >= deadline:
                raise ProviderDeadlineExceeded("Provider deadline exceeded")

            if await self._try_lock(owner):
                try:
                    current = await self._read_token()
                    if current is not None and (
                        expected_generation is None or current.generation != expected_generation
                    ):
                        return current

                    token = await login(deadline)
                    generation = secrets.token_hex(16)
                    stored = await self._store_token(
                        expected_generation=expected_generation,
                        token=token,
                        generation=generation,
                    )
                    if stored:
                        return ProviderToken(value=token, generation=generation)
                    current = await self._read_token()
                    if current is not None:
                        return current
                    raise ProviderUnavailable("Provider unavailable")
                finally:
                    remaining = deadline - loop.time()
                    if remaining > 0:
                        try:
                            async with asyncio.timeout(remaining):
                                await self._release_lock(owner)
                        except TimeoutError:
                            pass

            remaining = deadline - loop.time()
            if remaining <= self._poll_interval_seconds:
                raise ProviderDeadlineExceeded("Provider deadline exceeded")
            await asyncio.sleep(self._poll_interval_seconds)

    async def _read_token(self) -> ProviderToken | None:
        try:
            stored = await self._redis.hgetall(self._token_key)
        except RedisError:
            raise ProviderUnavailable("Provider unavailable") from None
        encrypted_token = stored.get("token")
        generation = stored.get("generation")
        if not encrypted_token or not generation:
            return None
        try:
            token = self._cipher.decrypt(encrypted_token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError):
            raise ProviderUnavailable("Provider unavailable") from None
        return ProviderToken(value=token, generation=generation)

    async def _try_lock(self, owner: str) -> bool:
        try:
            return bool(
                await self._redis.set(
                    self._lock_key,
                    owner,
                    nx=True,
                    px=self._refresh_lock_milliseconds,
                )
            )
        except RedisError:
            raise ProviderUnavailable("Provider unavailable") from None

    async def _store_token(
        self,
        *,
        expected_generation: str | None,
        token: str,
        generation: str,
    ) -> bool:
        encrypted_token = self._cipher.encrypt(token.encode("utf-8")).decode("ascii")
        try:
            return bool(
                await self._redis.eval(
                    STORE_TOKEN_SCRIPT,
                    1,
                    self._token_key,
                    expected_generation or "",
                    encrypted_token,
                    generation,
                    self._token_ttl_seconds,
                )
            )
        except RedisError:
            raise ProviderUnavailable("Provider unavailable") from None

    async def _release_lock(self, owner: str) -> None:
        try:
            await self._redis.eval(RELEASE_LOCK_SCRIPT, 1, self._lock_key, owner)
        except RedisError:
            raise ProviderUnavailable("Provider unavailable") from None
