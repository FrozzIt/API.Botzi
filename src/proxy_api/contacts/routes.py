from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from proxy_api.config import get_settings
from proxy_api.contacts.service import ContactCreateOutcome, ContactCreateState, ContactWriter
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
from proxy_api.provider import ProviderError
from proxy_api.reads.detail_ownership import DetailOwnershipStore

router = APIRouter()

CONFLICT_MESSAGE = "Idempotency key conflicts with another request"
PENDING_MESSAGE = "Operation is still in progress"
UNKNOWN_MESSAGE = "Operation outcome is unknown"


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
    if idempotency_keys is not None:
        if (
            len(idempotency_keys) != 1
            or not idempotency_keys[0].strip()
            or len(idempotency_keys[0]) > IDEMPOTENCY_KEY_MAX_LENGTH
        ):
            return request_invalid_error()
        idempotency_key = idempotency_keys[0]
    else:
        idempotency_key = None
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
