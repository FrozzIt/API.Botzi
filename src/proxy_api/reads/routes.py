from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from proxy_api.auth.service import AuthenticatedSession
from proxy_api.projects.routes import (
    AuthenticatedSessionDependency,
    ProviderAccountDependency,
    ProviderDependency,
    provider_unavailable_error,
)
from proxy_api.provider import (
    ProviderError,
    ProviderObjectNotFound,
    ProviderProtocolError,
)

router = APIRouter()


class ReadAccessDenied(Exception):
    """Neutral denial for a foreign, unknown, or unproven read target."""


def read_not_found_error() -> JSONResponse:
    return JSONResponse(
        {
            "status": "error",
            "errors": [{"code": 404, "message": "Object not found"}],
        }
    )


def request_not_valid_error() -> JSONResponse:
    return JSONResponse(
        {
            "status": "error",
            "errors": [{"code": 422, "message": "Request is not valid"}],
        }
    )


async def get_project_scoped_session(
    request: Request,
    access: AuthenticatedSessionDependency,
) -> AuthenticatedSession:
    requested_projects: list[str] = []
    path_project = request.path_params.get("project_id")
    if path_project is not None:
        requested_projects.append(str(path_project))

    query_projects = request.query_params.getlist("project_id")
    if len(query_projects) > 1:
        raise ReadAccessDenied
    requested_projects.extend(query_projects)

    for raw_project_id in requested_projects:
        try:
            requested_project_id = int(raw_project_id)
        except ValueError:
            raise ReadAccessDenied from None
        if str(requested_project_id) != raw_project_id or requested_project_id != access.project_id:
            raise ReadAccessDenied
    return access


ProjectScopedSessionDependency = Annotated[
    AuthenticatedSession,
    Depends(get_project_scoped_session),
]


def _require_int(value: object) -> int:
    if type(value) is not int:
        raise ProviderProtocolError("Provider response is invalid")
    return value


def _require_identifier(value: object) -> int | str:
    if type(value) is int:
        return value
    if isinstance(value, str) and value and value == value.strip():
        return value
    raise ProviderProtocolError("Provider response is invalid")


def _identifiers_match(first: int | str, second: int | str) -> bool:
    return str(first) == str(second)


def _require_string(value: object) -> str:
    if not isinstance(value, str):
        raise ProviderProtocolError("Provider response is invalid")
    return value


def _validate_project_links(value: object, expected_project_id: int) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"project_id", "last_project_id"} and nested is not None:
                if type(nested) is not int or nested != expected_project_id:
                    raise ReadAccessDenied
            _validate_project_links(nested, expected_project_id)
    elif isinstance(value, list):
        for nested in value:
            _validate_project_links(nested, expected_project_id)


def _copy_optional_string(
    source: dict[str, object],
    target: dict[str, object],
    field: str,
) -> None:
    if field not in source:
        return
    value = source[field]
    if value is not None and not isinstance(value, str):
        raise ProviderProtocolError("Provider response is invalid")
    target[field] = value


def _copy_optional_int(
    source: dict[str, object],
    target: dict[str, object],
    field: str,
) -> None:
    if field not in source:
        return
    value = source[field]
    if value is not None and type(value) is not int:
        raise ProviderProtocolError("Provider response is invalid")
    target[field] = value


