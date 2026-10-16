from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.exc import SQLAlchemyError

from proxy_api.auth.service import AuthenticatedSession
from proxy_api.operations import (
    OperationJournalService,
    OperationState,
    OperationTransitionRejected,
)
from proxy_api.projects.routes import AuthorizedProvider, ClientAuthenticationError
from proxy_api.provider import ProviderError, ProviderProtocolError
from proxy_api.reads.detail_ownership import (
    DetailOwnershipConflict,
    DetailOwnershipStore,
    DetailOwnershipUnavailable,
    ProviderIdentifier,
    VerifiedContact,
)

CONTACT_CREATE_OPERATION = "C02"


class ContactCreateState(StrEnum):
    SUCCEEDED = "succeeded"
    REPLAYED = "replayed"
    FAILED = "failed"
    PENDING = "pending"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class ContactCreateOutcome:
    request_id: uuid.UUID
    state: ContactCreateState
    result: dict[str, object] | None = None


class ContactWriter:
    def __init__(
        self,
        journal: OperationJournalService,
        ownership_store: DetailOwnershipStore,
    ) -> None:
        self._journal = journal
        self._ownership_store = ownership_store

    async def create(
        self,
        access: AuthenticatedSession,
        provider: AuthorizedProvider,
        *,
        request_id: uuid.UUID,
        provider_account: str,
        idempotency_key: str | None,
        name: str,
        detail_type: str,
        detail_data: str,
    ) -> ContactCreateOutcome:
        if access.provider_account != provider_account:
            raise ProviderError("Provider account is unavailable")

        upstream_payload: dict[str, object] = {
            "project_id": access.project_id,
            "name": name,
            "details": [{"type": detail_type, "data": detail_data}],
        }
        try:
            begun = await self._journal.begin(
                access,
                request_id=request_id,
                operation=CONTACT_CREATE_OPERATION,
                idempotency_key=idempotency_key,
                payload=upstream_payload,
            )
        except (SQLAlchemyError, RuntimeError):
            return ContactCreateOutcome(request_id, ContactCreateState.FAILED)
        record = begun.record
        if not begun.execute:
            if record.fingerprint != self._journal.payload_fingerprint(upstream_payload):
                return ContactCreateOutcome(record.request_id, ContactCreateState.CONFLICT)
            if record.state is OperationState.SUCCEEDED:
                if record.safe_result is None:
                    return ContactCreateOutcome(record.request_id, ContactCreateState.UNKNOWN)
                await provider.recheck_access()
                return ContactCreateOutcome(
                    record.request_id,
                    ContactCreateState.REPLAYED,
                    self._public_result(
                        record.safe_result,
                        expected_project_id=access.project_id,
                        name=name,
                        detail_type=detail_type,
                        detail_data=detail_data,
                    ),
                )
            return ContactCreateOutcome(
                record.request_id,
                ContactCreateState(record.state.value),
            )

        dispatch_started = False

        async def mark_dispatch_started() -> None:
            nonlocal dispatch_started
            await self._journal.mark_dispatch_started(record.request_id)
            dispatch_started = True

        try:
            response = await provider.post_once(
                "/contact",
                json_body=upstream_payload,
                before_send=mark_dispatch_started,
            )
            safe_result, verified_contact = self._validate_response(
                response,
                access.project_id,
                expected_name=name,
                expected_detail_type=detail_type,
                expected_detail_data=detail_data,
            )
            await self._ownership_store.sync_verified_contacts(
                access.provider_account,
                [verified_contact],
            )
            await self._journal.mark_succeeded(record.request_id, safe_result)
        except ClientAuthenticationError:
            if not dispatch_started:
                await self._mark_failed(record.request_id)
            raise
        except OperationTransitionRejected:
            return ContactCreateOutcome(record.request_id, ContactCreateState.UNKNOWN)
        except (ProviderError, ProviderProtocolError, DetailOwnershipConflict):
            if not dispatch_started:
                await self._mark_failed(record.request_id)
                return ContactCreateOutcome(record.request_id, ContactCreateState.FAILED)
            return ContactCreateOutcome(record.request_id, ContactCreateState.UNKNOWN)
        except (SQLAlchemyError, DetailOwnershipUnavailable, RuntimeError):
            if not dispatch_started:
                await self._mark_failed(record.request_id)
                return ContactCreateOutcome(record.request_id, ContactCreateState.FAILED)
            return ContactCreateOutcome(record.request_id, ContactCreateState.UNKNOWN)

        return ContactCreateOutcome(
            record.request_id,
            ContactCreateState.SUCCEEDED,
            self._public_result(
                safe_result,
                expected_project_id=access.project_id,
                name=name,
                detail_type=detail_type,
                detail_data=detail_data,
            ),
        )

    async def _mark_failed(self, request_id: uuid.UUID) -> None:
        try:
            await self._journal.mark_failed_before_send(request_id)
        except (SQLAlchemyError, RuntimeError):
            pass

    @staticmethod
    def _validate_response(
        response: object,
        expected_project_id: int,
        *,
        expected_name: str,
        expected_detail_type: str,
        expected_detail_data: str,
    ) -> tuple[dict[str, object], VerifiedContact]:
        if not isinstance(response, dict) or response.get("status") != "success":
            raise ProviderProtocolError("Provider response is invalid")
        result = response.get("result")
        if not isinstance(result, dict):
            raise ProviderProtocolError("Provider response is invalid")
        ContactWriter._validate_project_links(result, expected_project_id)
        if result.get("project_id") != expected_project_id or result.get("name") != expected_name:
            raise ProviderProtocolError("Provider response is invalid")
        contact_id = ContactWriter._identifier(result.get("id"))
        details = result.get("details")
        if not isinstance(details, list) or len(details) != 1:
            raise ProviderProtocolError("Provider response is invalid")
        detail = details[0]
        if not isinstance(detail, dict):
            raise ProviderProtocolError("Provider response is invalid")
        detail_id = ContactWriter._identifier(detail.get("id"))
        if detail.get("type") != expected_detail_type or detail.get("data") != expected_detail_data:
            raise ProviderProtocolError("Provider response is invalid")
        safe_result: dict[str, object] = {
            "id": contact_id,
            "project_id": expected_project_id,
            "details": [{"id": detail_id, "type": expected_detail_type}],
        }
        return safe_result, VerifiedContact(
            contact_id=contact_id,
            project_id=expected_project_id,
            detail_ids=frozenset({detail_id}),
            details_complete=True,
        )

    @staticmethod
    def _validate_project_links(value: object, expected_project_id: int) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in {"project_id", "last_project_id"} and nested is not None:
                    if type(nested) is not int or nested != expected_project_id:
                        raise ProviderProtocolError("Provider response is invalid")
                ContactWriter._validate_project_links(nested, expected_project_id)
        elif isinstance(value, list):
            for nested in value:
                ContactWriter._validate_project_links(nested, expected_project_id)

    @staticmethod
    def _identifier(value: object) -> ProviderIdentifier:
        if type(value) is int:
            return value
        if isinstance(value, str) and value and value == value.strip():
            return value
        raise ProviderProtocolError("Provider response is invalid")

    @staticmethod
    def _public_result(
        safe_result: dict[str, object],
        *,
        expected_project_id: int,
        name: str,
        detail_type: str,
        detail_data: str,
    ) -> dict[str, object]:
        contact_id = ContactWriter._identifier(safe_result.get("id"))
        if safe_result.get("project_id") != expected_project_id:
            raise ProviderProtocolError("Stored operation result is invalid")
        details = safe_result.get("details")
        if not isinstance(details, list) or len(details) != 1:
            raise ProviderProtocolError("Stored operation result is invalid")
        detail = details[0]
        if not isinstance(detail, dict) or detail.get("type") != detail_type:
            raise ProviderProtocolError("Stored operation result is invalid")
        detail_id = ContactWriter._identifier(detail.get("id"))
        return {
            "id": contact_id,
            "project_id": expected_project_id,
            "name": name,
            "details": [
                {
                    "id": detail_id,
                    "type": detail_type,
                    "data": detail_data,
                }
            ],
        }
