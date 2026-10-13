from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeAlias

from sqlalchemy import or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from proxy_api.database import ContactDetailOwnership

ProviderIdentifier: TypeAlias = int | str
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class DetailOwnershipConflict(Exception):
    """A trusted observation conflicts with an existing ownership record."""


class DetailOwnershipUnavailable(Exception):
    """The durable ownership store cannot be used safely."""


@dataclass(frozen=True)
class VerifiedContact:
    contact_id: ProviderIdentifier
    project_id: int
    detail_ids: frozenset[ProviderIdentifier]
    details_complete: bool = False


@dataclass(frozen=True)
class DetailOwnership:
    provider_account: str
    detail_id: str
    contact_id: str
    project_id: int
    active: bool
    verified_at: datetime


def normalize_identifier(value: ProviderIdentifier) -> str:
    if type(value) is int:
        normalized = str(value)
    elif isinstance(value, str) and value and value == value.strip():
        normalized = value
    else:
        raise DetailOwnershipConflict("Ownership identifier is invalid")
    if not IDENTIFIER_PATTERN.fullmatch(normalized):
        raise DetailOwnershipConflict("Ownership identifier is invalid")
    return normalized


class DetailOwnershipStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def sync_verified_contacts(
        self,
        provider_account: str,
        contacts: list[VerifiedContact],
    ) -> None:
        if not contacts:
            return
        if not provider_account or len(provider_account) > 64:
            raise DetailOwnershipConflict("Provider account is invalid")

        normalized_contacts: dict[tuple[int, str], tuple[frozenset[str], bool]] = {}
        detail_owners: dict[str, tuple[int, str]] = {}
        for contact in contacts:
            if type(contact.project_id) is not int or contact.project_id <= 0:
                raise DetailOwnershipConflict("Project identifier is invalid")
            contact_id = normalize_identifier(contact.contact_id)
            detail_ids = frozenset(normalize_identifier(item) for item in contact.detail_ids)
            contact_key = (contact.project_id, contact_id)
            previous_contact = normalized_contacts.get(contact_key)
            if previous_contact is not None and previous_contact[0] != detail_ids:
                raise DetailOwnershipConflict("Trusted contact observations conflict")
            normalized_contacts[contact_key] = (
                detail_ids,
                contact.details_complete or (previous_contact is not None and previous_contact[1]),
            )
            for detail_id in detail_ids:
                previous_owner = detail_owners.get(detail_id)
                if previous_owner is not None and previous_owner != contact_key:
                    raise DetailOwnershipConflict("Trusted detail observations conflict")
                detail_owners[detail_id] = contact_key

        try:
            async with self._session_factory() as session, session.begin():
                existing_by_detail: dict[str, ContactDetailOwnership] = {}
                complete_contact_keys = [
                    contact_key
                    for contact_key, (_, details_complete) in normalized_contacts.items()
                    if details_complete
                ]
                ownership_filters = []
                if detail_owners:
                    ownership_filters.append(ContactDetailOwnership.detail_id.in_(detail_owners))
                if complete_contact_keys:
                    ownership_filters.append(
                        tuple_(
                            ContactDetailOwnership.project_id,
                            ContactDetailOwnership.contact_id,
                        ).in_(complete_contact_keys)
                    )
                if ownership_filters:
                    existing_rows = (
                        await session.scalars(
                            select(ContactDetailOwnership)
                            .where(
                                ContactDetailOwnership.provider_account == provider_account,
                                or_(*ownership_filters),
                            )
                            .with_for_update()
                        )
                    ).all()
                    existing_by_detail = {row.detail_id: row for row in existing_rows}

                for detail_id, expected_owner in detail_owners.items():
                    existing = existing_by_detail.get(detail_id)
                    if existing is None:
                        continue
                    if (existing.project_id, existing.contact_id) != expected_owner:
                        raise DetailOwnershipConflict("Detail ownership cannot be rebound")

                for (project_id, contact_id), (
                    detail_ids,
                    details_complete,
                ) in normalized_contacts.items():
                    if not details_complete:
                        continue
                    for existing in existing_by_detail.values():
                        if (
                            existing.project_id == project_id
                            and existing.contact_id == contact_id
                            and existing.detail_id not in detail_ids
                        ):
                            existing.active = False

                for detail_id, (project_id, contact_id) in detail_owners.items():
                    existing = existing_by_detail.get(detail_id)
                    if existing is None:
                        session.add(
                            ContactDetailOwnership(
                                provider_account=provider_account,
                                detail_id=detail_id,
                                contact_id=contact_id,
                                project_id=project_id,
                                active=True,
                            )
                        )
                    else:
                        existing.active = True
                        existing.verified_at = datetime.now(UTC)
        except DetailOwnershipConflict:
            raise
        except IntegrityError:
            raise DetailOwnershipConflict("Concurrent detail ownership conflict") from None
        except SQLAlchemyError:
            raise DetailOwnershipUnavailable("Detail ownership store is unavailable") from None

    async def get(
        self,
        provider_account: str,
        detail_id: ProviderIdentifier,
    ) -> DetailOwnership | None:
        normalized_detail_id = normalize_identifier(detail_id)
        try:
            async with self._session_factory() as session:
                row = await session.get(
                    ContactDetailOwnership,
                    {
                        "provider_account": provider_account,
                        "detail_id": normalized_detail_id,
                    },
                )
        except SQLAlchemyError:
            raise DetailOwnershipUnavailable("Detail ownership store is unavailable") from None
        if row is None:
            return None
        return self._to_ownership(row)

    async def invalidate_if_matches(self, ownership: DetailOwnership) -> None:
        try:
            async with self._session_factory() as session, session.begin():
                await session.execute(
                    update(ContactDetailOwnership)
                    .where(
                        ContactDetailOwnership.provider_account == ownership.provider_account,
                        ContactDetailOwnership.detail_id == ownership.detail_id,
                        ContactDetailOwnership.contact_id == ownership.contact_id,
                        ContactDetailOwnership.project_id == ownership.project_id,
                        ContactDetailOwnership.active == ownership.active,
                        ContactDetailOwnership.verified_at == ownership.verified_at,
                    )
                    .values(
                        active=False,
                    )
                )
        except SQLAlchemyError:
            raise DetailOwnershipUnavailable("Detail ownership store is unavailable") from None

    async def is_current(self, ownership: DetailOwnership) -> bool:
        current = await self.get(ownership.provider_account, ownership.detail_id)
        return current == ownership

    @staticmethod
    def _to_ownership(row: ContactDetailOwnership) -> DetailOwnership:
        return DetailOwnership(
            provider_account=row.provider_account,
            detail_id=row.detail_id,
            contact_id=row.contact_id,
            project_id=row.project_id,
            active=row.active,
            verified_at=row.verified_at,
        )
