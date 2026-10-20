from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from proxy_api.config import get_settings
from proxy_api.contacts.service import (
    ContactCreateOutcome,
    ContactCreateState,
    ContactMutationWriter,
    ContactWriter,
)
from proxy_api.infrastructure import get_session_factory
from proxy_api.operations import IDEMPOTENCY_KEY_MAX_LENGTH, OperationJournalService
from proxy_api.projects.routes import (
    AuthenticatedSessionDependency,
    ProviderAccountDependency,
    ProviderDependency,
    project_not_found_error,
    provider_unavailable_error,
    validate_project_query,
)
from proxy_api.provider import ProviderError, ProviderObjectNotFound, ProviderProtocolError
from proxy_api.reads.detail_ownership import (
    DetailOwnershipConflict,
    DetailOwnershipStore,
    DetailOwnershipUnavailable,
    normalize_identifier,
)
from proxy_api.reads.routes import (
    DetailOwnershipDependency,
    ProjectScopedSessionDependency,
    ReadAccessDenied,
    _identifiers_match,
    _list_result,
    _object_result,
    _require_identifier,
    _require_string,
    _serialize_lead,
    read_not_found_error,
    verify_contact_detail_parent,
    verify_contact_parent,
    verify_project_field_id,
)

router = APIRouter()

CONFLICT_MESSAGE = "Idempotency key conflicts with another request"
PENDING_MESSAGE = "Operation is still in progress"
UNKNOWN_MESSAGE = "Operation outcome is unknown"
DEFERRED_MESSAGE = "Operation is not available in this release"


class ContactDetailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["email", "phone"]
    data: str = Field(min_length=1, max_length=512)

    @field_validator("data")
    @classmethod
    def reject_blank_data(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("detail data is blank")
        return value


class ContactCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    project_id: int | None = Field(default=None, gt=0)
    name: str = Field(min_length=1, max_length=512)
    details: list[ContactDetailRequest] = Field(min_length=1, max_length=1)

    @field_validator("name")
    @classmethod
    def reject_blank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("contact name is blank")
        return value


class ContactDetailEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    value: str = Field(min_length=1, max_length=512)

    @field_validator("value")
    @classmethod
    def reject_blank_value(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("detail value is blank")
        return value


def get_contact_writer() -> ContactWriter:
    settings = get_settings()
    digest_key = settings.provider_token_encryption_key
    if digest_key is None:
        raise RuntimeError("Operation journal digest key is not configured")
    session_factory = get_session_factory()
    return ContactWriter(
        OperationJournalService(
            session_factory,
            digest_secret=digest_key.get_secret_value().encode("utf-8"),
            pending_lease_seconds=settings.provider_deadline_seconds + 1,
        ),
        DetailOwnershipStore(session_factory),
    )


ContactWriterDependency = Annotated[ContactWriter, Depends(get_contact_writer)]


def get_contact_mutation_writer() -> ContactMutationWriter:
    settings = get_settings()
    digest_key = settings.provider_token_encryption_key
    if digest_key is None:
        raise RuntimeError("Operation journal digest key is not configured")
    return ContactMutationWriter(
        OperationJournalService(
            get_session_factory(),
            digest_secret=digest_key.get_secret_value().encode("utf-8"),
            pending_lease_seconds=settings.provider_deadline_seconds + 1,
        )
    )


ContactMutationWriterDependency = Annotated[
    ContactMutationWriter,
    Depends(get_contact_mutation_writer),
]


def operation_error(
    code: int,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        {
            "status": "error",
            "errors": [{"code": code, "message": message}],
        },
        headers=headers,
    )


def request_invalid_error() -> JSONResponse:
    return operation_error(422, "Request is not valid")


def _idempotency_key(values: list[str] | None) -> str | None:
    if values is None:
        return None
    if len(values) != 1 or not values[0].strip() or len(values[0]) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise ValueError("Idempotency key is invalid")
    return values[0]


def _request_id(request: Request) -> uuid.UUID:
    value = getattr(request.state, "operation_request_id", None)
    if not isinstance(value, uuid.UUID):
        raise RuntimeError("Operation request identifier is unavailable")
    return value


def _response(outcome: ContactCreateOutcome) -> JSONResponse:
    headers = {"X-Request-ID": str(outcome.request_id)}
    if outcome.state in {ContactCreateState.SUCCEEDED, ContactCreateState.REPLAYED}:
        if outcome.result is None:
            return operation_error(520, UNKNOWN_MESSAGE, headers=headers)
        return JSONResponse(
            {"status": "success", "result": outcome.result},
            headers=headers,
        )
    errors = {
        ContactCreateState.CONFLICT: (409, CONFLICT_MESSAGE),
        ContactCreateState.PENDING: (423, PENDING_MESSAGE),
        ContactCreateState.UNKNOWN: (520, UNKNOWN_MESSAGE),
        ContactCreateState.FAILED: (503, "Service unavailable"),
    }
    code, message = errors[outcome.state]
    return operation_error(code, message, headers=headers)


def _mutation_response(
    outcome: ContactCreateOutcome,
    *,
    public_result: dict[str, object] | None,
) -> JSONResponse:
    headers = {"X-Request-ID": str(outcome.request_id)}
    if outcome.state in {ContactCreateState.SUCCEEDED, ContactCreateState.REPLAYED}:
        if outcome.result is None:
            return operation_error(520, UNKNOWN_MESSAGE, headers=headers)
        payload: dict[str, object] = {"status": "success"}
        if public_result is not None:
            payload["result"] = public_result
        return JSONResponse(payload, headers=headers)
    return _response(outcome)


def _closed_operation(request: Request) -> JSONResponse:
    return operation_error(
        503,
        "Service unavailable",
        headers={"X-Request-ID": str(_request_id(request))},
    )


def _validate_detail_write_response(
    response: object,
    *,
    detail_id: str,
    expected_value: str,
) -> dict[str, object]:
    result = _object_result(response)
    if not isinstance(result, dict):
        raise ProviderProtocolError("Provider response is invalid")
    received_id = _require_identifier(result.get("id"))
    if not _identifiers_match(received_id, detail_id):
        raise ProviderProtocolError("Provider response is invalid")
    detail_type = _require_string(result.get("type"))
    if detail_type not in {"email", "phone"} or result.get("data") != expected_value:
        raise ProviderProtocolError("Provider response is invalid")
    return {"id": received_id, "type": detail_type}


def _validate_delete_response(response: object) -> dict[str, object]:
    if not isinstance(response, dict) or response.get("status") != "success":
        raise ProviderProtocolError("Provider response is invalid")
    result = response.get("result")
    if result is not None and result is not True:
        raise ProviderProtocolError("Provider response is invalid")
    return {}


@router.post("/contact", include_in_schema=False)
async def create_contact(
    request: Request,
    payload: ContactCreateRequest,
    access: AuthenticatedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
    writer: ContactWriterDependency,
    idempotency_keys: Annotated[
        list[str] | None,
        Header(alias="Idempotency-Key"),
    ] = None,
) -> JSONResponse:
    try:
        validate_project_query(request, access.project_id)
    except LookupError:
        return project_not_found_error()
    if payload.project_id is not None and payload.project_id != access.project_id:
        return project_not_found_error()
    try:
        idempotency_key = _idempotency_key(idempotency_keys)
    except ValueError:
        return request_invalid_error()
    detail = payload.details[0]
    try:
        outcome = await writer.create(
            access,
            provider,
            request_id=_request_id(request),
            provider_account=provider_account,
            idempotency_key=idempotency_key,
            name=payload.name,
            detail_type=detail.type,
            detail_data=detail.data,
        )
    except ProviderError:
        return provider_unavailable_error()
    return _response(outcome)


@router.put("/contact/{contact_id}", include_in_schema=False)
async def edit_contact_closed(
    request: Request,
    contact_id: str,
    access: AuthenticatedSessionDependency,
) -> JSONResponse:
    try:
        validate_project_query(request, access.project_id)
        normalize_identifier(contact_id)
    except (DetailOwnershipConflict, LookupError):
        return read_not_found_error()
    return _closed_operation(request)


@router.delete("/contact/{contact_id}", include_in_schema=False)
async def delete_contact_closed(
    request: Request,
    contact_id: str,
    access: AuthenticatedSessionDependency,
) -> JSONResponse:
    try:
        validate_project_query(request, access.project_id)
        normalize_identifier(contact_id)
    except (DetailOwnershipConflict, LookupError):
        return read_not_found_error()
    return _closed_operation(request)


@router.get("/contact/details/{detail_id}", include_in_schema=False)
async def contact_detail(
    detail_id: str,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
    ownership_store: DetailOwnershipDependency,
) -> JSONResponse:
    try:
        normalized_id = normalize_identifier(detail_id)
        ownership = await verify_contact_detail_parent(
            normalized_id,
            access,
            provider,
            provider_account,
            ownership_store,
        )
        response = await provider.get(f"/contact/details/{normalized_id}")
        result = _object_result(response)
        if not isinstance(result, dict):
            raise ProviderProtocolError("Provider response is invalid")
        received_id = _require_identifier(result.get("id"))
        if not _identifiers_match(received_id, normalized_id):
            raise ReadAccessDenied
        public_result = {
            "id": received_id,
            "type": _require_string(result.get("type")),
            "data": _require_string(result.get("data")),
        }
    except ProviderObjectNotFound:
        if "ownership" in locals():
            await ownership_store.invalidate_if_matches(ownership)
        return read_not_found_error()
    except (DetailOwnershipConflict, ReadAccessDenied):
        return read_not_found_error()
    except (ProviderError, DetailOwnershipUnavailable):
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": public_result})


@router.put("/contact/details/{detail_id}", include_in_schema=False)
async def edit_contact_detail(
    request: Request,
    detail_id: str,
    payload: ContactDetailEditRequest,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
    writer: ContactMutationWriterDependency,
    ownership_store: DetailOwnershipDependency,
    idempotency_keys: Annotated[list[str] | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    try:
        key = _idempotency_key(idempotency_keys)
        normalized_id = normalize_identifier(detail_id)
        await verify_contact_detail_parent(
            normalized_id,
            access,
            provider,
            provider_account,
            ownership_store,
        )
    except ValueError:
        return request_invalid_error()
    except DetailOwnershipConflict:
        return read_not_found_error()
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except (ProviderError, DetailOwnershipUnavailable):
        return provider_unavailable_error()

    try:
        outcome = await writer.mutate(
            access,
            provider,
            request_id=_request_id(request),
            provider_account=provider_account,
            operation="C07",
            idempotency_key=key,
            method="PUT",
            path=f"/contact/details/{normalized_id}",
            json_body={"value": payload.value},
            validate_response=lambda response: _validate_detail_write_response(
                response,
                detail_id=normalized_id,
                expected_value=payload.value,
            ),
        )
    except ProviderError:
        return provider_unavailable_error()
    if outcome.result is None:
        return _mutation_response(outcome, public_result=None)
    public_result = {
        "id": outcome.result.get("id"),
        "type": outcome.result.get("type"),
        "data": payload.value,
    }
    return _mutation_response(outcome, public_result=public_result)


@router.delete("/contact/details/{detail_id}", include_in_schema=False)
async def delete_contact_detail_deferred(
    request: Request,
    detail_id: str,
    access: AuthenticatedSessionDependency,
) -> JSONResponse:
    try:
        validate_project_query(request, access.project_id)
    except LookupError:
        return read_not_found_error()
    return operation_error(501, DEFERRED_MESSAGE)


@router.get("/contact/{contact_id}/leads", include_in_schema=False)
async def contact_leads(
    contact_id: str,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
    ownership_store: DetailOwnershipDependency,
) -> JSONResponse:
    try:
        normalized_contact_id = normalize_identifier(contact_id)
        await verify_contact_parent(
            normalized_contact_id,
            access,
            provider,
            provider_account,
            ownership_store,
        )
        listed = _list_result(await provider.get(f"/contact/{normalized_contact_id}/leads"))
        lead_ids: list[int] = []
        for item in listed:
            if not isinstance(item, dict):
                raise ProviderProtocolError("Provider response is invalid")
            lead_id = item.get("id")
            if type(lead_id) is not int or not _identifiers_match(
                _require_identifier(item.get("contact_id")),
                normalized_contact_id,
            ):
                raise ReadAccessDenied
            lead_ids.append(lead_id)
        if len(lead_ids) != len(set(lead_ids)):
            raise ProviderProtocolError("Provider response is invalid")
        result = []
        for lead_id in lead_ids:
            raw_lead = _object_result(await provider.get(f"/lead/{lead_id}"))
            result.append(
                _serialize_lead(
                    raw_lead,
                    access.project_id,
                    expected_lead_id=lead_id,
                    expected_contact_id=normalized_contact_id,
                )
            )
    except (DetailOwnershipConflict, ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except (ProviderError, DetailOwnershipUnavailable):
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/contact/{contact_id}/field/{field_id}", include_in_schema=False)
async def contact_field(
    contact_id: str,
    field_id: str,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
    ownership_store: DetailOwnershipDependency,
) -> JSONResponse:
    try:
        normalized_contact_id = normalize_identifier(contact_id)
        normalized_field_id = normalize_identifier(field_id)
        await verify_contact_parent(
            normalized_contact_id,
            access,
            provider,
            provider_account,
            ownership_store,
        )
        catalog_field = await verify_project_field_id(
            normalized_field_id,
            access,
            provider,
            provider_account,
        )
        result = _object_result(
            await provider.get(f"/contact/{normalized_contact_id}/field/{normalized_field_id}")
        )
        if not isinstance(result, dict) or not _identifiers_match(
            _require_identifier(result.get("id")),
            normalized_field_id,
        ):
            raise ReadAccessDenied
        if result.get("name") != catalog_field.get("name") or result.get(
            "type"
        ) != catalog_field.get("type"):
            raise ReadAccessDenied
        public_result = {
            "id": _require_identifier(result.get("id")),
            "type": _require_string(result.get("type")),
            "name": _require_string(result.get("name")),
            "value": result.get("value"),
        }
        if public_result["value"] is not None and not isinstance(
            public_result["value"],
            str | int | float | bool,
        ):
            raise ProviderProtocolError("Provider response is invalid")
    except (DetailOwnershipConflict, ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except (ProviderError, DetailOwnershipUnavailable):
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": public_result})


@router.put("/contact/{contact_id}/field/{field_id}", include_in_schema=False)
async def edit_contact_field_closed(
    request: Request,
    contact_id: str,
    field_id: str,
    access: AuthenticatedSessionDependency,
) -> JSONResponse:
    try:
        validate_project_query(request, access.project_id)
        normalize_identifier(contact_id)
        normalize_identifier(field_id)
    except (DetailOwnershipConflict, LookupError):
        return read_not_found_error()
    return _closed_operation(request)


@router.delete("/contact/{contact_id}/field/{field_id}", include_in_schema=False)
async def clear_contact_field(
    request: Request,
    contact_id: str,
    field_id: str,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
    writer: ContactMutationWriterDependency,
    ownership_store: DetailOwnershipDependency,
    idempotency_keys: Annotated[list[str] | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    try:
        key = _idempotency_key(idempotency_keys)
        normalized_contact_id = normalize_identifier(contact_id)
        normalized_field_id = normalize_identifier(field_id)
        await verify_contact_parent(
            normalized_contact_id,
            access,
            provider,
            provider_account,
            ownership_store,
        )
        await verify_project_field_id(
            normalized_field_id,
            access,
            provider,
            provider_account,
        )
    except ValueError:
        return request_invalid_error()
    except DetailOwnershipConflict:
        return read_not_found_error()
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except (ProviderError, DetailOwnershipUnavailable):
        return provider_unavailable_error()
    try:
        outcome = await writer.mutate(
            access,
            provider,
            request_id=_request_id(request),
            provider_account=provider_account,
            operation="C12",
            idempotency_key=key,
            method="DELETE",
            path=f"/contact/{normalized_contact_id}/field/{normalized_field_id}",
            json_body=None,
            validate_response=_validate_delete_response,
        )
    except ProviderError:
        return provider_unavailable_error()
    return _mutation_response(outcome, public_result=None)
