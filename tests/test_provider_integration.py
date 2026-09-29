from __future__ import annotations

import asyncio
import multiprocessing
import os
import time
import uuid
from multiprocessing.sharedctypes import Synchronized
from multiprocessing.synchronize import Event as EventType

import pytest
from cryptography.fernet import Fernet
from redis.asyncio import Redis

from proxy_api.provider.quota import DistributedQuotaLimiter
from proxy_api.provider.token import DistributedTokenManager

pytestmark = pytest.mark.integration


def require_redis() -> str:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("RUN_INTEGRATION_TESTS=1 is required")
    value = os.getenv("REDIS_URL")
    if value is None:
        raise RuntimeError("REDIS_URL is required for integration tests")
    return value


def quota_worker(
    redis_url: str,
    key: str,
    start: EventType,
    results: multiprocessing.Queue[float],
) -> None:
    async def run() -> None:
        redis_client = Redis.from_url(redis_url, decode_responses=True)
        limiter = DistributedQuotaLimiter(redis_client, key=key)
        try:
            for _ in range(3):
                await limiter.acquire(asyncio.get_running_loop().time() + 5)
                results.put(time.time())
        finally:
            await redis_client.aclose()

    start.wait()
    asyncio.run(run())


def refresh_worker(
    redis_url: str,
    account: str,
    encryption_key: str,
    start: EventType,
    login_count: Synchronized,
    results: multiprocessing.Queue[str],
) -> None:
    async def run() -> None:
        redis_client = Redis.from_url(redis_url, decode_responses=True)
        manager = DistributedTokenManager(
            redis_client,
            account=account,
            encryption_key=encryption_key,
            token_ttl_seconds=60,
            refresh_lock_seconds=2,
            poll_interval_seconds=0.01,
        )

        async def login(_: float) -> str:
            with login_count.get_lock():
                login_count.value += 1
            await asyncio.sleep(0.2)
            return "synthetic-internal-token"

        try:
            token = await manager.refresh_after_unauthorized(
                "stale-generation",
                login,
                asyncio.get_running_loop().time() + 5,
            )
            results.put(token.generation)
        finally:
            await redis_client.aclose()

    start.wait()
    asyncio.run(run())


def join_processes(processes: list[multiprocessing.Process]) -> None:
    try:
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)


def test_two_processes_share_sliding_provider_quota() -> None:
    redis_url = require_redis()
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    key = f"proxyapi:test:provider-quota:{uuid.uuid4().hex}"
    processes = [
        context.Process(target=quota_worker, args=(redis_url, key, start, results))
        for _ in range(2)
    ]

    for process in processes:
        process.start()
    start.set()
    join_processes(processes)
    timestamps = sorted(results.get(timeout=1) for _ in range(6))

    for timestamp in timestamps:
        assert sum(timestamp <= candidate < timestamp + 1 for candidate in timestamps) <= 3

    async def cleanup() -> None:
        redis_client = Redis.from_url(redis_url)
        try:
            await redis_client.delete(key)
        finally:
            await redis_client.aclose()

    asyncio.run(cleanup())


def test_two_processes_coordinate_one_refresh() -> None:
    redis_url = require_redis()
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    login_count = context.Value("i", 0)
    results = context.Queue()
    account = f"test-{uuid.uuid4().hex}"
    encryption_key = Fernet.generate_key().decode("ascii")
    account_key = account.encode("utf-8").hex()
    token_key = f"proxyapi:provider:token:{account_key}"
    lock_key = f"proxyapi:provider:refresh-lock:{account_key}"

    async def prepare() -> None:
        redis_client = Redis.from_url(redis_url, decode_responses=True)
        try:
            await redis_client.hset(
                token_key,
                mapping={
                    "token": Fernet(encryption_key.encode("ascii"))
                    .encrypt(b"stale-synthetic-token")
                    .decode("ascii"),
                    "generation": "stale-generation",
                },
            )
            await redis_client.expire(token_key, 60)
        finally:
            await redis_client.aclose()

    asyncio.run(prepare())
    processes = [
        context.Process(
            target=refresh_worker,
            args=(redis_url, account, encryption_key, start, login_count, results),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    join_processes(processes)

    generations = [results.get(timeout=1) for _ in range(2)]
    assert login_count.value == 1
    assert generations[0] == generations[1]

    async def assert_token_is_encrypted() -> None:
        redis_client = Redis.from_url(redis_url, decode_responses=True)
        try:
            stored = await redis_client.hget(token_key, "token")
            assert stored is not None
            assert "synthetic-internal-token" not in stored
        finally:
            await redis_client.aclose()

    asyncio.run(assert_token_is_encrypted())

    async def cleanup() -> None:
        redis_client = Redis.from_url(redis_url)
        try:
            await redis_client.delete(token_key, lock_key)
        finally:
            await redis_client.aclose()

    asyncio.run(cleanup())


@pytest.mark.asyncio
async def test_refresh_recovers_when_stale_token_record_has_expired() -> None:
    redis_url = require_redis()
    account = f"test-{uuid.uuid4().hex}"
    account_key = account.encode("utf-8").hex()
    token_key = f"proxyapi:provider:token:{account_key}"
    lock_key = f"proxyapi:provider:refresh-lock:{account_key}"
    redis_client = Redis.from_url(redis_url, decode_responses=True)
    encryption_key = Fernet.generate_key().decode("ascii")
    manager = DistributedTokenManager(
        redis_client,
        account=account,
        encryption_key=encryption_key,
        token_ttl_seconds=60,
        refresh_lock_seconds=2,
    )
    login_calls = 0

    async def login(_: float) -> str:
        nonlocal login_calls
        login_calls += 1
        return "synthetic-refreshed-token"

    try:
        token = await manager.refresh_after_unauthorized(
            "expired-generation",
            login,
            asyncio.get_running_loop().time() + 1,
        )
        assert token.value == "synthetic-refreshed-token"
        assert login_calls == 1
    finally:
        await redis_client.delete(token_key, lock_key)
        await redis_client.aclose()
