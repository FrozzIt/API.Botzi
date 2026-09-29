from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from proxy_api.database import AuthClient, AuthSession, ConfigState

PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65_536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("synthetic-dummy-password-never-used")


class AuthenticationFailed(ValueError):
    """Neutral authentication failure without account existence details."""


@dataclass(frozen=True)
class AuthenticatedSession:
    session_id: str
    client_key: str


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def hash_password(password: str) -> str:
    if len(password) < 16:
        raise ValueError("Password must contain at least 16 characters")
    return PASSWORD_HASHER.hash(password)


class AuthService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        idle_timeout_seconds: int,
        expected_config_revision: int,
    ) -> None:
        self._session_factory = session_factory
        self._idle_timeout = timedelta(seconds=idle_timeout_seconds)
        self._expected_config_revision = expected_config_revision

    async def login(
        self,
        *,
        login: str,
        password: str,
        service: str,
        version: str,
    ) -> str:
        normalized_login = login.casefold()
        async with self._session_factory() as session, session.begin():
            await self._lock_current_config_revision(session)
            client = await session.scalar(
                select(AuthClient)
                .where(AuthClient.login_normalized == normalized_login)
                .with_for_update()
            )
            candidate_hash = client.password_hash if client is not None else DUMMY_PASSWORD_HASH
            password_valid = self._verify_password(candidate_hash, password)
            if client is None or not client.enabled or not password_valid:
                raise AuthenticationFailed("Authentication failed")

            token = secrets.token_urlsafe(32)
            session.add(
                AuthSession(
                    client_key=client.client_key,
                    token_hash=hash_token(token),
                    service=service,
                    version=version,
                )
            )
        return token

    async def authenticate(self, token: str) -> AuthenticatedSession:
        now = datetime.now(UTC)
        token_digest = hash_token(token)
        async with self._session_factory() as session, session.begin():
            await self._lock_current_config_revision(session)
            row = (
                await session.execute(
                    select(AuthSession, AuthClient)
                    .join(AuthClient, AuthClient.client_key == AuthSession.client_key)
                    .where(AuthSession.token_hash == token_digest)
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                raise AuthenticationFailed("Authentication failed")

            auth_session, client = row
            expired = auth_session.last_seen_at < now - self._idle_timeout
            if auth_session.revoked_at is not None or expired or not client.enabled:
                if auth_session.revoked_at is None:
                    auth_session.revoked_at = now
                raise AuthenticationFailed("Authentication failed")

            auth_session.last_seen_at = now
            return AuthenticatedSession(
                session_id=str(auth_session.id),
                client_key=client.client_key,
            )

    async def logout(self, token: str) -> None:
        now = datetime.now(UTC)
        token_digest = hash_token(token)
        async with self._session_factory() as session, session.begin():
            await self._lock_current_config_revision(session)
            row = (
                await session.execute(
                    select(AuthSession, AuthClient)
                    .join(AuthClient, AuthClient.client_key == AuthSession.client_key)
                    .where(AuthSession.token_hash == token_digest)
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                raise AuthenticationFailed("Authentication failed")
            auth_session, client = row
            expired = auth_session.last_seen_at < now - self._idle_timeout
            if auth_session.revoked_at is not None or expired or not client.enabled:
                if auth_session.revoked_at is None:
                    auth_session.revoked_at = now
                raise AuthenticationFailed("Authentication failed")
            auth_session.revoked_at = now

    async def _lock_current_config_revision(self, session: AsyncSession) -> None:
        state = await session.scalar(
            select(ConfigState).where(ConfigState.id == 1).with_for_update(read=True)
        )
        if state is None or state.revision != self._expected_config_revision:
            raise AuthenticationFailed("Authentication failed")

    @staticmethod
    def _verify_password(password_hash: str, password: str) -> bool:
        try:
            return PASSWORD_HASHER.verify(password_hash, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return False