def _serialize_contact(
    value: object,
    expected_project_id: int,
    *,
    expected_contact_id: int | str | None = None,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProviderProtocolError("Provider response is invalid")
    _validate_project_links(value, expected_project_id)

    contact_id = _require_identifier(value.get("id"))
    if expected_contact_id is not None and not _identifiers_match(contact_id, expected_contact_id):
        raise ReadAccessDenied
    if _require_int(value.get("project_id")) != expected_project_id:
        raise ReadAccessDenied

    details = value.get("details", [])
    if not isinstance(details, list):
        raise ProviderProtocolError("Provider response is invalid")
    public_details: list[dict[str, object]] = []
    for detail in details:
        if not isinstance(detail, dict):
            raise ProviderProtocolError("Provider response is invalid")
        public_details.append(
            {
                "id": _require_identifier(detail.get("id")),
                "type": _require_string(detail.get("type")),
                "data": _require_string(detail.get("data")),
            }
        )

    fields = value.get("fields", [])
    if not isinstance(fields, list):
        raise ProviderProtocolError("Provider response is invalid")
    public_fields: list[dict[str, object]] = []
    for field in fields:
        if not isinstance(field, dict):
            raise ProviderProtocolError("Provider response is invalid")
        public_field: dict[str, object] = {
            "id": _require_identifier(field.get("id")),
            "type": _require_string(field.get("type")),
            "name": _require_string(field.get("name")),
        }
        if "value" in field:
            field_value = field["value"]
            if field_value is not None and not isinstance(field_value, str | int | float | bool):
                raise ProviderProtocolError("Provider response is invalid")
            public_field["value"] = field_value
        public_fields.append(public_field)

    public_contact: dict[str, object] = {
        "id": contact_id,
        "project_id": expected_project_id,
        "details": public_details,
        "fields": public_fields,
    }
    for field in ("created_at", "name", "site", "profession"):
        _copy_optional_string(value, public_contact, field)
    return public_contact


def _serialize_view(
    value: object,
    expected_project_id: int,
    *,
    expected_view_id: str | None = None,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProviderProtocolError("Provider response is invalid")
    _validate_project_links(value, expected_project_id)
    view_id = _require_int(value.get("id"))
    if _require_int(value.get("project_id")) != expected_project_id:
        raise ReadAccessDenied
    if expected_view_id is not None:
        uuid = value.get("uuid")
        matches_id = expected_view_id == str(view_id)
        matches_uuid = isinstance(uuid, str) and expected_view_id == uuid
        if not matches_id and not matches_uuid:
            raise ReadAccessDenied

    public_view: dict[str, object] = {
        "id": view_id,
        "project_id": expected_project_id,
    }
    for field in (
        "uuid",
        "ym_client_id",
        "ga_client_id",
        "source",
        "campaign",
        "keyword",
        "seo_system",
        "page",
        "referer",
    ):
        _copy_optional_string(value, public_view, field)
    return public_view


def _serialize_task(
    value: object,
    expected_project_id: int,
    *,
    expected_task_id: int | None = None,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProviderProtocolError("Provider response is invalid")
    _validate_project_links(value, expected_project_id)

    task_id = _require_int(value.get("id"))
    if expected_task_id is not None and task_id != expected_task_id:
        raise ReadAccessDenied
    if _require_int(value.get("project_id")) != expected_project_id:
        raise ReadAccessDenied

    public_task: dict[str, object] = {
        "id": task_id,
        "project_id": expected_project_id,
    }
    for field in ("title", "text", "remind_method"):
        _copy_optional_string(value, public_task, field)
    for field in (
        "date_start",
        "date_end",
        "remind_type",
        "visibility",
        "status",
    ):
        _copy_optional_int(value, public_task, field)
    return public_task


def _serialize_lead(
    value: object,
    expected_project_id: int,
    *,
    expected_lead_id: int | None = None,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProviderProtocolError("Provider response is invalid")
    _validate_project_links(value, expected_project_id)

    lead_id = _require_int(value.get("id"))
    if expected_lead_id is not None and lead_id != expected_lead_id:
        raise ReadAccessDenied
    contact_id = _require_identifier(value.get("contact_id"))
    contact = _serialize_contact(
        value.get("contact"),
        expected_project_id,
        expected_contact_id=contact_id,
    )

    public_lead: dict[str, object] = {
        "id": lead_id,
        "contact_id": contact_id,
        "contact": contact,
    }
    for field in ("name", "created_at", "updated_at"):
        _copy_optional_string(value, public_lead, field)
    if "deal" in value:
        if type(value["deal"]) is not bool:
            raise ProviderProtocolError("Provider response is invalid")
        public_lead["deal"] = value["deal"]
    if value.get("view") is not None:
        public_lead["view"] = _serialize_view(value["view"], expected_project_id)
    return public_lead


def _object_result(response: object) -> object:
    if not isinstance(response, dict) or response.get("status") != "success":
        raise ProviderProtocolError("Provider response is invalid")
    return response.get("result")


def _list_result(response: object) -> list[object]:
    if isinstance(response, list):
        return response
    result = _object_result(response)
    if not isinstance(result, list):
        raise ProviderProtocolError("Provider response is invalid")
    return result


def _ensure_provider_account(
    access: AuthenticatedSession,
    provider_account: str,
) -> None:
    if access.provider_account != provider_account:
        raise ProviderError("Provider account is unavailable")


@router.get("/contact/search", include_in_schema=False)
async def contacts(
    request: Request,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
    project_id: int,
    phone: Annotated[list[str] | None, Query()] = None,
    email: Annotated[list[str] | None, Query()] = None,
    max_bot_id: str | None = None,
    max_user_id: str | None = None,
) -> JSONResponse:
    filters = {
        "phone": phone or [],
        "email": email or [],
        "max_bot_id": [max_bot_id] if max_bot_id is not None else [],
        "max_user_id": [max_user_id] if max_user_id is not None else [],
    }
    if not any(filters.values()):
        return request_not_valid_error()
    if any(not item or item != item.strip() for values in filters.values() for item in values):
        return request_not_valid_error()
    if any(len(request.query_params.getlist(key)) > 1 for key in ("max_bot_id", "max_user_id")):
        return request_not_valid_error()

    params: list[tuple[str, str | int]] = [("project_id", project_id)]
    for key, values in filters.items():
        params.extend((key, value) for value in values)
    try:
        _ensure_provider_account(access, provider_account)
        response = await provider.get("/contact/search", params=params)
        result = [_serialize_contact(item, access.project_id) for item in _list_result(response)]
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/contact/{contact_id}", include_in_schema=False)
async def contact(
    contact_id: int,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
) -> JSONResponse:
    try:
        _ensure_provider_account(access, provider_account)
        response = await provider.get(f"/contact/{contact_id}")
        result = _serialize_contact(
            _object_result(response),
            access.project_id,
            expected_contact_id=contact_id,
        )
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/view/{view_id}", include_in_schema=False)
async def view(
    view_id: str,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
) -> JSONResponse:
    if not view_id or view_id != view_id.strip():
        return request_not_valid_error()
    try:
        _ensure_provider_account(access, provider_account)
        response = await provider.get(f"/view/{view_id}")
        result = _serialize_view(
            _object_result(response),
            access.project_id,
            expected_view_id=view_id,
        )
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/task/{task_id}", include_in_schema=False)
async def task(
    task_id: int,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
) -> JSONResponse:
    try:
        _ensure_provider_account(access, provider_account)
        response = await provider.get(f"/task/{task_id}")
        result = _serialize_task(
            _object_result(response),
            access.project_id,
            expected_task_id=task_id,
        )
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/task/{project_id}/list", include_in_schema=False)
async def tasks(
    project_id: int,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> JSONResponse:
    try:
        _ensure_provider_account(access, provider_account)
        response = await provider.get(
            f"/task/{project_id}/list",
            params={"offset": offset, "limit": limit},
        )
        result = [_serialize_task(item, access.project_id) for item in _list_result(response)]
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/lead/{lead_id}", include_in_schema=False)
async def lead(
    lead_id: int,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
) -> JSONResponse:
    try:
        _ensure_provider_account(access, provider_account)
        response = await provider.get(f"/lead/{lead_id}")
        result = _serialize_lead(
            _object_result(response),
            access.project_id,
            expected_lead_id=lead_id,
        )
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/lead/{project_id}/list", include_in_schema=False)
async def leads(
    project_id: int,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> JSONResponse:
    try:
        _ensure_provider_account(access, provider_account)
        response = await provider.get(
            f"/lead/{project_id}/list",
            params={"offset": offset, "limit": limit},
        )
        result = [_serialize_lead(item, access.project_id) for item in _list_result(response)]
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})
