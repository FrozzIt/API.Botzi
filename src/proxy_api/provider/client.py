from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import httpx

from proxy_api.provider.errors import (
    ProviderAuthenticationError,
    ProviderDeadlineExceeded,
    ProviderObjectNotFound,
    ProviderProtocolError,
    ProviderUnavailable,
)
from proxy_api.provider.quota import DistributedQuotaLimiter
from proxy_api.provider.token import DistributedTokenManager


class LPTrackerAdapter:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        quota_limiter: DistributedQuotaLimiter,
        token_manager: DistributedTokenManager,
        *,
        login: str,
        password: str,
        service: str,
        version: str,
        default_deadline_seconds: float,
        http_timeout_seconds: float,
        max_response_bytes: int = 1_048_576,
    ) -> None:
        if default_deadline_seconds <= 0 or http_timeout_seconds <= 0:
            raise ValueError("Provider timeouts must be positive")
        if max_response_bytes <= 0:
            raise ValueError("Provider response limit must be positive")
        self._http = http_client
        self._quota = quota_limiter
        self._tokens = token_manager
        self._login_value = login
        self._password = password
        self._service = service
        self._version = version
        self._default_deadline_seconds = default_deadline_seconds
        self._http_timeout_seconds = http_timeout_seconds
        self._max_response_bytes = max_response_bytes

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, Any] | list[Any]:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Provider path must be relative to the configured host")
        budget = self._default_deadline_seconds if deadline_seconds is None else deadline_seconds
        if budget <= 0 or budget > self._default_deadline_seconds:
            raise ValueError("Provider deadline is outside the configured bound")
        deadline = asyncio.get_running_loop().time() + budget
        try:
            async with asyncio.timeout_at(deadline):
                token = await self._tokens.current_or_refresh(self._login, deadline)
                response = await self._request(
                    "GET",
                    path,
                    deadline=deadline,
                    token=token.value,
                    params=params,
                )
                if isinstance(response, dict) and self._is_unauthorized(response):
                    token = await self._tokens.refresh_after_unauthorized(
                        token.generation,
                        self._login,
                        deadline,
                    )
                    response = await self._request(
                        "GET",
                        path,
                        deadline=deadline,
                        token=token.value,
                        params=params,
                    )
                if isinstance(response, dict) and self._is_unauthorized(response):
                    raise ProviderAuthenticationError("Provider authentication failed")
                if isinstance(response, list):
                    return response
                if response.get("status") != "success":
                    if self._is_not_found(response):
                        raise ProviderObjectNotFound("Provider object not found")
                    raise ProviderUnavailable("Provider unavailable")
                return response
        except TimeoutError:
            raise ProviderDeadlineExceeded("Provider deadline exceeded") from None

    async def _login(self, deadline: float) -> str:
        response = await self._request(
            "POST",
            "/login",
            deadline=deadline,
            json_body={
                "login": self._login_value,
                "password": self._password,
                "service": self._service,
                "version": self._version,
            },
        )
        if not isinstance(response, dict):
            raise ProviderProtocolError("Provider response is invalid")
        if response.get("status") != "success":
            raise ProviderAuthenticationError("Provider authentication failed")
        result = response.get("result")
        if not isinstance(result, dict):
            raise ProviderProtocolError("Provider response is invalid")
        token = result.get("token")
        if not isinstance(token, str) or not token:
            raise ProviderProtocolError("Provider response is invalid")
        return token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        deadline: float,
        token: str | None = None,
        params: Mapping[str, str | int] | None = None,
        json_body: Mapping[str, str] | None = None,
    ) -> dict[str, Any] | list[Any]:
        await self._quota.acquire(deadline)
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise ProviderDeadlineExceeded("Provider deadline exceeded")
        request = self._http.build_request(
            method,
            path,
            params=params,
            json=json_body,
            headers={"token": token} if token is not None else None,
        )
        response: httpx.Response | None = None
        try:
            async with asyncio.timeout(min(remaining, self._http_timeout_seconds)):
                response = await self._http.send(request, stream=True, follow_redirects=False)
                if response.is_redirect or not 200 <= response.status_code < 300:
                    raise ProviderUnavailable("Provider unavailable")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self._max_response_bytes:
                        raise ProviderProtocolError("Provider response is invalid")
        except TimeoutError:
            raise ProviderDeadlineExceeded("Provider deadline exceeded") from None
        except httpx.HTTPError:
            raise ProviderUnavailable("Provider unavailable") from None
        finally:
            if response is not None:
                await response.aclose()

        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderProtocolError("Provider response is invalid") from None
        if not isinstance(payload, dict | list):
            raise ProviderProtocolError("Provider response is invalid")
        return payload

    @staticmethod
    def _is_unauthorized(response: Mapping[str, Any]) -> bool:
        if response.get("status") != "error":
            return False
        errors = response.get("errors")
        if not isinstance(errors, list):
            return False
        return any(isinstance(error, dict) and error.get("code") == 401 for error in errors)

    @staticmethod
    def _is_not_found(response: Mapping[str, Any]) -> bool:
        if response.get("status") != "error":
            return False
        errors = response.get("errors")
        if not isinstance(errors, list):
            return False
        return any(isinstance(error, dict) and error.get("code") in {400, 404} for error in errors)
