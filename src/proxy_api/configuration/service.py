from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from proxy_api.configuration.loader import LoadedClientConfig, load_client_config
from proxy_api.database import AuthClient, AuthSession, ConfigState

CONFIG_APPLY_LOCK_ID = 7_109_120_012


class ConfigRevisionError(ValueError):
    """Raised when a configuration revision cannot replace the active revision."""


@dataclass(frozen=True)
class ApplyResult:
    revision: int
    changed: bool
    revoked_client_keys: frozenset[str]


class ConfigService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def apply_file(
        self,
        path: Path,
        allowed_provider_accounts: frozenset[str],
    ) -> ApplyResult:
        loaded = load_client_config(path, allowed_provider_accounts)
        return await self.apply(loaded)

    async def apply(self, loaded: LoadedClientConfig) -> ApplyResult:
        document = loaded.document
        now = datetime.now(UTC)
        revoked_client_keys: set[str] = set()

        async with self._session_factory() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": CONFIG_APPLY_LOCK_ID},
            )
            state = await session.get(ConfigState, 1, with_for_update=True)
            if state is not None:
                if document.revision < state.revision:
                    raise ConfigRevisionError("Configuration revision must increase")
                if document.revision == state.revision:
                    if loaded.content_digest != state.content_digest:
                        raise ConfigRevisionError("Active revision has different content")
                    return ApplyResult(document.revision, False, frozenset())

            existing_clients = {
                client.client_key: client
                for client in (await session.scalars(select(AuthClient).with_for_update())).all()
            }

            configured_keys = set(document.clients)
            for removed_key in set(existing_clients) - configured_keys:
                removed = existing_clients[removed_key]
                if removed.enabled:
                    revoked_client_keys.add(removed_key)
                removed.enabled = False
                removed.revision = document.revision
                removed.login = f"removed-{removed_key}-{document.revision}"
                removed.login_normalized = removed.login.casefold()

            for client_key, definition in document.clients.items():
                existing = existing_clients.get(client_key)
                if (
                    existing is not None
                    and existing.login_normalized != definition.login.casefold()
                ):
                    existing.login = f"pending-{client_key}-{document.revision}"
                    existing.login_normalized = existing.login.casefold()
            await session.flush()

            for client_key, definition in document.clients.items():
                existing = existing_clients.get(client_key)
                if existing is None:
                    session.add(
                        AuthClient(
                            client_key=client_key,
                            revision=document.revision,
                            enabled=definition.enabled,
                            login=definition.login,
                            login_normalized=definition.login.casefold(),
                            password_hash=definition.password_hash,
                            provider_account=definition.provider_account,
                            project_id=definition.project_id,
                            access_group=definition.access_group,
                            allowed_staff_ids=definition.allowed_staff_ids,
                            main_owner_display_name=definition.main_owner_display_name,
                        )
                    )
                    continue

                security_before = (
                    existing.enabled,
                    existing.login_normalized,
                    existing.password_hash,
                    existing.provider_account,
                    existing.project_id,
                    existing.access_group,
                )
                security_after = (
                    definition.enabled,
                    definition.login.casefold(),
                    definition.password_hash,
                    definition.provider_account,
                    definition.project_id,
                    definition.access_group,
                )
                if security_before != security_after:
                    revoked_client_keys.add(client_key)

                existing.revision = document.revision
                existing.enabled = definition.enabled
                existing.login = definition.login
                existing.login_normalized = definition.login.casefold()
                existing.password_hash = definition.password_hash
                existing.provider_account = definition.provider_account
                existing.project_id = definition.project_id
                existing.access_group = definition.access_group
                existing.allowed_staff_ids = definition.allowed_staff_ids
                existing.main_owner_display_name = definition.main_owner_display_name

            if revoked_client_keys:
                await session.execute(
                    update(AuthSession)
                    .where(
                        AuthSession.client_key.in_(revoked_client_keys),
                        AuthSession.revoked_at.is_(None),
                    )
                    .values(revoked_at=now)
                )

            if state is None:
                session.add(
                    ConfigState(
                        id=1,
                        schema_version=document.schema_version,
                        revision=document.revision,
                        content_digest=loaded.content_digest,
                        applied_at=now,
                    )
                )
            else:
                state.schema_version = document.schema_version
                state.revision = document.revision
                state.content_digest = loaded.content_digest
                state.applied_at = now

        return ApplyResult(
            revision=document.revision,
            changed=True,
            revoked_client_keys=frozenset(revoked_client_keys),
        )
